"""Tests for M5-M10 trace accommodations: async_pending, new endpoints, rate limits, transfer FSM."""

from ignite_trace.modality import ResponseOutcome
from ignite_trace.profiles.plaid import (
    ENDPOINT_INTENTS,
    KNOWN_ENDPOINTS,
    RATE_LIMIT_ERRORS,
    RATE_LIMITS,
    TRANSFER_EVENT_TYPES,
    TRANSFER_STATES,
)
from ignite_trace.span import SpanBuilder


class TestAsyncPendingOutcome:
    """M8: PRODUCT_NOT_READY is async_pending, not error."""

    def test_async_pending_enum_exists(self):
        assert ResponseOutcome.ASYNC_PENDING.value == "async_pending"

    def test_async_pending_in_span(self):
        s = SpanBuilder(kind="api_call", target="POST /asset_report/get", sequence=1, trace_id="test")
        s.classify(request_intent="query", response_outcome="async_pending")
        s.observed(what_happened="PRODUCT_NOT_READY", what_learned="report still generating")
        d = s.to_dict()
        assert d["request_intent"] == "query"
        assert d["response_outcome"] == "async_pending"

    def test_response_outcome_count(self):
        """Seven outcomes: success, error, partial, timeout, rate_limited, auth_failure, async_pending."""
        assert len(ResponseOutcome) == 7


class TestTransferFSM:
    """M6: 7-state ACH FSM with funds_available."""

    def test_seven_states(self):
        assert len(TRANSFER_STATES) == 7

    def test_funds_available_present(self):
        assert "funds_available" in TRANSFER_STATES

    def test_debit_terminal_state(self):
        """Debit FSM terminates at funds_available (or cancelled/failed/returned)."""
        assert "funds_available" in TRANSFER_STATES
        assert "settled" in TRANSFER_STATES

    def test_transfer_event_types(self):
        """Event types include both transfer + sweep states."""
        assert "swept" in TRANSFER_EVENT_TYPES
        assert "swept_settled" in TRANSFER_EVENT_TYPES
        assert "return_swept" in TRANSFER_EVENT_TYPES
        assert len(TRANSFER_EVENT_TYPES) == 10


class TestM5M10Endpoints:
    """M5-M10 endpoints are registered with intent classifications."""

    def test_transfer_authorization_is_decision(self):
        """M6: authorization/create is a decision gate, not a mutation."""
        assert ENDPOINT_INTENTS["transfer/authorization/create"] == "decision"

    def test_transfer_event_sync_is_query(self):
        assert ENDPOINT_INTENTS["transfer/event/sync"] == "query"

    def test_asset_report_create_is_state_transition(self):
        """M8: async report create starts a lifecycle."""
        assert ENDPOINT_INTENTS["asset_report/create"] == "state_transition"

    def test_asset_report_get_is_query(self):
        assert ENDPOINT_INTENTS["asset_report/get"] == "query"

    def test_signal_decision_report_is_mutation(self):
        """M7: feedback loop — outcome report improves model."""
        assert ENDPOINT_INTENTS["signal/decision/report"] == "mutation"

    def test_idv_create_is_state_transition(self):
        """M9: IDV session creation starts multi-step flow."""
        assert ENDPOINT_INTENTS["identity_verification/create"] == "state_transition"

    def test_idv_retry_is_state_transition(self):
        """M9: retry creates a new linked session."""
        assert ENDPOINT_INTENTS["identity_verification/retry"] == "state_transition"

    def test_all_known_endpoints_have_intents(self):
        """Every endpoint in KNOWN_ENDPOINTS must have an intent classification."""
        for endpoint in KNOWN_ENDPOINTS:
            assert endpoint in ENDPOINT_INTENTS, f"Missing intent for endpoint: {endpoint}"

    def test_all_intents_are_valid(self):
        """All intent values must be valid RequestIntent enum values."""
        valid_intents = {"state_transition", "query", "mutation", "config_change", "decision"}
        for endpoint, intent in ENDPOINT_INTENTS.items():
            assert intent in valid_intents, f"Invalid intent '{intent}' for {endpoint}"


class TestRateLimits:
    """M10: Per-endpoint rate limit thresholds."""

    def test_balance_most_restrictive(self):
        """accounts/balance/get is 5/min per item — most restrictive."""
        assert RATE_LIMITS["accounts/balance/get"]["per_item_min"] == 5

    def test_transactions_sync_most_generous(self):
        """transactions/sync is 50/min per item — most generous read."""
        assert RATE_LIMITS["transactions/sync"]["per_item_min"] == 50

    def test_transactions_refresh_has_daily_limit(self):
        """transactions/refresh has per_item_day limit (2880)."""
        assert RATE_LIMITS["transactions/refresh"]["per_item_day"] == 2880

    def test_empty_cursor_penalty(self):
        """transactions/sync has lower client limit for empty cursor calls."""
        assert RATE_LIMITS["transactions/sync"]["per_client_min_empty_cursor"] == 500
        assert RATE_LIMITS["transactions/sync"]["per_client_min"] == 2500

    def test_rate_limit_error_codes(self):
        """12 specific rate limit error codes are mapped."""
        assert len(RATE_LIMIT_ERRORS) == 12

    def test_known_limits_have_error_codes(self):
        """Every endpoint with rate limits has a corresponding error code."""
        for error_code, endpoint in RATE_LIMIT_ERRORS.items():
            if endpoint is not None:
                assert endpoint in RATE_LIMITS, f"Error {error_code} maps to {endpoint} but no limit defined"


class TestTraceArchetypePatterns:
    """M5-M10 validated four trace archetypes — test that spans can express each."""

    def test_sync_request_response(self):
        """Archetype 1: simple query."""
        s = SpanBuilder(kind="api_call", target="POST /transactions/get", sequence=1, trace_id="t")
        s.classify(request_intent="query", response_outcome="success")
        s.observed(what_happened="h", what_learned="l")
        d = s.to_dict()
        assert d["request_intent"] == "query"

    def test_fsm_lifecycle(self):
        """Archetype 2: transfer creation starts FSM lifecycle."""
        s = SpanBuilder(kind="api_call", target="POST /transfer/create", sequence=1, trace_id="t")
        s.classify(
            request_intent="state_transition",
            response_outcome="success",
            state_transition_subtype="lifecycle_start",
        )
        s.observed(what_happened="h", what_learned="l")
        d = s.to_dict()
        assert d["request_intent"] == "state_transition"
        assert d["state_transition_subtype"] == "lifecycle_start"

    def test_async_report(self):
        """Archetype 3: asset report polling returns async_pending."""
        s = SpanBuilder(kind="api_call", target="POST /asset_report/get", sequence=1, trace_id="t")
        s.classify(request_intent="query", response_outcome="async_pending")
        s.observed(what_happened="PRODUCT_NOT_READY", what_learned="report generating")
        d = s.to_dict()
        assert d["response_outcome"] == "async_pending"

    def test_decision_gate(self):
        """Archetype pattern: transfer authorization is a decision gate."""
        s = SpanBuilder(kind="api_call", target="POST /transfer/authorization/create", sequence=1, trace_id="t")
        s.classify(request_intent="decision", response_outcome="success")
        s.observed(what_happened="approved", what_learned="NSF check passed")
        d = s.to_dict()
        assert d["request_intent"] == "decision"

    def test_rate_limited_errp_loop(self):
        """M10: rate limit → backoff → retry pattern for ErrP demo."""
        s = SpanBuilder(kind="api_call", target="POST /accounts/get", sequence=1, trace_id="t")
        s.classify(request_intent="query", response_outcome="rate_limited")
        s.observed(what_happened="429 ACCOUNTS_LIMIT", what_learned="exceeded 15/min per item")
        d = s.to_dict()
        assert d["request_intent"] == "query"
        assert d["response_outcome"] == "rate_limited"
