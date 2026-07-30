"""E2E tests for DB modality — trace-db-session-007 through Parser→Analyzer→Optimizer.

Proves that DB traces flow through the SAME universal code paths as API traces:
no modality-specific branching anywhere in the pipeline. This is the acid test
for the universal trace surface.
"""

from __future__ import annotations

import json
import sys
from pathlib import Path

import pytest

sys.path.insert(0, str(Path(__file__).parent.parent / "src"))
# Also add trace-sdk for DB profile imports in feedback.py
sys.path.insert(0, str(Path(__file__).parent.parent.parent / "trace-sdk" / "src"))

from ignite_parser.parser import parse_trace
from ignite_parser.analyzer import analyze
from ignite_parser.optimizer import optimize
from ignite_parser.planner import plan, ActionKind
from ignite_parser.executor import execute, ActionStatus
from ignite_parser.feedback import lookup_correction, capture_feedback
from ignite_parser.models import (
    CorrectionAction,
    Modality,
    RequestIntent,
    ResponseOutcome,
    SpanStructure,
)


FIXTURE_PATH = Path(__file__).parent / "fixtures" / "trace_db_session_007.json"


@pytest.fixture
def db_trace_data():
    return json.loads(FIXTURE_PATH.read_text(encoding="utf-8"))


@pytest.fixture
def parsed_trace(db_trace_data):
    result = parse_trace(db_trace_data)
    assert result.ok, f"Parse errors: {[e.message for e in result.errors]}"
    return result.traces[0]


@pytest.fixture
def analysis_result(parsed_trace):
    return analyze([parsed_trace])


@pytest.fixture
def optimized_result(analysis_result):
    return optimize([analysis_result])


# --- Parser E2E ---


class TestDbParserE2E:
    def test_parses_all_7_spans(self, parsed_trace):
        assert len(parsed_trace.spans) == 7

    def test_all_spans_modality_db(self, parsed_trace):
        for span in parsed_trace.spans:
            assert span.modality == Modality.DB

    def test_modality_ext_preserved(self, parsed_trace):
        for span in parsed_trace.spans:
            assert span.modality_ext is not None
            assert span.modality_ext["type"] == "db_ext"
            assert "db_system" in span.modality_ext

    def test_db_ext_risk_level(self, parsed_trace):
        """P1 field: risk_level flows through Parser."""
        risk_levels = {s.span_id: s.modality_ext.get("risk_level") for s in parsed_trace.spans}
        assert risk_levels["db-span-001"] == "low"    # SELECT
        assert risk_levels["db-span-004"] == "medium"  # UPDATE (deadlocked)
        assert risk_levels["db-span-007"] == "high"    # CREATE INDEX (DDL)

    def test_request_intent_classification(self, parsed_trace):
        """DB operations map to intent deterministically: SELECT→query, INSERT/UPDATE/DELETE→mutation."""
        intents = {s.span_id: s.request_intent for s in parsed_trace.spans}
        assert intents["db-span-001"] == RequestIntent.QUERY     # SELECT
        assert intents["db-span-002"] == RequestIntent.QUERY     # SELECT join
        assert intents["db-span-003"] == RequestIntent.MUTATION  # INSERT
        assert intents["db-span-004"] == RequestIntent.MUTATION  # UPDATE (error)
        assert intents["db-span-005"] == RequestIntent.MUTATION  # UPDATE (retry)
        assert intents["db-span-006"] == RequestIntent.QUERY     # EXPLAIN
        assert intents["db-span-007"] == RequestIntent.MUTATION  # CREATE INDEX

    def test_error_span_outcome(self, parsed_trace):
        """Deadlock span has error outcome; retry span has success."""
        span4 = next(s for s in parsed_trace.spans if s.span_id == "db-span-004")
        span5 = next(s for s in parsed_trace.spans if s.span_id == "db-span-005")
        assert span4.response_outcome == ResponseOutcome.ERROR
        assert span5.response_outcome == ResponseOutcome.SUCCESS

    def test_divergence_fields(self, parsed_trace):
        """ErrP divergence on the deadlock span."""
        span4 = next(s for s in parsed_trace.spans if s.span_id == "db-span-004")
        assert span4.expected_outcome is not None
        assert span4.actual_outcome is not None
        assert span4.divergence_score == 0.8

    def test_span_structure_transactional(self, parsed_trace):
        """All DB spans use transactional structure."""
        for span in parsed_trace.spans:
            assert span.span_structure == SpanStructure.TRANSACTIONAL

    def test_contains_contexts_cross_modality(self, parsed_trace):
        """First span has a cross-modality context linking to API."""
        span1 = parsed_trace.spans[0]
        assert len(span1.contains_contexts) == 1
        ctx = span1.contains_contexts[0]
        assert ctx.modality == "api"
        assert ctx.relationship == "reads_from"

    def test_session_intent(self, parsed_trace):
        assert parsed_trace.session_intent is not None
        assert parsed_trace.session_intent.value == "validation"

    def test_findings_parsed(self, parsed_trace):
        assert len(parsed_trace.findings) == 2

    def test_no_parse_errors(self, db_trace_data):
        result = parse_trace(db_trace_data)
        assert result.ok
        assert result.error_count == 0


# --- Analyzer E2E ---


class TestDbAnalyzerE2E:
    def test_modality_profile_db(self, analysis_result):
        """Analyzer extracts DB modality profile without modality-specific code."""
        assert "db" in analysis_result.modality_profiles
        profile = analysis_result.modality_profiles["db"]
        assert profile.span_count == 7
        assert profile.total_duration_ms == 15 + 45 + 8 + 120 + 25 + 3 + 350

    def test_success_and_error_rates(self, analysis_result):
        profile = analysis_result.modality_profiles["db"]
        # 6 success, 1 error out of 7
        assert profile.success_rate == pytest.approx(6 / 7, abs=0.01)
        assert profile.error_rate == pytest.approx(1 / 7, abs=0.01)

    def test_unique_targets(self, analysis_result):
        profile = analysis_result.modality_profiles["db"]
        expected_targets = {
            "SELECT accounts", "SELECT transactions", "INSERT audit_log",
            "UPDATE accounts", "EXPLAIN SELECT transactions",
            "CREATE INDEX transactions",
        }
        assert profile.unique_targets == expected_targets

    def test_no_api_modality_profile(self, analysis_result):
        """Pure DB trace should NOT create an API profile."""
        assert "api" not in analysis_result.modality_profiles

    def test_endpoint_catalog_universal(self, analysis_result):
        """Endpoint catalog works for DB targets with same code as API."""
        assert len(analysis_result.endpoints) > 0
        # UPDATE accounts appears twice (deadlock + retry)
        update_key = "UPDATE accounts"
        assert update_key in analysis_result.endpoints
        assert analysis_result.endpoints[update_key].hit_count == 2

    def test_dependency_graph_built(self, analysis_result):
        graph = analysis_result.graph
        assert len(graph.nodes) >= 7  # at least 7 spans
        assert graph.edge_count > 0

    def test_finding_clusters(self, analysis_result):
        assert len(analysis_result.finding_clusters) == 2

    def test_coverage_modalities(self, analysis_result):
        assert "db" in analysis_result.coverage.modalities_seen
        assert "api" not in analysis_result.coverage.modalities_seen

    def test_open_questions_collected(self, analysis_result):
        assert len(analysis_result.coverage.open_questions) > 0


# --- Optimizer E2E ---


class TestDbOptimizerE2E:
    def test_merged_findings(self, optimized_result):
        assert optimized_result.finding_count == 2

    def test_merged_endpoints(self, optimized_result):
        assert len(optimized_result.merged_endpoints) > 0

    def test_merged_graph(self, optimized_result):
        assert len(optimized_result.merged_graph.nodes) >= 7

    def test_coverage_preserved(self, optimized_result):
        assert optimized_result.coverage.total_spans == 7
        assert optimized_result.coverage.total_traces == 1

    def test_source_analysis_count(self, optimized_result):
        assert optimized_result.source_analysis_count == 1


# --- Universal pipeline (no modality branching) ---


class TestUniversalPipeline:
    """The critical proof: DB traces use the EXACT same code paths as API.

    We verify this by running the full pipeline and checking that results
    match the structure API traces produce — no special cases needed.
    """

    def test_same_analysis_structure_as_api(self, db_trace_data):
        """Parse a DB trace and an API trace — both produce the same AnalysisResult shape."""
        # DB trace
        db_result = parse_trace(db_trace_data)
        assert db_result.ok
        db_analysis = analyze(db_result.traces)

        # Minimal API trace for comparison
        api_data = {
            "schema_version": "0.2",
            "trace_id": "trace-api-cmp",
            "agent_id": "ApiExplorer",
            "agent_role": "explorer",
            "system": "ignite",
            "status": "completed",
            "started_at": "2026-07-30T00:00:00Z",
            "objective": "Compare structure",
            "spans": [{
                "span_id": "api-span-001",
                "kind": "api_call",
                "started_at": "2026-07-30T00:00:00Z",
                "duration_ms": 100,
                "modality": "api",
                "interaction": {"target": "POST /test"},
                "observation": {
                    "what_happened": "Called API",
                    "what_learned": "API works",
                    "confidence": "high",
                },
            }],
            "findings": [],
        }
        api_result = parse_trace(api_data)
        assert api_result.ok
        api_analysis = analyze(api_result.traces)

        # Both have the same fields populated
        assert type(db_analysis) is type(api_analysis)
        assert hasattr(db_analysis, "endpoints")
        assert hasattr(db_analysis, "graph")
        assert hasattr(db_analysis, "finding_clusters")
        assert hasattr(db_analysis, "coverage")
        assert hasattr(db_analysis, "modality_profiles")

    def test_optimizer_handles_mixed_modalities(self, db_trace_data):
        """Optimizer merges DB and API analyses without modality branching."""
        db_result = parse_trace(db_trace_data)
        db_analysis = analyze(db_result.traces)

        api_data = {
            "schema_version": "0.2",
            "trace_id": "trace-api-mix",
            "agent_id": "ApiExplorer",
            "agent_role": "explorer",
            "system": "ignite",
            "status": "completed",
            "started_at": "2026-07-30T00:00:00Z",
            "objective": "Mixed modality test",
            "spans": [{
                "span_id": "api-span-mix",
                "kind": "api_call",
                "started_at": "2026-07-30T00:00:00Z",
                "duration_ms": 200,
                "modality": "api",
                "interaction": {"target": "POST /transactions/get"},
                "observation": {
                    "what_happened": "Called Plaid API",
                    "what_learned": "Got transactions",
                    "confidence": "high",
                },
            }],
            "findings": [],
        }
        api_result = parse_trace(api_data)
        api_analysis = analyze(api_result.traces)

        # Optimize both together
        opt = optimize([db_analysis, api_analysis])
        assert opt.source_analysis_count == 2
        assert opt.coverage.total_spans == 8  # 7 DB + 1 API
        assert opt.coverage.total_traces == 2


# --- Planner E2E ---


class TestDbPlannerE2E:
    def test_plan_generated_from_db_findings(self, optimized_result):
        """Planner produces actions from DB findings using same code as API."""
        execution_plan = plan(optimized_result)
        assert execution_plan.action_count > 0

    def test_error_handler_from_deadlock_finding(self, optimized_result):
        """Deadlock error_pattern finding produces a GENERATE_HANDLER action."""
        execution_plan = plan(optimized_result)
        handlers = execution_plan.actions_by_kind(ActionKind.GENERATE_HANDLER)
        assert len(handlers) >= 1
        # The deadlock finding should be in the handler description
        handler_desc = " ".join(h.description for h in handlers)
        assert "error" in handler_desc.lower() or "pattern" in handler_desc.lower()

    def test_plan_has_source_counts(self, optimized_result):
        execution_plan = plan(optimized_result)
        assert execution_plan.source_finding_count == optimized_result.finding_count


# --- Executor E2E ---


class TestDbExecutorE2E:
    def test_executor_runs_plan(self, optimized_result, tmp_path):
        """Executor handles DB-derived plan actions without modality branching."""
        execution_plan = plan(optimized_result)
        result = execute(execution_plan, optimized_result, output_dir=tmp_path)
        assert result.total == execution_plan.action_count
        assert result.total > 0

    def test_executor_produces_artifacts(self, optimized_result, tmp_path):
        """Executor generates files from DB trace intelligence."""
        execution_plan = plan(optimized_result)
        result = execute(execution_plan, optimized_result, output_dir=tmp_path)
        # At least some actions should succeed and produce files
        succeeded = result.by_status(ActionStatus.SUCCESS)
        generated_files = []
        for ar in succeeded:
            generated_files.extend(ar.generated_files)
        assert len(generated_files) > 0


# --- Feedback E2E ---


class TestDbFeedbackE2E:
    def test_feedback_captures_db_execution(self, optimized_result, tmp_path):
        """Feedback writer creates trace from DB execution — closes the loop."""
        execution_plan = plan(optimized_result)
        result = execute(execution_plan, optimized_result, output_dir=tmp_path)
        feedback_path = capture_feedback(execution_plan, result, output_dir=tmp_path)
        assert feedback_path is not None
        assert feedback_path.exists()

    def test_feedback_trace_parseable(self, optimized_result, tmp_path):
        """Feedback trace re-enters Parser without errors — loop closes."""
        execution_plan = plan(optimized_result)
        result = execute(execution_plan, optimized_result, output_dir=tmp_path)
        feedback_path = capture_feedback(execution_plan, result, output_dir=tmp_path)
        assert feedback_path is not None

        feedback_data = json.loads(feedback_path.read_text(encoding="utf-8"))
        re_parsed = parse_trace(feedback_data)
        assert re_parsed.ok, f"Feedback re-parse errors: {[e.message for e in re_parsed.errors]}"


# --- DB ErrP integration ---


class TestDbErrPIntegration:
    """Verify DB error codes flow through lookup_correction via the DB profile."""

    def test_deadlock_correction_backoff_retry(self):
        """Deadlock (mutation + error) should get backoff_retry via DB profile override."""
        correction = lookup_correction("mutation", "error", error_code="deadlock_detected")
        assert correction == CorrectionAction.BACKOFF_RETRY

    def test_unique_violation_correction_escalate(self):
        """Unique violation should escalate, not retry."""
        correction = lookup_correction("mutation", "error", error_code="unique_violation")
        assert correction == CorrectionAction.ESCALATE_HUMAN

    def test_connection_timeout_backoff(self):
        correction = lookup_correction("query", "error", error_code="connection_timeout")
        assert correction == CorrectionAction.BACKOFF_RETRY

    def test_disk_full_log_escalate(self):
        correction = lookup_correction("mutation", "error", error_code="disk_full")
        assert correction == CorrectionAction.LOG_ESCALATE

    def test_permission_denied_escalate(self):
        correction = lookup_correction("query", "error", error_code="permission_denied")
        assert correction == CorrectionAction.ESCALATE_HUMAN

    def test_unknown_db_error_falls_through_to_table(self):
        """Unknown error code falls through to the intent-class table."""
        correction = lookup_correction("query", "error", error_code="some_unknown_db_error")
        # Falls through to (query, error) -> BACKOFF_RETRY from the table
        assert correction == CorrectionAction.BACKOFF_RETRY

    def test_success_still_returns_none(self):
        correction = lookup_correction("query", "success", error_code="deadlock_detected")
        assert correction is None


# --- Full E2E pipeline (acid test) ---


class TestFullDbPipeline:
    """The acid test: DB trace flows through ALL 6 stages using the SAME code paths as API."""

    def test_full_pipeline_e2e(self, db_trace_data, tmp_path):
        """Parse → Analyze → Optimize → Plan → Execute → Feedback, all from a DB trace."""
        # Stage 1: Parser
        parsed = parse_trace(db_trace_data)
        assert parsed.ok
        assert all(s.modality == Modality.DB for s in parsed.traces[0].spans)

        # Stage 2: Analyzer
        analysis = analyze(parsed.traces)
        assert "db" in analysis.modality_profiles
        assert analysis.modality_profiles["db"].span_count == 7

        # Stage 3: Optimizer
        optimized = optimize([analysis])
        assert optimized.finding_count == 2
        assert optimized.coverage.total_spans == 7

        # Stage 4: Planner
        execution_plan = plan(optimized)
        assert execution_plan.action_count > 0

        # Stage 5: Executor
        result = execute(execution_plan, optimized, output_dir=tmp_path)
        assert result.total > 0
        assert result.succeeded > 0

        # Stage 6: Feedback
        feedback_path = capture_feedback(execution_plan, result, output_dir=tmp_path)
        assert feedback_path is not None

        # Close the loop: feedback trace re-enters Parser
        feedback_data = json.loads(feedback_path.read_text(encoding="utf-8"))
        re_parsed = parse_trace(feedback_data)
        assert re_parsed.ok
