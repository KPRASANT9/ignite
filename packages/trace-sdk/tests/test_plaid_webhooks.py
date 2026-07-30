"""Tests for Plaid webhook classification profile.

Validates that webhook_type x webhook_code pairs map correctly to
v0.2 request_intent and response_outcome values.
"""

from __future__ import annotations

import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).parent.parent / "src"))

from ignite_trace.profiles.plaid_webhooks import classify_webhook, WEBHOOK_INTENTS


class TestClassifyWebhook:
    def test_item_error(self):
        result = classify_webhook("ITEM", "ERROR")
        assert result is not None
        assert result["request_intent"] == "state_transition"
        assert result["response_outcome"] == "expected_error"

    def test_item_login_repaired(self):
        result = classify_webhook("ITEM", "LOGIN_REPAIRED")
        assert result is not None
        assert result["request_intent"] == "state_transition"
        assert result["response_outcome"] == "success"

    def test_transactions_default_update(self):
        result = classify_webhook("TRANSACTIONS", "DEFAULT_UPDATE")
        assert result is not None
        assert result["request_intent"] == "query"
        assert result["response_outcome"] == "success"

    def test_transactions_removed_is_mutation(self):
        result = classify_webhook("TRANSACTIONS", "TRANSACTIONS_REMOVED")
        assert result is not None
        assert result["request_intent"] == "mutation"

    def test_acknowledged_is_config_change(self):
        result = classify_webhook("ITEM", "WEBHOOK_UPDATE_ACKNOWLEDGED")
        assert result is not None
        assert result["request_intent"] == "config_change"

    def test_auth_verified_is_config_change(self):
        result = classify_webhook("AUTH", "AUTOMATICALLY_VERIFIED")
        assert result is not None
        assert result["request_intent"] == "config_change"

    def test_transfer_is_state_transition(self):
        result = classify_webhook("TRANSFER", "TRANSFER_EVENTS_UPDATE")
        assert result is not None
        assert result["request_intent"] == "state_transition"

    def test_unknown_returns_none(self):
        assert classify_webhook("UNKNOWN", "UNKNOWN") is None

    def test_all_entries_have_required_keys(self):
        for key, value in WEBHOOK_INTENTS.items():
            assert "request_intent" in value, f"Missing request_intent for {key}"
            assert "response_outcome" in value, f"Missing response_outcome for {key}"

    def test_state_transition_webhooks_count(self):
        st_count = sum(
            1 for v in WEBHOOK_INTENTS.values()
            if v["request_intent"] == "state_transition"
        )
        assert st_count >= 8  # ITEM/ERROR, LOGIN_REPAIRED, PENDING_*, USER_*, TRANSFER, AUTH/expired, ASSETS/ERROR

    def test_query_webhooks_count(self):
        q_count = sum(
            1 for v in WEBHOOK_INTENTS.values()
            if v["request_intent"] == "query"
        )
        assert q_count >= 9  # TRANSACTIONS/*, HOLDINGS, INVESTMENTS, AUTH/DEFAULT, INCOME/*, ASSETS/READY
