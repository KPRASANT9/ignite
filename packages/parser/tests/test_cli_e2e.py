"""E2E tests for CLI modality — trace-cli-session-009 through Parser→Analyzer→Optimizer.

Fourth and final modality. Proves CLI traces flow through the SAME universal
code paths as API, DB, and Web — zero modality-specific branching.
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
)


FIXTURE_PATH = Path(__file__).parent / "fixtures" / "trace_cli_session_009.json"


@pytest.fixture
def cli_trace_data():
    return json.loads(FIXTURE_PATH.read_text(encoding="utf-8"))


@pytest.fixture
def parsed_trace(cli_trace_data):
    result = parse_trace(cli_trace_data)
    assert result.ok, f"Parse errors: {[e.message for e in result.errors]}"
    return result.traces[0]


@pytest.fixture
def analysis_result(parsed_trace):
    return analyze([parsed_trace])


@pytest.fixture
def optimized_result(analysis_result):
    return optimize([analysis_result])


# --- Parser E2E ---


class TestCliParserE2E:
    def test_parses_all_8_spans(self, parsed_trace):
        assert len(parsed_trace.spans) == 8

    def test_all_spans_modality_cli(self, parsed_trace):
        for span in parsed_trace.spans:
            assert span.modality == Modality.CLI

    def test_modality_ext_preserved(self, parsed_trace):
        for span in parsed_trace.spans:
            assert span.modality_ext is not None
            assert span.modality_ext["type"] == "cli_ext"
            assert "command" in span.modality_ext

    def test_cli_ext_command_line(self, parsed_trace):
        """P0 field: command_line flows through Parser."""
        span1 = parsed_trace.spans[0]
        assert span1.modality_ext["command_line"] == "git status"
        assert span1.modality_ext["command"] == "git"

    def test_exit_codes(self, parsed_trace):
        codes = {s.span_id: s.modality_ext.get("exit_code") for s in parsed_trace.spans}
        assert codes["cli-span-001"] == 0   # git status
        assert codes["cli-span-004"] == 1   # npm run test (failure)
        assert codes["cli-span-008"] == 0   # npm run test (retry)

    def test_intent_classification(self, parsed_trace):
        intents = {s.span_id: s.request_intent for s in parsed_trace.spans}
        assert intents["cli-span-001"] == RequestIntent.QUERY            # git status
        assert intents["cli-span-002"] == RequestIntent.MUTATION         # npm install
        assert intents["cli-span-003"] == RequestIntent.STATE_TRANSITION # npm run build
        assert intents["cli-span-004"] == RequestIntent.STATE_TRANSITION # npm run test (fail)
        assert intents["cli-span-005"] == RequestIntent.QUERY            # cat
        assert intents["cli-span-006"] == RequestIntent.QUERY            # grep
        assert intents["cli-span-007"] == RequestIntent.MUTATION         # git commit
        assert intents["cli-span-008"] == RequestIntent.STATE_TRANSITION # npm run test (pass)

    def test_error_span(self, parsed_trace):
        span4 = next(s for s in parsed_trace.spans if s.span_id == "cli-span-004")
        assert span4.response_outcome == ResponseOutcome.ERROR
        assert span4.divergence_score == 0.7

    def test_retry_span_success(self, parsed_trace):
        span8 = next(s for s in parsed_trace.spans if s.span_id == "cli-span-008")
        assert span8.response_outcome == ResponseOutcome.SUCCESS

    def test_span_structure_transactional(self, parsed_trace):
        for span in parsed_trace.spans:
            assert span.span_structure == SpanStructure.TRANSACTIONAL

    def test_cross_modality_build_api(self, parsed_trace):
        """Build span triggers API calls to package registry."""
        span3 = next(s for s in parsed_trace.spans if s.span_id == "cli-span-003")
        assert len(span3.contains_contexts) == 1
        assert span3.contains_contexts[0].modality == "api"
        assert span3.contains_contexts[0].relationship == "triggers"

    def test_cross_modality_commit_db(self, parsed_trace):
        """Git commit writes to .git database."""
        span7 = next(s for s in parsed_trace.spans if s.span_id == "cli-span-007")
        assert len(span7.contains_contexts) == 1
        assert span7.contains_contexts[0].modality == "db"
        assert span7.contains_contexts[0].relationship == "writes_to"

    def test_session_intent(self, parsed_trace):
        assert parsed_trace.session_intent.value == "validation"

    def test_findings_parsed(self, parsed_trace):
        assert len(parsed_trace.findings) == 3

    def test_no_parse_errors(self, cli_trace_data):
        result = parse_trace(cli_trace_data)
        assert result.ok
        assert result.error_count == 0


# --- Analyzer E2E ---


class TestCliAnalyzerE2E:
    def test_modality_profile_cli(self, analysis_result):
        assert "cli" in analysis_result.modality_profiles
        profile = analysis_result.modality_profiles["cli"]
        assert profile.span_count == 8
        assert profile.total_duration_ms == 500 + 30000 + 30000 + 15000 + 200 + 500 + 300 + 15000

    def test_success_and_error_rates(self, analysis_result):
        profile = analysis_result.modality_profiles["cli"]
        assert profile.success_rate == pytest.approx(7 / 8, abs=0.01)
        assert profile.error_rate == pytest.approx(1 / 8, abs=0.01)

    def test_no_other_modality_profiles(self, analysis_result):
        assert "api" not in analysis_result.modality_profiles
        assert "db" not in analysis_result.modality_profiles
        assert "web" not in analysis_result.modality_profiles

    def test_endpoint_catalog(self, analysis_result):
        assert len(analysis_result.endpoints) > 0

    def test_dependency_graph(self, analysis_result):
        assert len(analysis_result.graph.nodes) >= 8

    def test_finding_clusters(self, analysis_result):
        assert len(analysis_result.finding_clusters) == 3

    def test_coverage_modalities(self, analysis_result):
        assert "cli" in analysis_result.coverage.modalities_seen


# --- Optimizer E2E ---


class TestCliOptimizerE2E:
    def test_merged_findings(self, optimized_result):
        assert optimized_result.finding_count == 3

    def test_coverage(self, optimized_result):
        assert optimized_result.coverage.total_spans == 8
        assert optimized_result.coverage.total_traces == 1


# --- All four modalities together ---


class TestAllFourModalities:
    """The ultimate proof: API + DB + Web + CLI all optimize together."""

    def test_four_modality_merge(self, cli_trace_data):
        fixtures_dir = Path(__file__).parent / "fixtures"

        # Parse all four
        cli_result = parse_trace(cli_trace_data)
        db_result = parse_trace(json.loads(
            (fixtures_dir / "trace_db_session_007.json").read_text()
        ))
        web_result = parse_trace(json.loads(
            (fixtures_dir / "trace_web_session_008.json").read_text()
        ))
        api_data = {
            "schema_version": "0.2",
            "trace_id": "trace-api-quad",
            "agent_id": "ApiExplorer",
            "agent_role": "explorer",
            "system": "ignite",
            "status": "completed",
            "started_at": "2026-07-30T00:00:00Z",
            "objective": "Quad modality test",
            "spans": [{
                "span_id": "api-span-quad",
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

        assert all(r.ok for r in [cli_result, db_result, web_result, api_result])

        # Analyze each
        analyses = [
            analyze(cli_result.traces),
            analyze(db_result.traces),
            analyze(web_result.traces),
            analyze(api_result.traces),
        ]

        # Optimize all together
        opt = optimize(analyses)
        assert opt.source_analysis_count == 4
        assert opt.coverage.total_spans == 8 + 7 + 7 + 1  # cli + db + web + api
        assert opt.coverage.total_traces == 4


# --- Planner E2E ---


class TestCliPlannerE2E:
    def test_plan_generated_from_cli_findings(self, optimized_result):
        execution_plan = plan(optimized_result)
        assert execution_plan.action_count > 0

    def test_error_handler_from_test_failure(self, optimized_result):
        execution_plan = plan(optimized_result)
        handlers = execution_plan.actions_by_kind(ActionKind.GENERATE_HANDLER)
        assert len(handlers) >= 1

    def test_plan_has_source_counts(self, optimized_result):
        execution_plan = plan(optimized_result)
        assert execution_plan.source_finding_count == optimized_result.finding_count


# --- Executor E2E ---


class TestCliExecutorE2E:
    def test_executor_runs_plan(self, optimized_result, tmp_path):
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


class TestCliFeedbackE2E:
    def test_feedback_captures_cli_execution(self, optimized_result, tmp_path):
        execution_plan = plan(optimized_result)
        result = execute(execution_plan, optimized_result, output_dir=tmp_path)
        feedback_path = capture_feedback(execution_plan, result, output_dir=tmp_path)
        assert feedback_path is not None
        assert feedback_path.exists()

    def test_feedback_trace_parseable(self, optimized_result, tmp_path):
        execution_plan = plan(optimized_result)
        result = execute(execution_plan, optimized_result, output_dir=tmp_path)
        feedback_path = capture_feedback(execution_plan, result, output_dir=tmp_path)
        assert feedback_path is not None
        feedback_data = json.loads(feedback_path.read_text(encoding="utf-8"))
        re_parsed = parse_trace(feedback_data)
        assert re_parsed.ok, f"Feedback re-parse errors: {[e.message for e in re_parsed.errors]}"


# --- CLI ErrP integration ---


class TestCliErrPIntegration:
    """Verify CLI error codes flow through lookup_correction via the CLI profile."""

    def test_command_not_found_escalate(self):
        correction = lookup_correction("query", "error", error_code="command_not_found")
        assert correction == CorrectionAction.ESCALATE_HUMAN

    def test_timeout_backoff_retry(self):
        correction = lookup_correction("state_transition", "error", error_code="timeout")
        assert correction == CorrectionAction.BACKOFF_RETRY

    def test_oom_killed_log_escalate(self):
        """OOM kill (exit 137) — log and escalate, not safe to auto-retry."""
        correction = lookup_correction("state_transition", "error", error_code="oom_killed")
        assert correction == CorrectionAction.LOG_ESCALATE

    def test_general_error_backoff(self):
        correction = lookup_correction("mutation", "error", error_code="general_error")
        assert correction == CorrectionAction.BACKOFF_RETRY

    def test_test_failure_escalate(self):
        """Test failure must NOT be retried — fix the code, not the run."""
        correction = lookup_correction("state_transition", "error", error_code="test_failure")
        assert correction == CorrectionAction.ESCALATE_HUMAN

    def test_unknown_cli_error_falls_through_to_table(self):
        correction = lookup_correction("query", "error", error_code="some_unknown_cli_error")
        assert correction == CorrectionAction.BACKOFF_RETRY

    def test_success_still_returns_none(self):
        correction = lookup_correction("query", "success", error_code="command_not_found")
        assert correction is None


# --- Full E2E pipeline (acid test) ---


class TestFullCliPipeline:
    """The acid test: CLI trace flows through ALL 6 stages using the SAME code paths."""

    def test_full_pipeline_e2e(self, cli_trace_data, tmp_path):
        """Parse -> Analyze -> Optimize -> Plan -> Execute -> Feedback, all from a CLI trace."""
        # Stage 1: Parser
        parsed = parse_trace(cli_trace_data)
        assert parsed.ok
        assert all(s.modality == Modality.CLI for s in parsed.traces[0].spans)

        # Stage 2: Analyzer
        analysis = analyze(parsed.traces)
        assert "cli" in analysis.modality_profiles
        assert analysis.modality_profiles["cli"].span_count == 8

        # Stage 3: Optimizer
        optimized = optimize([analysis])
        assert optimized.finding_count == 3
        assert optimized.coverage.total_spans == 8

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
