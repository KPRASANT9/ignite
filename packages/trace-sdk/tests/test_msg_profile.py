"""Tests for Message modality profile — error taxonomy, intent/risk classification."""

from __future__ import annotations

import sys
from pathlib import Path

import pytest

sys.path.insert(0, str(Path(__file__).parent.parent / "src"))

from ignite_trace.profiles.message import (
    classify_msg_intent,
    classify_msg_risk,
    is_msg_structural_anomaly,
    lookup_msg_errp_override,
    MSG_ERROR_CODES,
    MSG_ERROR_CODE_CORRECTIONS,
)
from ignite_trace.extensions.msg_ext import MsgExt


# --- Error taxonomy ---


class TestMsgErrorTaxonomy:
    def test_all_error_codes_have_corrections(self):
        for code in MSG_ERROR_CODES:
            assert code in MSG_ERROR_CODE_CORRECTIONS, f"Missing correction for {code}"

    def test_broker_unavailable_log_escalate(self):
        assert MSG_ERROR_CODES["broker_unavailable"]["retry"] == "unsafe"
        assert lookup_msg_errp_override("broker_unavailable") == "log_escalate"

    def test_delivery_timeout_backoff(self):
        assert lookup_msg_errp_override("delivery_timeout") == "backoff_retry"

    def test_invalid_payload_never_retry(self):
        assert MSG_ERROR_CODES["invalid_payload"]["retry"] == "never"
        assert lookup_msg_errp_override("invalid_payload") == "escalate_human"

    def test_token_expired_reauth(self):
        assert lookup_msg_errp_override("token_expired") == "reauth_retry"

    def test_duplicate_message_log(self):
        assert MSG_ERROR_CODES["duplicate_message"]["retry"] == "never"
        assert lookup_msg_errp_override("duplicate_message") == "log_escalate"

    def test_rate_limited_backoff(self):
        assert lookup_msg_errp_override("rate_limited") == "backoff_retry"

    def test_unknown_returns_none(self):
        assert lookup_msg_errp_override("unknown") is None


# --- Intent classification ---


class TestMsgIntentClassification:
    def test_receive_is_query(self):
        assert classify_msg_intent(operation="receive") == "query"

    def test_subscribe_is_query(self):
        assert classify_msg_intent(operation="subscribe") == "query"

    def test_publish_is_mutation(self):
        assert classify_msg_intent(operation="publish") == "mutation"

    def test_ack_is_mutation(self):
        assert classify_msg_intent(operation="ack") == "mutation"

    def test_webhook_type_is_query(self):
        assert classify_msg_intent(message_type="webhook") == "query"

    def test_command_type_is_mutation(self):
        assert classify_msg_intent(message_type="command") == "mutation"

    def test_event_type_is_query(self):
        assert classify_msg_intent(message_type="event") == "query"

    def test_default_is_query(self):
        assert classify_msg_intent() == "query"


# --- Risk classification ---


class TestMsgRiskClassification:
    def test_publish_at_most_once_high(self):
        assert classify_msg_risk(operation="publish", delivery_guarantee="at_most_once") == "high"

    def test_publish_at_least_once_medium(self):
        assert classify_msg_risk(operation="publish", delivery_guarantee="at_least_once") == "medium"

    def test_ack_medium(self):
        assert classify_msg_risk(operation="ack") == "medium"

    def test_receive_low(self):
        assert classify_msg_risk(operation="receive") == "low"

    def test_subscribe_low(self):
        assert classify_msg_risk(operation="subscribe") == "low"

    def test_default_low(self):
        assert classify_msg_risk() == "low"


# --- Structural anomaly ---


class TestMsgStructuralAnomaly:
    def test_receive_with_payload_too_large(self):
        assert is_msg_structural_anomaly("receive", "payload_too_large") is True

    def test_publish_with_payload_too_large_not_anomalous(self):
        assert is_msg_structural_anomaly("publish", "payload_too_large") is False

    def test_subscribe_with_invalid_payload(self):
        assert is_msg_structural_anomaly("subscribe", "invalid_payload") is True


# --- MsgExt roundtrip with new fields ---


class TestMsgExtEnhanced:
    def test_p0_fields_roundtrip(self):
        original = MsgExt(
            system="nats",
            operation="publish",
            topic="ci.trigger",
            message_type="command",
            delivery_guarantee="at_least_once",
        )
        d = original.to_dict()
        restored = MsgExt.from_dict(d)
        assert restored.message_type == "command"
        assert restored.delivery_guarantee == "at_least_once"

    def test_p1_fields_roundtrip(self):
        original = MsgExt(
            system="webhook",
            operation="receive",
            payload_schema="github-push-v1",
            delivery_attempt=2,
            sequence_id="seq-042",
            source_system="github",
        )
        d = original.to_dict()
        restored = MsgExt.from_dict(d)
        assert restored.payload_schema == "github-push-v1"
        assert restored.delivery_attempt == 2
        assert restored.sequence_id == "seq-042"
        assert restored.source_system == "github"

    def test_p2_idempotency_key(self):
        original = MsgExt(
            system="nats",
            operation="publish",
            idempotency_key="dedup-key-123",
        )
        d = original.to_dict()
        assert d["idempotency_key"] == "dedup-key-123"
        restored = MsgExt.from_dict(d)
        assert restored.idempotency_key == "dedup-key-123"

    def test_backward_compat_delivery_alias(self):
        """Old 'delivery' field still works alongside new 'delivery_guarantee'."""
        original = MsgExt(delivery="at_least_once")
        d = original.to_dict()
        assert d["delivery"] == "at_least_once"
