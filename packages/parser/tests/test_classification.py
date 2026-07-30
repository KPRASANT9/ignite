"""Tests for v0.2 schema classification — parser backward compat + new fields."""

from ignite_parser.models import (
    DeltaFromPrior,
    Modality,
    RequestIntent,
    ResponseOutcome,
    SignalClass,
    Span,
    StateTransitionSubtype,
)
from ignite_parser.parser import parse_trace


def _minimal_trace(**overrides):
    """Build a minimal valid trace dict."""
    base = {
        "schema_version": "0.1",
        "trace_id": "test-trace-001",
        "agent_id": "test-agent",
        "agent_role": "explorer",
        "system": "test-system",
        "status": "completed",
        "started_at": "2026-07-28T10:00:00Z",
        "objective": "test objective",
        "spans": [],
        "findings": [],
    }
    base.update(overrides)
    return base


def _minimal_span(**overrides):
    """Build a minimal valid span dict."""
    base = {
        "span_id": "span-001",
        "kind": "api_call",
        "started_at": "2026-07-28T10:00:00Z",
        "duration_ms": 100,
        "observation": {
            "what_happened": "called endpoint",
            "what_learned": "got response",
            "confidence": "high",
        },
    }
    base.update(overrides)
    return base


class TestBackwardCompatibility:
    """v0.1 traces must parse as valid v0.2 with defaults."""

    def test_v01_trace_parses_successfully(self):
        trace = _minimal_trace(spans=[_minimal_span()])
        result = parse_trace(trace)
        assert result.ok
        assert len(result.traces) == 1

    def test_v01_span_gets_defaults(self):
        trace = _minimal_trace(spans=[_minimal_span()])
        result = parse_trace(trace)
        span = result.traces[0].spans[0]
        assert span.modality == Modality.API
        assert span.request_intent == RequestIntent.QUERY
        assert span.response_outcome == ResponseOutcome.SUCCESS
        assert span.signal_class == SignalClass.INTENT_CARRYING
        assert span.delta_from_prior == DeltaFromPrior.FULL
        assert span.state_transition_subtype is None
        assert span.correlation_ids == []

    def test_v02_schema_version_accepted(self):
        trace = _minimal_trace(schema_version="0.2", spans=[_minimal_span()])
        result = parse_trace(trace)
        assert result.ok


class TestClassificationParsing:
    """v0.2 classification fields are correctly parsed."""

    def test_all_classification_fields(self):
        span_data = _minimal_span(
            modality="db",
            request_intent="mutation",
            response_outcome="error",
            signal_class="noise",
            delta_from_prior="none",
            state_transition_subtype="state_change",
            correlation_ids=["session-abc", "journey-xyz"],
        )
        trace = _minimal_trace(schema_version="0.2", spans=[span_data])
        result = parse_trace(trace)
        assert result.ok
        span = result.traces[0].spans[0]
        assert span.modality == Modality.DB
        assert span.request_intent == RequestIntent.MUTATION
        assert span.response_outcome == ResponseOutcome.ERROR
        assert span.signal_class == SignalClass.NOISE
        assert span.delta_from_prior == DeltaFromPrior.NONE
        assert span.state_transition_subtype == StateTransitionSubtype.STATE_CHANGE
        assert span.correlation_ids == ["session-abc", "journey-xyz"]

    def test_partial_classification(self):
        """Only some v0.2 fields provided — rest get defaults."""
        span_data = _minimal_span(request_intent="mutation")
        trace = _minimal_trace(schema_version="0.2", spans=[span_data])
        result = parse_trace(trace)
        assert result.ok
        span = result.traces[0].spans[0]
        assert span.request_intent == RequestIntent.MUTATION
        assert span.modality == Modality.API  # default
        assert span.response_outcome == ResponseOutcome.SUCCESS  # default

    def test_invalid_modality_warns(self):
        span_data = _minimal_span(modality="quantum_computer")
        trace = _minimal_trace(schema_version="0.2", spans=[span_data])
        result = parse_trace(trace)
        assert result.ok  # warnings, not errors
        assert result.warning_count > 0
        span = result.traces[0].spans[0]
        assert span.modality == Modality.API  # falls back to default

    def test_invalid_request_intent_warns(self):
        span_data = _minimal_span(request_intent="teleport")
        trace = _minimal_trace(spans=[span_data])
        result = parse_trace(trace)
        assert result.ok
        span = result.traces[0].spans[0]
        assert span.request_intent == RequestIntent.QUERY  # default

    def test_invalid_response_outcome_warns(self):
        span_data = _minimal_span(response_outcome="exploded")
        trace = _minimal_trace(spans=[span_data])
        result = parse_trace(trace)
        assert result.ok
        span = result.traces[0].spans[0]
        assert span.response_outcome == ResponseOutcome.SUCCESS  # default


class TestIntentOutcomeSplit:
    """The key v0.2 design: request_intent and response_outcome are separate."""

    def test_query_rate_limited(self):
        """A 429 on /transactions/get is a query that got rate_limited."""
        span_data = _minimal_span(
            request_intent="query",
            response_outcome="rate_limited",
        )
        trace = _minimal_trace(schema_version="0.2", spans=[span_data])
        result = parse_trace(trace)
        span = result.traces[0].spans[0]
        assert span.request_intent == RequestIntent.QUERY
        assert span.response_outcome == ResponseOutcome.RATE_LIMITED

    def test_mutation_auth_failure(self):
        span_data = _minimal_span(
            request_intent="mutation",
            response_outcome="auth_failure",
        )
        trace = _minimal_trace(schema_version="0.2", spans=[span_data])
        result = parse_trace(trace)
        span = result.traces[0].spans[0]
        assert span.request_intent == RequestIntent.MUTATION
        assert span.response_outcome == ResponseOutcome.AUTH_FAILURE

    def test_state_transition_with_subtype(self):
        span_data = _minimal_span(
            request_intent="state_transition",
            state_transition_subtype="lifecycle_start",
        )
        trace = _minimal_trace(schema_version="0.2", spans=[span_data])
        result = parse_trace(trace)
        span = result.traces[0].spans[0]
        assert span.request_intent == RequestIntent.STATE_TRANSITION
        assert span.state_transition_subtype == StateTransitionSubtype.LIFECYCLE_START

    def test_config_change_error(self):
        span_data = _minimal_span(
            request_intent="config_change",
            response_outcome="error",
        )
        trace = _minimal_trace(schema_version="0.2", spans=[span_data])
        result = parse_trace(trace)
        span = result.traces[0].spans[0]
        assert span.request_intent == RequestIntent.CONFIG_CHANGE
        assert span.response_outcome == ResponseOutcome.ERROR


class TestAsyncPendingOutcome:
    """M8: async_pending is a valid response_outcome for PRODUCT_NOT_READY."""

    def test_async_pending_parses(self):
        span_data = _minimal_span(
            request_intent="query",
            response_outcome="async_pending",
        )
        trace = _minimal_trace(schema_version="0.2", spans=[span_data])
        result = parse_trace(trace)
        assert result.ok
        span = result.traces[0].spans[0]
        assert span.request_intent == RequestIntent.QUERY
        assert span.response_outcome == ResponseOutcome.ASYNC_PENDING

    def test_decision_intent_parses(self):
        """M6: transfer/authorization/create is a decision gate."""
        span_data = _minimal_span(request_intent="decision")
        trace = _minimal_trace(schema_version="0.2", spans=[span_data])
        result = parse_trace(trace)
        assert result.ok
        assert result.traces[0].spans[0].request_intent == RequestIntent.DECISION


class TestAllModalityValues:
    """Every Modality enum value parses correctly."""

    def test_all_modalities(self):
        for modality in Modality:
            span_data = _minimal_span(modality=modality.value)
            trace = _minimal_trace(schema_version="0.2", spans=[span_data])
            result = parse_trace(trace)
            assert result.ok, f"Failed for modality={modality.value}"
            assert result.traces[0].spans[0].modality == modality


class TestAllRequestIntentValues:
    """Every RequestIntent enum value parses correctly."""

    def test_all_intents(self):
        for intent in RequestIntent:
            span_data = _minimal_span(request_intent=intent.value)
            trace = _minimal_trace(schema_version="0.2", spans=[span_data])
            result = parse_trace(trace)
            assert result.ok, f"Failed for intent={intent.value}"
            assert result.traces[0].spans[0].request_intent == intent


class TestAllResponseOutcomeValues:
    """Every ResponseOutcome enum value parses correctly."""

    def test_all_outcomes(self):
        for outcome in ResponseOutcome:
            span_data = _minimal_span(response_outcome=outcome.value)
            trace = _minimal_trace(schema_version="0.2", spans=[span_data])
            result = parse_trace(trace)
            assert result.ok, f"Failed for outcome={outcome.value}"
            assert result.traces[0].spans[0].response_outcome == outcome
