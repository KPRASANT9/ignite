"""Message modality profile — error taxonomy, intent classification, and ErrP overrides.

Bridge modality for cross-system chains: webhooks, event buses, pub/sub.
Without this, Web→API→DB works but GitHub→webhook→CI breaks.

Same template as DB/Web/CLI profiles.

Source: Honey Message Modality Product Spec.
"""

from __future__ import annotations


# --- Message error taxonomy ---
# 5 families: delivery, format, auth, ordering, capacity.

MSG_ERROR_CODES: dict[str, dict[str, str]] = {
    # Delivery errors — conditional retry with backoff
    "delivery_timeout": {"family": "delivery", "retry": "conditional"},
    "delivery_rejected": {"family": "delivery", "retry": "conditional"},
    "no_subscribers": {"family": "delivery", "retry": "conditional"},
    "ack_timeout": {"family": "delivery", "retry": "safe"},
    "nack_received": {"family": "delivery", "retry": "safe"},
    # Format errors — never retry (fix payload)
    "invalid_payload": {"family": "format", "retry": "never"},
    "schema_mismatch": {"family": "format", "retry": "never"},
    "deserialization_error": {"family": "format", "retry": "never"},
    "payload_too_large": {"family": "format", "retry": "never"},
    # Auth errors — never retry (re-auth required)
    "auth_failed": {"family": "auth", "retry": "never"},
    "topic_permission_denied": {"family": "auth", "retry": "never"},
    "token_expired": {"family": "auth", "retry": "never"},
    # Ordering errors — conditional retry
    "sequence_gap": {"family": "ordering", "retry": "conditional"},
    "duplicate_message": {"family": "ordering", "retry": "never"},
    "out_of_order": {"family": "ordering", "retry": "conditional"},
    # Capacity errors — unsafe (wait for recovery)
    "queue_full": {"family": "capacity", "retry": "unsafe"},
    "rate_limited": {"family": "capacity", "retry": "safe"},
    "broker_unavailable": {"family": "capacity", "retry": "unsafe"},
}


# --- ErrP correction overrides ---

MSG_ERROR_CODE_CORRECTIONS: dict[str, str] = {
    # Delivery
    "delivery_timeout": "backoff_retry",
    "delivery_rejected": "backoff_retry",
    "no_subscribers": "log_escalate",
    "ack_timeout": "backoff_retry",
    "nack_received": "backoff_retry",
    # Format — escalate (fix payload, don't retry)
    "invalid_payload": "escalate_human",
    "schema_mismatch": "escalate_human",
    "deserialization_error": "escalate_human",
    "payload_too_large": "escalate_human",
    # Auth — restart flow
    "auth_failed": "restart_flow",
    "topic_permission_denied": "escalate_human",
    "token_expired": "reauth_retry",
    # Ordering
    "sequence_gap": "backoff_retry",
    "duplicate_message": "log_escalate",
    "out_of_order": "backoff_retry",
    # Capacity
    "queue_full": "log_escalate",
    "rate_limited": "backoff_retry",
    "broker_unavailable": "log_escalate",
}


def lookup_msg_errp_override(error_code: str) -> str | None:
    """Look up message-specific ErrP correction override by error code.

    Returns CorrectionAction value string, or None if unknown error code.
    """
    return MSG_ERROR_CODE_CORRECTIONS.get(error_code)


# --- Intent classification ---

# Operations that are inbound (receiving signals)
_RECEIVE_OPERATIONS = frozenset({"receive", "subscribe", "consume", "poll"})

# Operations that are outbound (sending signals)
_PUBLISH_OPERATIONS = frozenset({"publish", "send", "emit", "enqueue", "produce"})

# Operations that are acknowledgement
_ACK_OPERATIONS = frozenset({"ack", "nack", "reject", "complete"})


def classify_msg_intent(
    operation: str | None = None,
    message_type: str | None = None,
) -> str:
    """Classify message interaction intent.

    Inbound webhooks/events are 'event_received' — a new intent class
    for signals that are neither query nor mutation. They are received signals
    that may trigger downstream actions.

    Returns RequestIntent value string.
    """
    if operation:
        op_lower = operation.lower()
        if op_lower in _RECEIVE_OPERATIONS:
            return "query"  # receiving is a read operation
        if op_lower in _PUBLISH_OPERATIONS:
            return "mutation"  # publishing has side effects
        if op_lower in _ACK_OPERATIONS:
            return "mutation"  # ack changes message state

    if message_type:
        mt_lower = message_type.lower()
        if mt_lower in ("webhook", "event", "notification"):
            return "query"  # inbound events are received, not initiated
        if mt_lower == "command":
            return "mutation"  # commands request action

    return "query"  # safe default


def classify_msg_risk(
    operation: str | None = None,
    delivery_guarantee: str | None = None,
) -> str:
    """Classify message interaction risk level.

    Publish with at_most_once is higher risk (can lose messages).
    Returns: "low", "medium", "high".
    """
    if operation:
        op_lower = operation.lower()
        if op_lower in _PUBLISH_OPERATIONS:
            if delivery_guarantee and delivery_guarantee == "at_most_once":
                return "high"  # fire-and-forget, can lose
            return "medium"  # publishing has side effects
        if op_lower in _ACK_OPERATIONS:
            return "medium"  # ack changes state

    return "low"  # receiving/subscribing is passive


# --- Structural anomaly detection ---

def is_msg_structural_anomaly(operation: str | None, error_code: str) -> bool:
    """Detect structurally impossible message errors.

    A 'subscribe' operation producing 'payload_too_large' is anomalous —
    subscribing doesn't send a payload.
    """
    if operation and operation.lower() in _RECEIVE_OPERATIONS and error_code in (
        "payload_too_large", "invalid_payload"
    ):
        # These are sender-side errors, not receiver-side
        return True
    return False
