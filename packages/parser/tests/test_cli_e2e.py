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
from ignite_parser.models import (
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
    def test_parses_all_7_spans(self, parsed_trace):
        assert len(parsed_trace.spans) == 7

    def test_all_spans_modality_cli(self, parsed_trace):
        for span in parsed_trace.spans:
            assert span.modality == Modality.CLI

    def test_modality_ext_preserved(self, parsed_trace):
        for span in parsed_trace.spans:
            assert span.modality_ext is not None
            assert span.modality_ext["type"] == "cli_ext"

    def test_cli_ext_command_line(self, parsed_trace):
        """P0 field: command_line flows through Parser."""
        span1 = parsed_trace.spans[0]
        assert span1.modality_ext["command_line"] == "git status"
        assert span1.modality_ext["command"] == "git"

    def test_exit_codes(self, parsed_trace):
        codes = {s.span_id: s.modality_ext.get("exit_code") for s in parsed_trace.spans}
        assert codes["cli-span-001"] == 0   # git status
        assert codes["cli-span-003"] == 1   # pytest failure
        assert codes["cli-span-004"] == 0   # pytest retry

    def test_intent_classification(self, parsed_trace):
        intents = {s.span_id: s.request_intent for s in parsed_trace.spans}
        assert intents["cli-span-001"] == RequestIntent.QUERY           # git status
        assert intents["cli-span-002"] == RequestIntent.STATE_TRANSITION  # make build
        assert intents["cli-span-003"] == RequestIntent.QUERY           # pytest (read)
        assert intents["cli-span-005"] == RequestIntent.MUTATION        # git commit
        assert intents["cli-span-006"] == RequestIntent.MUTATION        # git push

    def test_error_span(self, parsed_trace):
        span3 = next(s for s in parsed_trace.spans if s.span_id == "cli-span-003")
        assert span3.response_outcome == ResponseOutcome.ERROR
        assert span3.divergence_score == 0.3

    def test_span_structure_narrative(self, parsed_trace):
        for span in parsed_trace.spans:
            assert span.span_structure == SpanStructure.NARRATIVE

    def test_cross_modality_context(self, parsed_trace):
        span6 = next(s for s in parsed_trace.spans if s.span_id == "cli-span-006")
        assert len(span6.contains_contexts) == 1
        assert span6.contains_contexts[0].modality == "api"
        assert span6.contains_contexts[0].relationship == "triggers"

    def test_session_intent(self, parsed_trace):
        assert parsed_trace.session_intent.value == "deployment"

    def test_findings_parsed(self, parsed_trace):
        assert len(parsed_trace.findings) == 2

    def test_no_parse_errors(self, cli_trace_data):
        result = parse_trace(cli_trace_data)
        assert result.ok


# --- Analyzer E2E ---


class TestCliAnalyzerE2E:
    def test_modality_profile_cli(self, analysis_result):
        assert "cli" in analysis_result.modality_profiles
        profile = analysis_result.modality_profiles["cli"]
        assert profile.span_count == 7

    def test_success_and_error_rates(self, analysis_result):
        profile = analysis_result.modality_profiles["cli"]
        assert profile.success_rate == pytest.approx(6 / 7, abs=0.01)
        assert profile.error_rate == pytest.approx(1 / 7, abs=0.01)

    def test_no_other_modality_profiles(self, analysis_result):
        assert "api" not in analysis_result.modality_profiles
        assert "db" not in analysis_result.modality_profiles
        assert "web" not in analysis_result.modality_profiles

    def test_endpoint_catalog(self, analysis_result):
        assert len(analysis_result.endpoints) > 0

    def test_dependency_graph(self, analysis_result):
        assert len(analysis_result.graph.nodes) >= 7

    def test_coverage_modalities(self, analysis_result):
        assert "cli" in analysis_result.coverage.modalities_seen


# --- Optimizer E2E ---


class TestCliOptimizerE2E:
    def test_merged_findings(self, optimized_result):
        assert optimized_result.finding_count == 2

    def test_coverage(self, optimized_result):
        assert optimized_result.coverage.total_spans == 7


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
        assert opt.coverage.total_spans == 7 + 7 + 7 + 1  # cli + db + web + api
        assert opt.coverage.total_traces == 4
