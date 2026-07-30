"""Message/event bus modality extension — async messaging interactions.

Captures message-specific interaction details: topics, queues, event types,
delivery semantics. Used when agents interact with message brokers,
event buses, webhooks, or pub/sub systems.

Privacy: payload content may contain PII — apply same scrubbing as Web/CLI
before setting payload_schema or any content fields.
"""

from __future__ import annotations

from dataclasses import dataclass, field
from typing import Any


@dataclass
class MsgExt:
    """Typed message/event interaction extension."""
    # P0 fields
    system: str | None = None           # e.g., "nats", "kafka", "rabbitmq", "webhook", "github"
    operation: str | None = None        # "publish", "subscribe", "receive", "ack", "nack"
    topic: str | None = None            # Topic/subject/queue name
    message_type: str | None = None     # e.g., "webhook", "event", "command", "notification"
    delivery_guarantee: str | None = None  # "at_most_once", "at_least_once", "exactly_once"
    # P1 fields
    message_id: str | None = None       # Message identifier
    correlation_id: str | None = None   # For request-reply patterns
    content_type: str | None = None     # e.g., "application/json"
    payload_size: int | None = None     # bytes
    payload_schema: str | None = None   # JSON schema fingerprint of payload structure
    delivery_attempt: int | None = None # Which delivery attempt (1 = first, 2+ = retry)
    sequence_id: str | None = None      # Ordering sequence ID
    source_system: str | None = None    # Origin system (e.g., "github", "plaid", "stripe")
    delivery: str | None = None         # Legacy alias for delivery_guarantee
    consumer_group: str | None = None   # Consumer group identifier
    # P2 fields
    idempotency_key: str | None = None  # Dedup key for exactly-once processing
    # Webhook-specific fields
    event_type: str | None = None       # e.g., "ITEM", "TRANSACTIONS", "AUTH"
    webhook_code: str | None = None     # e.g., "ERROR", "DEFAULT_UPDATE", "LOGIN_REPAIRED"
    ordering: str | None = None         # "ordered", "unordered"
    replay_support: bool | None = None  # Whether replay/re-delivery is available
    retry_window_hours: int | None = None  # Retry window before event is dropped

    def to_dict(self) -> dict[str, Any]:
        d: dict[str, Any] = {"type": "msg_ext"}
        if self.system is not None:
            d["system"] = self.system
        if self.operation is not None:
            d["operation"] = self.operation
        if self.topic is not None:
            d["topic"] = self.topic
        if self.message_type is not None:
            d["message_type"] = self.message_type
        if self.delivery_guarantee is not None:
            d["delivery_guarantee"] = self.delivery_guarantee
        if self.message_id is not None:
            d["message_id"] = self.message_id
        if self.correlation_id is not None:
            d["correlation_id"] = self.correlation_id
        if self.content_type is not None:
            d["content_type"] = self.content_type
        if self.payload_size is not None:
            d["payload_size"] = self.payload_size
        if self.payload_schema is not None:
            d["payload_schema"] = self.payload_schema
        if self.delivery_attempt is not None:
            d["delivery_attempt"] = self.delivery_attempt
        if self.sequence_id is not None:
            d["sequence_id"] = self.sequence_id
        if self.source_system is not None:
            d["source_system"] = self.source_system
        if self.delivery is not None:
            d["delivery"] = self.delivery
        if self.consumer_group is not None:
            d["consumer_group"] = self.consumer_group
        if self.idempotency_key is not None:
            d["idempotency_key"] = self.idempotency_key
        if self.event_type is not None:
            d["event_type"] = self.event_type
        if self.webhook_code is not None:
            d["webhook_code"] = self.webhook_code
        if self.ordering is not None:
            d["ordering"] = self.ordering
        if self.replay_support is not None:
            d["replay_support"] = self.replay_support
        if self.retry_window_hours is not None:
            d["retry_window_hours"] = self.retry_window_hours
        return d

    @classmethod
    def from_dict(cls, data: dict[str, Any]) -> MsgExt:
        return cls(
            system=data.get("system"),
            operation=data.get("operation"),
            topic=data.get("topic"),
            message_type=data.get("message_type"),
            delivery_guarantee=data.get("delivery_guarantee"),
            message_id=data.get("message_id"),
            correlation_id=data.get("correlation_id"),
            content_type=data.get("content_type"),
            payload_size=data.get("payload_size"),
            payload_schema=data.get("payload_schema"),
            delivery_attempt=data.get("delivery_attempt"),
            sequence_id=data.get("sequence_id"),
            source_system=data.get("source_system"),
            delivery=data.get("delivery"),
            consumer_group=data.get("consumer_group"),
            idempotency_key=data.get("idempotency_key"),
            event_type=data.get("event_type"),
            webhook_code=data.get("webhook_code"),
            ordering=data.get("ordering"),
            replay_support=data.get("replay_support"),
            retry_window_hours=data.get("retry_window_hours"),
        )
