"""E2E tests for Web modality — trace-web-session-008 through Parser→Analyzer→Optimizer.

Proves that Web traces flow through the SAME universal code paths as API and DB.
Additional coverage: privacy masking, delta compression, cross-modality chains.
"""

from __future__ import annotations

import json
import sys
from pathlib import Path

import pytest

sys.path.insert(0, str(Path(__file__).parent.parent / "src"))
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
    DeltaFromPrior,
    SignalClass,
)


FIXTURE_PATH = Path(__file__).parent / "fixtures" / "trace_web_session_008.json"


@pytest.fixture
def web_trace_data():
    return json.loads(FIXTURE_PATH.read_text(encoding="utf-8"))


@pytest.fixture
def parsed_trace(web_trace_data):
    result = parse_trace(web_trace_data)
    assert result.ok, f"Parse errors: {[e.message for e in result.errors]}"
    return result.traces[0]


@pytest.fixture
def analysis_result(parsed_trace):
    return analyze([parsed_trace])


@pytest.fixture
def optimized_result(analysis_result):
    return optimize([analysis_result])


# --- Parser E2E ---


class TestWebParserE2E:
    def test_parses_all_7_spans(self, parsed_trace):
        assert len(parsed_trace.spans) == 7

    def test_all_spans_modality_web(self, parsed_trace):
        for span in parsed_trace.spans:
            assert span.modality == Modality.WEB

    def test_modality_ext_preserved(self, parsed_trace):
        for span in parsed_trace.spans:
            assert span.modality_ext is not None
            assert span.modality_ext["type"] == "web_ext"

    def test_web_ext_new_fields(self, parsed_trace):
        """P0 fields: element_name, input_value, snapshot_hash, delta_from."""
        span2 = next(s for s in parsed_trace.spans if s.span_id == "web-span-002")
        ext = span2.modality_ext
        assert ext["element_name"] == "username"
        assert ext["input_value"] == "test@example.com"
        assert ext["input_type"] == "text"
        assert ext["snapshot_hash"] == "axtree-hash-002"
        assert ext["delta_from"] == "web-span-001"

    def test_privacy_masking_password(self, parsed_trace):
        """P0 privacy: password input_value is '***'."""
        span3 = next(s for s in parsed_trace.spans if s.span_id == "web-span-003")
        assert span3.modality_ext["input_value"] == "***"
        assert span3.modality_ext["input_type"] == "password"
        assert span3.modality_ext["element_name"] == "password"

    def test_intent_classification(self, parsed_trace):
        """Web intent: link=query, button=mutation, textbox=mutation, navigate=query."""
        intents = {s.span_id: s.request_intent for s in parsed_trace.spans}
        assert intents["web-span-001"] == RequestIntent.QUERY      # navigate
        assert intents["web-span-002"] == RequestIntent.MUTATION   # type username
        assert intents["web-span-003"] == RequestIntent.MUTATION   # type password
        assert intents["web-span-004"] == RequestIntent.MUTATION   # click button
        assert intents["web-span-005"] == RequestIntent.QUERY      # click link
        assert intents["web-span-006"] == RequestIntent.QUERY      # observe
        assert intents["web-span-007"] == RequestIntent.MUTATION   # click button

    def test_span_structure_navigational(self, parsed_trace):
        for span in parsed_trace.spans:
            assert span.span_structure == SpanStructure.NAVIGATIONAL

    def test_delta_compression_signal(self, parsed_trace):
        """Span 6 is noise with delta_from_prior=none (no change from previous snapshot)."""
        span6 = next(s for s in parsed_trace.spans if s.span_id == "web-span-006")
        assert span6.signal_class == SignalClass.NOISE
        assert span6.delta_from_prior == DeltaFromPrior.NONE
        assert span6.compression_ratio == 0.0

    def test_error_span(self, parsed_trace):
        span7 = next(s for s in parsed_trace.spans if s.span_id == "web-span-007")
        assert span7.response_outcome == ResponseOutcome.ERROR
        assert span7.divergence_score == 0.9

    def test_cross_modality_chain(self, parsed_trace):
        """Web→API→DB chain via contains_contexts."""
        span5 = next(s for s in parsed_trace.spans if s.span_id == "web-span-005")
        assert len(span5.contains_contexts) == 2
        modalities = {ctx.modality for ctx in span5.contains_contexts}
        assert modalities == {"api", "db"}
        relationships = {ctx.relationship for ctx in span5.contains_contexts}
        assert "triggers" in relationships
        assert "reads_from" in relationships

    def test_session_intent(self, parsed_trace):
        assert parsed_trace.session_intent is not None
        assert parsed_trace.session_intent.value == "exploration"

    def test_findings_parsed(self, parsed_trace):
        assert len(parsed_trace.findings) == 3

    def test_no_parse_errors(self, web_trace_data):
        result = parse_trace(web_trace_data)
        assert result.ok
        assert result.error_count == 0


# --- Analyzer E2E ---


class TestWebAnalyzerE2E:
    def test_modality_profile_web(self, analysis_result):
        assert "web" in analysis_result.modality_profiles
        profile = analysis_result.modality_profiles["web"]
        assert profile.span_count == 7
        assert profile.total_duration_ms == 2000 + 500 + 500 + 2000 + 800 + 100 + 1500

    def test_success_and_error_rates(self, analysis_result):
        profile = analysis_result.modality_profiles["web"]
        assert profile.success_rate == pytest.approx(6 / 7, abs=0.01)
        assert profile.error_rate == pytest.approx(1 / 7, abs=0.01)

    def test_unique_targets(self, analysis_result):
        profile = analysis_result.modality_profiles["web"]
        assert len(profile.unique_targets) >= 6  # 7 spans, some unique targets

    def test_no_api_or_db_profile(self, analysis_result):
        assert "api" not in analysis_result.modality_profiles
        assert "db" not in analysis_result.modality_profiles

    def test_endpoint_catalog(self, analysis_result):
        assert len(analysis_result.endpoints) > 0

    def test_dependency_graph(self, analysis_result):
        assert len(analysis_result.graph.nodes) >= 7

    def test_finding_clusters(self, analysis_result):
        assert len(analysis_result.finding_clusters) == 3

    def test_coverage_modalities(self, analysis_result):
        assert "web" in analysis_result.coverage.modalities_seen


# --- Optimizer E2E ---


class TestWebOptimizerE2E:
    def test_merged_findings(self, optimized_result):
        assert optimized_result.finding_count == 3

    def test_coverage(self, optimized_result):
        assert optimized_result.coverage.total_spans == 7
        assert optimized_result.coverage.total_traces == 1


# --- Universal pipeline: all three modalities ---


class TestAllModalitiesTogether:
    """Optimizer merges Web + DB + API analyses — same code, same structure."""

    def test_three_modality_merge(self, web_trace_data):
        # Parse web trace
        web_result = parse_trace(web_trace_data)
        assert web_result.ok
        web_analysis = analyze(web_result.traces)

        # Parse DB trace
        db_data = json.loads(
            (Path(__file__).parent / "fixtures" / "trace_db_session_007.json").read_text()
        )
        db_result = parse_trace(db_data)
        assert db_result.ok
        db_analysis = analyze(db_result.traces)

        # Minimal API trace
        api_data = {
            "schema_version": "0.2",
            "trace_id": "trace-api-triple",
            "agent_id": "ApiExplorer",
            "agent_role": "explorer",
            "system": "ignite",
            "status": "completed",
            "started_at": "2026-07-30T00:00:00Z",
            "objective": "Triple modality test",
            "spans": [{
                "span_id": "api-span-triple",
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
        assert api_result.ok
        api_analysis = analyze(api_result.traces)

        # Optimize all three together
        opt = optimize([web_analysis, db_analysis, api_analysis])
        assert opt.source_analysis_count == 3
        assert opt.coverage.total_spans == 7 + 7 + 1  # web + db + api
        assert opt.coverage.total_traces == 3


# --- Planner E2E ---


class TestWebPlannerE2E:
    def test_plan_generated_from_web_findings(self, optimized_result):
        """Planner produces actions from Web findings using same code as API/DB."""
        execution_plan = plan(optimized_result)
        assert execution_plan.action_count > 0

    def test_error_handler_from_element_finding(self, optimized_result):
        """Error finding produces a GENERATE_HANDLER action."""
        execution_plan = plan(optimized_result)
        handlers = execution_plan.actions_by_kind(ActionKind.GENERATE_HANDLER)
        assert len(handlers) >= 1

    def test_plan_has_source_counts(self, optimized_result):
        execution_plan = plan(optimized_result)
        assert execution_plan.source_finding_count == optimized_result.finding_count


# --- Executor E2E ---


class TestWebExecutorE2E:
    def test_executor_runs_plan(self, optimized_result, tmp_path):
        """Executor handles Web-derived plan actions without modality branching."""
        execution_plan = plan(optimized_result)
        result = execute(execution_plan, optimized_result, output_dir=tmp_path)
        assert result.total == execution_plan.action_count
        assert result.total > 0

    def test_executor_produces_artifacts(self, optimized_result, tmp_path):
        execution_plan = plan(optimized_result)
        result = execute(execution_plan, optimized_result, output_dir=tmp_path)
        succeeded = result.by_status(ActionStatus.SUCCESS)
        generated_files = []
        for ar in succeeded:
            generated_files.extend(ar.generated_files)
        assert len(generated_files) > 0


# --- Feedback E2E ---


class TestWebFeedbackE2E:
    def test_feedback_captures_web_execution(self, optimized_result, tmp_path):
        """Feedback writer creates trace from Web execution — closes the loop."""
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


# --- Web ErrP integration ---


class TestWebErrPIntegration:
    """Verify Web error codes flow through lookup_correction via the Web profile."""

    def test_element_not_found_idempotency_retry(self):
        """element_not_found should get idempotency_retry via Web profile override."""
        correction = lookup_correction("mutation", "error", error_code="element_not_found")
        assert correction == CorrectionAction.IDEMPOTENCY_RETRY

    def test_session_expired_restart_flow(self):
        """session_expired should restart flow, not retry."""
        correction = lookup_correction("query", "error", error_code="session_expired")
        assert correction == CorrectionAction.RESTART_FLOW

    def test_navigation_timeout_backoff(self):
        correction = lookup_correction("query", "error", error_code="navigation_timeout")
        assert correction == CorrectionAction.BACKOFF_RETRY

    def test_browser_crashed_log_escalate(self):
        correction = lookup_correction("mutation", "error", error_code="browser_crashed")
        assert correction == CorrectionAction.LOG_ESCALATE

    def test_stale_element_idempotency_retry(self):
        correction = lookup_correction("mutation", "error", error_code="stale_element")
        assert correction == CorrectionAction.IDEMPOTENCY_RETRY

    def test_unknown_web_error_falls_through_to_table(self):
        """Unknown error code falls through to the intent-class table."""
        correction = lookup_correction("query", "error", error_code="some_unknown_web_error")
        assert correction == CorrectionAction.BACKOFF_RETRY

    def test_success_still_returns_none(self):
        correction = lookup_correction("query", "success", error_code="element_not_found")
        assert correction is None


# --- Full E2E pipeline (acid test) ---


class TestFullWebPipeline:
    """The acid test: Web trace flows through ALL 6 stages using the SAME code paths as API/DB."""

    def test_full_pipeline_e2e(self, web_trace_data, tmp_path):
        """Parse -> Analyze -> Optimize -> Plan -> Execute -> Feedback, all from a Web trace."""
        # Stage 1: Parser
        parsed = parse_trace(web_trace_data)
        assert parsed.ok
        assert all(s.modality == Modality.WEB for s in parsed.traces[0].spans)

        # Stage 2: Analyzer
        analysis = analyze(parsed.traces)
        assert "web" in analysis.modality_profiles
        assert analysis.modality_profiles["web"].span_count == 7

        # Stage 3: Optimizer
        optimized = optimize([analysis])
        assert optimized.finding_count == 3
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
