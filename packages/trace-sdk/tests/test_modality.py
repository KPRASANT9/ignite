"""Tests for v0.2 classification enums and SpanBuilder integration."""

from ignite_trace.modality import (
    DeltaFromPrior,
    Modality,
    RequestIntent,
    ResponseOutcome,
    SignalClass,
    StateTransitionSubtype,
)
from ignite_trace.span import SpanBuilder


class TestEnumValues:
    """Verify enum string values match the v0.2 schema spec."""

    def test_modality_values(self):
        assert Modality.API.value == "api"
        assert Modality.WEB.value == "web"
        assert Modality.DB.value == "db"
        assert Modality.CLI.value == "cli"
        assert Modality.MESSAGE.value == "message"
        assert Modality.CUSTOM.value == "custom"
        assert len(Modality) == 6

    def test_request_intent_values(self):
        assert RequestIntent.STATE_TRANSITION.value == "state_transition"
        assert RequestIntent.QUERY.value == "query"
        assert RequestIntent.MUTATION.value == "mutation"
        assert RequestIntent.CONFIG_CHANGE.value == "config_change"
        assert RequestIntent.DECISION.value == "decision"
        assert len(RequestIntent) == 5

    def test_response_outcome_values(self):
        assert ResponseOutcome.SUCCESS.value == "success"
        assert ResponseOutcome.ERROR.value == "error"
        assert ResponseOutcome.PARTIAL.value == "partial"
        assert ResponseOutcome.TIMEOUT.value == "timeout"
        assert ResponseOutcome.RATE_LIMITED.value == "rate_limited"
        assert ResponseOutcome.AUTH_FAILURE.value == "auth_failure"
        assert ResponseOutcome.ASYNC_PENDING.value == "async_pending"
        assert len(ResponseOutcome) == 7

    def test_signal_class_values(self):
        assert SignalClass.INTENT_CARRYING.value == "intent_carrying"
        assert SignalClass.NOISE.value == "noise"
        assert SignalClass.AMBIGUOUS.value == "ambiguous"
        assert len(SignalClass) == 3

    def test_delta_from_prior_values(self):
        assert DeltaFromPrior.NONE.value == "none"
        assert DeltaFromPrior.PARTIAL.value == "partial"
        assert DeltaFromPrior.FULL.value == "full"
        assert len(DeltaFromPrior) == 3

    def test_state_transition_subtype_values(self):
        assert StateTransitionSubtype.LIFECYCLE_START.value == "lifecycle_start"
        assert StateTransitionSubtype.STATE_CHANGE.value == "state_change"
        assert len(StateTransitionSubtype) == 2

    def test_enums_are_str_enums(self):
        """All classification enums should be str enums for JSON serialization."""
        assert isinstance(Modality.API, str)
        assert isinstance(RequestIntent.QUERY, str)
        assert isinstance(ResponseOutcome.SUCCESS, str)
        assert isinstance(SignalClass.INTENT_CARRYING, str)


class TestSpanBuilderClassification:
    """Test SpanBuilder v0.2 classification fields."""

    def _make_span(self, kind="api_call", target="POST /test") -> SpanBuilder:
        return SpanBuilder(kind=kind, target=target, sequence=1, trace_id="test-trace")

    def test_defaults(self):
        """v0.1 spans get safe defaults for all v0.2 fields."""
        s = self._make_span()
        d = s.to_dict()
        assert d["modality"] == "api"
        assert d["request_intent"] == "query"
        assert d["response_outcome"] == "success"
        assert d["signal_class"] == "intent_carrying"
        assert d["delta_from_prior"] == "full"
        assert "state_transition_subtype" not in d
        assert "correlation_ids" not in d

    def test_classify_all_fields(self):
        s = self._make_span()
        s.classify(
            modality="web",
            request_intent="mutation",
            response_outcome="error",
            signal_class="noise",
            delta_from_prior="none",
            state_transition_subtype="lifecycle_start",
        )
        d = s.to_dict()
        assert d["modality"] == "web"
        assert d["request_intent"] == "mutation"
        assert d["response_outcome"] == "error"
        assert d["signal_class"] == "noise"
        assert d["delta_from_prior"] == "none"
        assert d["state_transition_subtype"] == "lifecycle_start"

    def test_classify_partial(self):
        """Classify only some fields, rest stay at defaults."""
        s = self._make_span()
        s.classify(request_intent="mutation", response_outcome="rate_limited")
        d = s.to_dict()
        assert d["modality"] == "api"  # default
        assert d["request_intent"] == "mutation"
        assert d["response_outcome"] == "rate_limited"
        assert d["signal_class"] == "intent_carrying"  # default

    def test_classify_fluent_chain(self):
        """Classify returns self for fluent chaining."""
        s = self._make_span()
        result = s.classify(modality="db")
        assert result is s

    def test_correlate(self):
        s = self._make_span()
        s.correlate("session-abc", "journey-xyz")
        d = s.to_dict()
        assert d["correlation_ids"] == ["session-abc", "journey-xyz"]

    def test_correlate_empty_not_serialized(self):
        s = self._make_span()
        d = s.to_dict()
        assert "correlation_ids" not in d

    def test_classify_with_enum_values(self):
        """Using enum .value strings works correctly."""
        s = self._make_span()
        s.classify(
            modality=Modality.DB.value,
            request_intent=RequestIntent.STATE_TRANSITION.value,
            response_outcome=ResponseOutcome.AUTH_FAILURE.value,
        )
        d = s.to_dict()
        assert d["modality"] == "db"
        assert d["request_intent"] == "state_transition"
        assert d["response_outcome"] == "auth_failure"

    def test_plaid_query_rate_limited_pattern(self):
        """The key v0.2 pattern: a query that got rate_limited (not an 'error intent')."""
        s = self._make_span(target="POST /transactions/get")
        s.request(url="https://sandbox.plaid.com/transactions/get", method="POST")
        s.response(status=429, error="RATE_LIMIT_EXCEEDED")
        s.observed(what_happened="429 rate limit", what_learned="need backoff")
        s.classify(request_intent="query", response_outcome="rate_limited")
        d = s.to_dict()
        assert d["request_intent"] == "query"
        assert d["response_outcome"] == "rate_limited"
        assert d["interaction"]["response"]["status_code"] == 429

    def test_state_transition_lifecycle_start(self):
        """Stripe PaymentIntent.create pattern: lifecycle_start subtype."""
        s = self._make_span(target="POST /v1/payment_intents")
        s.classify(
            request_intent="state_transition",
            state_transition_subtype="lifecycle_start",
        )
        d = s.to_dict()
        assert d["request_intent"] == "state_transition"
        assert d["state_transition_subtype"] == "lifecycle_start"
