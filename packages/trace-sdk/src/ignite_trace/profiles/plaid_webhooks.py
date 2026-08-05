"""Plaid webhook classification profile — maps webhook events to v0.2 intents.

Extends the per-endpoint classification in plaid.py to async webhook events.
Same mechanism, new modality: webhook_type x webhook_code -> request_intent.

Source: M5 trace (trace-c7dd65d1ed68), validated by EngineeringModeller
model-eng-crossmodal-005. 11 webhook_type x code combinations mapped,
0 ambiguous classifications.
"""

from __future__ import annotations

from typing import Dict, Optional, Tuple

# v0.2: Per-webhook-event intent classification.
# webhook_type x webhook_code -> (request_intent, default_response_outcome).
# The SDK auto-classifies MESSAGE modality spans using this lookup.
WEBHOOK_INTENTS: Dict[Tuple[str, str], Dict[str, str]] = {
    # ITEM webhooks — state_transition (require app action: recovery, consent)
    ("ITEM", "ERROR"): {
        "request_intent": "state_transition",
        "response_outcome": "expected_error",
    },
    ("ITEM", "LOGIN_REPAIRED"): {
        "request_intent": "state_transition",
        "response_outcome": "success",
    },
    ("ITEM", "PENDING_EXPIRATION"): {
        "request_intent": "state_transition",
        "response_outcome": "success",
    },
    ("ITEM", "PENDING_DISCONNECT"): {
        "request_intent": "state_transition",
        "response_outcome": "success",
    },
    ("ITEM", "USER_PERMISSION_REVOKED"): {
        "request_intent": "state_transition",
        "response_outcome": "success",
    },
    ("ITEM", "USER_ACCOUNT_REVOKED"): {
        "request_intent": "state_transition",
        "response_outcome": "success",
    },
    # ITEM webhooks — config_change (system config confirmed)
    ("ITEM", "WEBHOOK_UPDATE_ACKNOWLEDGED"): {
        "request_intent": "config_change",
        "response_outcome": "success",
    },
    ("ITEM", "NEW_ACCOUNTS_AVAILABLE"): {
        "request_intent": "config_change",
        "response_outcome": "success",
    },
    # TRANSACTIONS webhooks — query (data-ready notifications, trigger sync)
    ("TRANSACTIONS", "INITIAL_UPDATE"): {
        "request_intent": "query",
        "response_outcome": "success",
    },
    ("TRANSACTIONS", "HISTORICAL_UPDATE"): {
        "request_intent": "query",
        "response_outcome": "success",
    },
    ("TRANSACTIONS", "DEFAULT_UPDATE"): {
        "request_intent": "query",
        "response_outcome": "success",
    },
    ("TRANSACTIONS", "SYNC_UPDATES_AVAILABLE"): {
        "request_intent": "query",
        "response_outcome": "success",
    },
    ("TRANSACTIONS", "TRANSACTIONS_REMOVED"): {
        "request_intent": "mutation",
        "response_outcome": "success",
    },
    # HOLDINGS webhooks — query
    ("HOLDINGS", "DEFAULT_UPDATE"): {
        "request_intent": "query",
        "response_outcome": "success",
    },
    # INVESTMENTS_TRANSACTIONS webhooks — query
    ("INVESTMENTS_TRANSACTIONS", "DEFAULT_UPDATE"): {
        "request_intent": "query",
        "response_outcome": "success",
    },
    # AUTH webhooks
    ("AUTH", "AUTOMATICALLY_VERIFIED"): {
        "request_intent": "config_change",
        "response_outcome": "success",
    },
    ("AUTH", "VERIFICATION_EXPIRED"): {
        "request_intent": "state_transition",
        "response_outcome": "expected_error",
    },
    ("AUTH", "DEFAULT_UPDATE"): {
        "request_intent": "query",
        "response_outcome": "success",
    },
    # TRANSFER webhooks — state_transition
    ("TRANSFER", "TRANSFER_EVENTS_UPDATE"): {
        "request_intent": "state_transition",
        "response_outcome": "success",
    },
    # INCOME webhooks — query
    ("INCOME", "INCOME_VERIFICATION"): {
        "request_intent": "query",
        "response_outcome": "success",
    },
    ("INCOME", "INCOME_VERIFICATION_RISK_SIGNALS"): {
        "request_intent": "query",
        "response_outcome": "success",
    },
    # ASSETS webhooks
    ("ASSETS", "PRODUCT_READY"): {
        "request_intent": "query",
        "response_outcome": "success",
    },
    ("ASSETS", "ERROR"): {
        "request_intent": "state_transition",
        "response_outcome": "expected_error",
    },
}


def classify_webhook(
    webhook_type: str,
    webhook_code: str,
) -> Optional[Dict[str, str]]:
    """Look up v0.2 classification for a Plaid webhook event.

    Returns dict with request_intent and response_outcome if known,
    None if the webhook_type x webhook_code pair is not in the profile.
    The response_outcome is a default — callers may override based on
    actual processing result.
    """
    return WEBHOOK_INTENTS.get((webhook_type, webhook_code))
