"""E2E tests for Message modality — trace-msg-session-010 through Parser→Analyzer→Optimizer.

Fifth and final modality extension. Proves message traces flow through the SAME
universal code paths as API, DB, Web, and CLI — completing the cross-system chain.
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
from ignite_parser.spike import (
    SpikeSignal,
    SpikeType,
    Urgency,
    classify_signal,
    compute_correlation_boost,
    detect_spikes,
)
from ignite_parser.models import (
    Modality,
    RequestIntent,
    ResponseOutcome,
    SignalClass,
    SpanStructure,
)


FIXTURE_PATH = Path(__file__).parent / "fixtures" / "trace_msg_session_010.json"


@pytest.fixture
def msg_trace_data():
    return json.loads(FIXTURE_PATH.read_text(encoding="utf-8"))


@pytest.fixture
def parsed_trace(msg_trace_data):
    result = parse_trace(msg_trace_data)
    assert result.ok, f"Parse errors: {[e.message for e in result.errors]}"
    return result.traces[0]


@pytest.fixture
def analysis_result(parsed_trace):
    return analyze([parsed_trace])


@pytest.fixture
def optimized_result(analysis_result):
    return optimize([analysis_result])


# --- Parser E2E ---


class TestMsgParserE2E:
    def test_parses_all_7_spans(self, parsed_trace):
        assert len(parsed_trace.spans) == 7

    def test_all_spans_modality_message(self, parsed_trace):
        for span in parsed_trace.spans:
            assert span.modality == Modality.MESSAGE

    def test_modality_ext_preserved(self, parsed_trace):
        for span in parsed_trace.spans:
            assert span.modality_ext is not None
            assert span.modality_ext["type"] == "msg_ext"

    def test_msg_ext_p0_fields(self, parsed_trace):
        """P0: message_type and delivery_guarantee flow through."""
        span1 = parsed_trace.spans[0]
        assert span1.modality_ext["message_type"] == "webhook"
        assert span1.modality_ext["delivery_guarantee"] == "at_least_once"
        assert span1.modality_ext["source_system"] == "github"

    def test_msg_ext_p1_fields(self, parsed_trace):
        """P1: delivery_attempt, sequence_id, payload_schema."""
        span4 = next(s for s in parsed_trace.spans if s.span_id == "msg-span-004")
        assert span4.modality_ext["delivery_attempt"] == 1
        assert span4.modality_ext["sequence_id"] == "seq-001"

    def test_msg_ext_p2_idempotency(self, parsed_trace):
        """P2: idempotency_key for dedup."""
        span2 = next(s for s in parsed_trace.spans if s.span_id == "msg-span-002")
        assert span2.modality_ext["idempotency_key"] == "ci-run-abc123-main"

    def test_intent_classification(self, parsed_trace):
        intents = {s.span_id: s.request_intent for s in parsed_trace.spans}
        assert intents["msg-span-001"] == RequestIntent.QUERY      # receive
        assert intents["msg-span-002"] == RequestIntent.MUTATION   # publish
        assert intents["msg-span-003"] == RequestIntent.QUERY      # subscribe
        assert intents["msg-span-004"] == RequestIntent.QUERY      # receive
        assert intents["msg-span-005"] == RequestIntent.MUTATION   # ack
        assert intents["msg-span-006"] == RequestIntent.MUTATION   # publish (error)
        assert intents["msg-span-007"] == RequestIntent.MUTATION   # publish (retry)

    def test_error_span(self, parsed_trace):
        span6 = next(s for s in parsed_trace.spans if s.span_id == "msg-span-006")
        assert span6.response_outcome == ResponseOutcome.ERROR
        assert span6.divergence_score == 0.8

    def test_retry_span_delivery_attempt(self, parsed_trace):
        span7 = next(s for s in parsed_trace.spans if s.span_id == "msg-span-007")
        assert span7.response_outcome == ResponseOutcome.SUCCESS
        assert span7.modality_ext["delivery_attempt"] == 2

    def test_span_structure_conversational(self, parsed_trace):
        for span in parsed_trace.spans:
            assert span.span_structure == SpanStructure.CONVERSATIONAL

    def test_cross_modality_context(self, parsed_trace):
        """CLI→Message chain via contains_contexts."""
        span1 = parsed_trace.spans[0]
        assert len(span1.contains_contexts) == 1
        assert span1.contains_contexts[0].modality == "cli"
        assert span1.contains_contexts[0].relationship == "triggers"

    def test_session_intent(self, parsed_trace):
        assert parsed_trace.session_intent.value == "validation"

    def test_findings_parsed(self, parsed_trace):
        assert len(parsed_trace.findings) == 2

    def test_no_parse_errors(self, msg_trace_data):
        result = parse_trace(msg_trace_data)
        assert result.ok


# --- Analyzer E2E ---


class TestMsgAnalyzerE2E:
    def test_modality_profile_message(self, analysis_result):
        assert "message" in analysis_result.modality_profiles
        profile = analysis_result.modality_profiles["message"]
        assert profile.span_count == 7

    def test_success_and_error_rates(self, analysis_result):
        profile = analysis_result.modality_profiles["message"]
        assert profile.success_rate == pytest.approx(6 / 7, abs=0.01)
        assert profile.error_rate == pytest.approx(1 / 7, abs=0.01)

    def test_no_other_modality_profiles(self, analysis_result):
        assert "api" not in analysis_result.modality_profiles

    def test_endpoint_catalog(self, analysis_result):
        assert len(analysis_result.endpoints) > 0

    def test_dependency_graph(self, analysis_result):
        assert len(analysis_result.graph.nodes) >= 7

    def test_coverage_modalities(self, analysis_result):
        assert "message" in analysis_result.coverage.modalities_seen


# --- Optimizer E2E ---


class TestMsgOptimizerE2E:
    def test_merged_findings(self, optimized_result):
        assert optimized_result.finding_count == 2

    def test_coverage(self, optimized_result):
        assert optimized_result.coverage.total_spans == 7


# --- All five modalities together ---


class TestAllFiveModalities:
    """The complete proof: all 5 modalities optimize together."""

    def test_five_modality_merge(self, msg_trace_data):
        fixtures_dir = Path(__file__).parent / "fixtures"

        msg_result = parse_trace(msg_trace_data)
        cli_result = parse_trace(json.loads(
            (fixtures_dir / "trace_cli_session_009.json").read_text()
        ))
        db_result = parse_trace(json.loads(
            (fixtures_dir / "trace_db_session_007.json").read_text()
        ))
        web_result = parse_trace(json.loads(
            (fixtures_dir / "trace_web_session_008.json").read_text()
        ))
        api_data = {
            "schema_version": "0.2",
            "trace_id": "trace-api-quint",
            "agent_id": "ApiExplorer",
            "agent_role": "explorer",
            "system": "ignite",
            "status": "completed",
            "started_at": "2026-07-30T00:00:00Z",
            "objective": "Quint modality test",
            "spans": [{
                "span_id": "api-span-quint",
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

        assert all(r.ok for r in [msg_result, cli_result, db_result, web_result, api_result])

        analyses = [
            analyze(msg_result.traces),
            analyze(cli_result.traces),
            analyze(db_result.traces),
            analyze(web_result.traces),
            analyze(api_result.traces),
        ]

        opt = optimize(analyses)
        assert opt.source_analysis_count == 5
        assert opt.coverage.total_traces == 5


# --- Spike Signal Detection E2E ---


class TestSpikeDetectionE2E:
    def test_detect_spikes_on_message_trace(self, parsed_trace):
        """Spike detection pipeline runs on message traces."""
        spikes = detect_spikes([parsed_trace], system="ignite")
        assert len(spikes) >= 1

    def test_error_spike_detected(self, parsed_trace):
        """The broker_unavailable error produces an error spike."""
        spikes = detect_spikes([parsed_trace], system="ignite")
        error_spikes = [s for s in spikes if s.spike_type == SpikeType.ERROR]
        assert len(error_spikes) >= 1

    def test_error_spike_has_action_space(self, parsed_trace):
        """Error spikes include actionable options for L3."""
        spikes = detect_spikes([parsed_trace], system="ignite")
        error_spikes = [s for s in spikes if s.spike_type == SpikeType.ERROR]
        assert len(error_spikes) >= 1
        assert len(error_spikes[0].action_space) >= 1

    def test_spike_confidence_in_range(self, parsed_trace):
        """All spike confidences are in [0, 1]."""
        spikes = detect_spikes([parsed_trace], system="ignite")
        for spike in spikes:
            assert 0.0 <= spike.confidence <= 1.0

    def test_spikes_sorted_by_confidence(self, parsed_trace):
        """Spikes are returned in descending confidence order."""
        spikes = detect_spikes([parsed_trace], system="ignite")
        if len(spikes) >= 2:
            for i in range(len(spikes) - 1):
                assert spikes[i].confidence >= spikes[i + 1].confidence

    def test_spike_modalities_populated(self, parsed_trace):
        """All spikes have at least one modality."""
        spikes = detect_spikes([parsed_trace], system="ignite")
        for spike in spikes:
            assert len(spike.modalities) >= 1

    def test_correlation_boost_values(self):
        """Correlation boost multipliers match Honey spec."""
        assert compute_correlation_boost(1) == 1.0
        assert compute_correlation_boost(2) == 1.5
        assert compute_correlation_boost(3) == 2.0
        assert compute_correlation_boost(5) == 2.0  # 3+ caps at 2.0

    def test_noise_signal_classification(self):
        """CLI noise commands (cd, ls) classified as noise."""
        from ignite_parser.models import Span, Interaction, Observation
        noise_span = Span(
            span_id="noise-1",
            trace_id="t1",
            sequence=1,
            kind="api_call",
            started_at="2026-07-30T00:00:00Z",
            modality=Modality.CLI.value,
            signal_class=SignalClass.NOISE.value,
            interaction=Interaction(target="cd /tmp"),
            observation=Observation(what_happened="Changed directory"),
            modality_ext={"type": "cli_ext", "command": "cd"},
        )
        assert classify_signal(noise_span) == SignalClass.NOISE.value

    def test_intent_carrying_classification(self):
        """Message publish classified as intent_carrying."""
        from ignite_parser.models import Span, Interaction, Observation
        msg_span = Span(
            span_id="msg-1",
            trace_id="t1",
            sequence=1,
            kind="api_call",
            started_at="2026-07-30T00:00:00Z",
            modality=Modality.MESSAGE.value,
            signal_class=SignalClass.INTENT_CARRYING.value,
            interaction=Interaction(target="publish ci.trigger"),
            observation=Observation(what_happened="Published CI trigger"),
            modality_ext={"type": "msg_ext", "operation": "publish"},
        )
        assert classify_signal(msg_span) == SignalClass.INTENT_CARRYING.value

    def test_message_noise_filtered(self):
        """Message heartbeat classified as noise."""
        from ignite_parser.models import Span, Interaction, Observation
        heartbeat = Span(
            span_id="hb-1",
            trace_id="t1",
            sequence=1,
            kind="api_call",
            started_at="2026-07-30T00:00:00Z",
            modality=Modality.MESSAGE.value,
            interaction=Interaction(target="heartbeat"),
            observation=Observation(what_happened="Heartbeat ping"),
            modality_ext={"type": "msg_ext", "operation": "heartbeat"},
        )
        assert classify_signal(heartbeat) == SignalClass.NOISE.value

    def test_spike_source_system(self, parsed_trace):
        """Spikes carry the source system identifier."""
        spikes = detect_spikes([parsed_trace], system="ignite")
        for spike in spikes:
            assert spike.source_system != ""
