"""Produce M9 trace: Message/webhook interactions — Plaid webhook lifecycle.

This trace validates message modality processing through the pipeline.
Models the Plaid webhook lifecycle: receive ITEM webhook, verify, process
TRANSACTIONS update, and acknowledge completion.

Modality: Message (primary).
Session structure: conversational (webhook receive → process → ack pattern).
"""

import sys
sys.path.insert(0, "packages/trace-sdk/src")
sys.path.insert(0, "packages/parser/src")

from ignite_trace import (
    TraceSession,
    Modality,
    RequestIntent,
    ResponseOutcome,
    SignalClass,
    DeltaFromPrior,
    MsgExt,
)

with TraceSession(
    agent="Bumble",
    system="plaid-webhooks",
    objective="Validate pipeline processes message modality traces end-to-end — Plaid webhook lifecycle: ITEM event, TRANSACTIONS update, acknowledgment flow",
    agent_role="explorer",
    study_channel="e833bff9-f27f-4039-80ed-fe7f38034ee6",
    output_dir="traces/explorer",
) as trace:

    # --- Span 1: Receive ITEM webhook ---
    with trace.span("action_result", target="Receive ITEM webhook") as s1:
        s1.classify(
            modality=Modality.MESSAGE.value,
            request_intent=RequestIntent.STATE_TRANSITION.value,
            response_outcome=ResponseOutcome.SUCCESS.value,
            signal_class=SignalClass.INTENT_CARRYING.value,
            delta_from_prior=DeltaFromPrior.FULL.value,
        )
        s1.structure("conversational")
        s1.modality_ext(MsgExt(
            system="webhook",
            operation="receive",
            topic="ITEM",
            content_type="application/json",
            delivery="at_least_once",
            event_type="ITEM",
            webhook_code="ERROR",
            ordering="unordered",
            replay_support=False,
            retry_window_hours=24,
        ))
        s1.observed(
            what_happened="Received Plaid ITEM webhook with code ERROR. Item ID ins_12345 has entered error state — requires user re-authentication via Link update mode.",
            what_learned="Webhooks are state_transition events — they signal that an external system's state changed. ITEM ERROR means the institution connection is broken. The webhook_code provides the specific error type. Delivery is at_least_once (may receive duplicates — need idempotency).",
            confidence="high",
        )
        s1.state_change("item state: healthy → error")
        s1.tag("message", "webhook", "plaid", "item-error")

    # --- Span 2: Verify webhook signature ---
    with trace.span("action_result", target="Verify webhook signature") as s2:
        s2.depends_on(s1.span_id)
        s2.classify(
            modality=Modality.MESSAGE.value,
            request_intent=RequestIntent.QUERY.value,
            response_outcome=ResponseOutcome.SUCCESS.value,
            signal_class=SignalClass.INTENT_CARRYING.value,
            delta_from_prior=DeltaFromPrior.PARTIAL.value,
        )
        s2.structure("conversational")
        s2.modality_ext(MsgExt(
            system="webhook",
            operation="receive",
            topic="ITEM",
            content_type="application/json",
        ))
        s2.observed(
            what_happened="Verified webhook JWT signature against Plaid's public key. Signature valid — webhook is authentic.",
            what_learned="Webhook verification is a QUERY — reading and validating the signature without changing state. This is a critical security step: without verification, an attacker could forge webhooks to trigger unauthorized actions.",
            confidence="high",
        )
        s2.tag("message", "webhook", "verification", "security")

    # --- Span 3: Receive TRANSACTIONS webhook ---
    with trace.span("action_result", target="Receive TRANSACTIONS webhook") as s3:
        s3.depends_on(s2.span_id)
        s3.classify(
            modality=Modality.MESSAGE.value,
            request_intent=RequestIntent.STATE_TRANSITION.value,
            response_outcome=ResponseOutcome.SUCCESS.value,
            signal_class=SignalClass.INTENT_CARRYING.value,
            delta_from_prior=DeltaFromPrior.FULL.value,
        )
        s3.structure("conversational")
        s3.modality_ext(MsgExt(
            system="webhook",
            operation="receive",
            topic="TRANSACTIONS",
            content_type="application/json",
            delivery="at_least_once",
            event_type="TRANSACTIONS",
            webhook_code="DEFAULT_UPDATE",
            ordering="unordered",
            replay_support=False,
            retry_window_hours=24,
        ))
        s3.observed(
            what_happened="Received TRANSACTIONS DEFAULT_UPDATE webhook. 47 new transactions available for item ins_67890. Need to call /transactions/sync to fetch them.",
            what_learned="TRANSACTIONS webhooks are the primary trigger for data sync. DEFAULT_UPDATE means new data is available — the webhook is a notification, not the data itself. The actual transaction data requires a follow-up API call (/transactions/sync). This is a cross-modality handoff: message → API.",
            confidence="high",
        )
        s3.state_change("transactions: stale → new_data_available (47 pending)")
        s3.tag("message", "webhook", "plaid", "transactions-update")

    # --- Span 4: Process and acknowledge ---
    with trace.span("action_result", target="Process webhook and acknowledge") as s4:
        s4.depends_on(s3.span_id)
        s4.classify(
            modality=Modality.MESSAGE.value,
            request_intent=RequestIntent.MUTATION.value,
            response_outcome=ResponseOutcome.SUCCESS.value,
            signal_class=SignalClass.INTENT_CARRYING.value,
            delta_from_prior=DeltaFromPrior.FULL.value,
        )
        s4.structure("conversational")
        s4.modality_ext(MsgExt(
            system="webhook",
            operation="ack",
            topic="TRANSACTIONS",
            content_type="application/json",
        ))
        s4.observed(
            what_happened="Processed TRANSACTIONS webhook: enqueued sync job for item ins_67890, responded 200 OK to Plaid within 2.1s (well within 10s timeout). Webhook acknowledged.",
            what_learned="Webhook acknowledgment is a MUTATION — it changes the processing state (pending → processed) and responds to the sender. Must respond quickly (Plaid expects <10s) or the webhook will be retried. The ack operation is the message-modality equivalent of an API response.",
            confidence="high",
        )
        s4.state_change("webhook: received → acknowledged")
        s4.tag("message", "webhook", "ack", "mutation")

    trace.finding(
        category="trace_surface",
        title="Message modality validates through pipeline — conversational webhook lifecycle",
        description="4-span message trace demonstrates conversational span_structure, webhook-specific MsgExt fields (event_type, webhook_code, ordering, replay_support), and the receive→verify→process→ack pattern. Webhooks map cleanly to intent/outcome classification.",
        source_spans=[s1, s2, s3, s4],
        confidence="confirmed",
        actionability="informational",
    )

print("M9 message trace produced in traces/explorer/")
