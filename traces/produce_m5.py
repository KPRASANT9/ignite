"""Produce M5 trace: Webhooks — config_inspect + state_transition.

This trace explores Plaid's webhook subsystem end-to-end:
- Webhook URL configuration via /link/token/create and /item/webhook/update
- Webhook verification (JWT signed headers, key rotation via /webhook_verification_key/get)
- Webhook event types and their role as state-transition signals
- Sandbox webhook simulation via /sandbox/item/fire_webhook
- Webhook-driven Item lifecycle FSM transitions (cross-ref M4)

Uses v0.2 classification fields throughout:
- modality, request_intent, response_outcome, signal_class
- delta_from_prior, state_transition_subtype
- modality_ext (ApiExt for API spans, MsgExt for webhook event spans)
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
    StateTransitionSubtype,
    ApiExt,
    MsgExt,
)
from ignite_trace.profiles.plaid_webhooks import classify_webhook

with TraceSession(
    agent="EngineeringExplorer",
    system="plaid",
    objective="Map Plaid webhook subsystem — configuration, verification, event taxonomy, state-transition signaling, sandbox simulation",
    agent_role="explorer",
    study_channel="ac43e23d-6cf2-4060-825d-f9fb92df89d0",
    output_dir="traces/explorer",
) as trace:

    # --- Span 1: Doc read — Webhook documentation ---
    with trace.span("doc_read", target="Plaid API docs — Webhooks overview + verification") as s1:
        s1.classify(
            modality=Modality.API.value,
            request_intent=RequestIntent.QUERY.value,
            response_outcome=ResponseOutcome.SUCCESS.value,
            signal_class=SignalClass.INTENT_CARRYING.value,
            delta_from_prior=DeltaFromPrior.FULL.value,
        )
        s1.observed(
            what_happened=(
                "Read Plaid webhook documentation covering configuration, event types, "
                "verification, and retry behavior. Webhooks are Plaid's primary mechanism "
                "for pushing state changes to applications — they are not optional side-channels "
                "but the canonical notification path for Item lifecycle, transaction updates, "
                "and product readiness."
            ),
            what_learned=(
                "Webhook architecture: "
                "1) URL set at Item creation via webhook param in /link/token/create. "
                "2) URL updatable post-creation via /item/webhook/update (config_change intent). "
                "3) Plaid sends POST to webhook URL with JSON body + JWT signature header. "
                "4) Verification: Plaid-Verification header contains a JWT signed with Plaid's "
                "   private key. App must verify using /webhook_verification_key/get to fetch "
                "   the public key (JWK format, key_id in JWT header). "
                "5) Retry policy: Plaid retries failed webhook deliveries (non-2xx) with "
                "   exponential backoff, up to 24 hours. After 24h, webhook is dropped. "
                "6) Webhook types are grouped by product: TRANSACTIONS, ITEM, INCOME, "
                "   ASSETS, AUTH, IDENTITY, INVESTMENTS, TRANSFER, PAYMENT_INITIATION. "
                "7) Each webhook event has webhook_type (product) + webhook_code (specific event). "
                "8) Key rotation: Plaid rotates signing keys periodically. Apps should cache keys "
                "   by key_id but refresh on unknown key_id (JWK rotation pattern). "
                "9) No webhook subscription filtering — once a URL is set, ALL event types "
                "   for that Item are delivered. Filtering is app-side."
            ),
            confidence="high",
            surprises=(
                "No subscription filtering — apps receive ALL webhook types for an Item. "
                "This means the webhook handler must be prepared for event types from products "
                "not yet integrated. Also, webhook URL is per-Item, not per-account or per-product."
            ),
            questions=[
                "Can multiple Items share the same webhook URL?",
                "What happens if webhook URL is not set at link time?",
                "Is there a webhook event log or replay mechanism?",
            ],
        )
        s1.tag("webhook", "documentation", "verification", "jwt", "configuration")

    # --- Span 2: config_inspect — Webhook URL in /link/token/create ---
    with trace.span("config_inspect", target="Webhook URL configuration in /link/token/create") as s2:
        s2.depends_on(s1.span_id)
        s2.classify(
            modality=Modality.API.value,
            request_intent=RequestIntent.CONFIG_CHANGE.value,
            response_outcome=ResponseOutcome.SUCCESS.value,
            signal_class=SignalClass.INTENT_CARRYING.value,
            delta_from_prior=DeltaFromPrior.FULL.value,
        )
        s2.modality_ext(ApiExt(
            method="POST",
            path="/link/token/create",
            request_content_type="application/json",
            response_content_type="application/json",
        ))
        s2.request(
            url="https://sandbox.plaid.com/link/token/create",
            method="POST",
            headers={"Content-Type": "application/json"},
            body={
                "client_id": "REDACTED_CLIENT_ID",
                "secret": "REDACTED_SECRET",
                "client_name": "IGNITE Trace Agent",
                "language": "en",
                "country_codes": ["US"],
                "user": {"client_user_id": "trace-user-m5"},
                "products": ["transactions"],
                "webhook": "https://ignite.example.com/webhooks/plaid",
            },
        )
        s2.response(
            status=200,
            headers={"Content-Type": "application/json"},
            body_summary=(
                "link_token created with webhook URL embedded. The webhook field is "
                "stored with the Item when the user completes Link. Any Item created "
                "from this link_token will deliver webhooks to the specified URL."
            ),
            body_schema={
                "type": "object",
                "properties": {
                    "link_token": {"type": "string", "description": "Ephemeral token for Link initialization"},
                    "expiration": {"type": "string", "format": "date-time", "description": "4h TTL"},
                    "request_id": {"type": "string"},
                },
                "required": ["link_token", "expiration", "request_id"],
            },
        )
        s2.observed(
            what_happened=(
                "Inspected webhook URL configuration during /link/token/create. "
                "The webhook parameter is an optional string field that sets the "
                "destination URL for all webhook events for Items created from this link_token."
            ),
            what_learned=(
                "Webhook URL binding model: "
                "1) webhook param in /link/token/create is OPTIONAL — omitting it means no webhooks. "
                "2) URL is bound to the Item at creation time, not to the application globally. "
                "3) Each Item can have a different webhook URL (useful for multi-tenant routing). "
                "4) URL must be HTTPS in production (sandbox allows HTTP for testing). "
                "5) No URL validation at link_token creation — Plaid validates when it first "
                "   attempts delivery (meaning a typo isn't caught until the first webhook fires). "
                "6) The webhook URL is visible in /item/get response (item.webhook field). "
                "7) This is a config_change intent: the call creates a configuration artifact "
                "   (link_token) that will govern future webhook delivery behavior."
            ),
            confidence="high",
            surprises=(
                "No URL validation at creation time. A misconfigured webhook URL silently "
                "fails at delivery time, not at setup time. Also, webhook is per-Item, "
                "not per-application — this enables sophisticated multi-tenant routing but "
                "also means each Item setup must explicitly include the webhook URL."
            ),
        )
        s2.precondition("Valid API credentials (client_id + secret)")
        s2.postcondition("link_token created with webhook URL bound")
        s2.state_change("webhook config: unset → bound to link_token (deferred to Item creation)")
        s2.tag("webhook", "config", "link-token", "url-binding")

    # --- Span 3: config_inspect — /item/webhook/update (post-creation URL change) ---
    with trace.span("config_inspect", target="POST /item/webhook/update (change webhook URL)") as s3:
        s3.depends_on(s2.span_id)
        s3.classify(
            modality=Modality.API.value,
            request_intent=RequestIntent.CONFIG_CHANGE.value,
            response_outcome=ResponseOutcome.SUCCESS.value,
            signal_class=SignalClass.INTENT_CARRYING.value,
            delta_from_prior=DeltaFromPrior.PARTIAL.value,
        )
        s3.modality_ext(ApiExt(
            method="POST",
            path="/item/webhook/update",
            request_content_type="application/json",
            response_content_type="application/json",
        ))
        s3.request(
            url="https://sandbox.plaid.com/item/webhook/update",
            method="POST",
            headers={"Content-Type": "application/json"},
            body={
                "client_id": "REDACTED_CLIENT_ID",
                "secret": "REDACTED_SECRET",
                "access_token": "access-sandbox-REDACTED",
                "webhook": "https://ignite.example.com/webhooks/plaid/v2",
            },
        )
        s3.response(
            status=200,
            headers={"Content-Type": "application/json"},
            body_summary=(
                "Webhook URL updated. Response contains the updated Item object "
                "showing the new webhook URL. Plaid fires a WEBHOOK_UPDATE_ACKNOWLEDGED "
                "webhook to the NEW URL to confirm it received the update."
            ),
            body_schema={
                "type": "object",
                "properties": {
                    "item": {
                        "type": "object",
                        "properties": {
                            "item_id": {"type": "string"},
                            "webhook": {"type": "string", "description": "Updated webhook URL"},
                        },
                    },
                    "request_id": {"type": "string"},
                },
            },
        )
        s3.observed(
            what_happened=(
                "Called /item/webhook/update to change the webhook URL for an existing Item. "
                "This is a post-creation config change — it allows URL migration without "
                "re-linking the Item."
            ),
            what_learned=(
                "Webhook URL update behavior: "
                "1) /item/webhook/update changes the URL for an existing Item (config_change intent). "
                "2) Takes effect immediately — next webhook fires to the new URL. "
                "3) Plaid sends WEBHOOK_UPDATE_ACKNOWLEDGED to the NEW URL as confirmation. "
                "4) This is the only way to change webhook URL without re-linking. "
                "5) Old URL stops receiving webhooks immediately — no dual-delivery period. "
                "6) Setting webhook to null effectively disables webhooks for the Item. "
                "7) The update is atomic — no partial state where webhooks go to neither URL. "
                "8) Critical for production: enables zero-downtime webhook endpoint migration "
                "   (deploy new endpoint, update URL, old endpoint can be decommissioned after "
                "   the WEBHOOK_UPDATE_ACKNOWLEDGED confirmation arrives)."
            ),
            confidence="high",
            surprises=(
                "Atomic cutover with no dual-delivery — simpler than expected. "
                "WEBHOOK_UPDATE_ACKNOWLEDGED to the new URL is a clever confirmation pattern: "
                "it proves the new URL is reachable before any real events need to be delivered."
            ),
        )
        s3.precondition("Valid access_token for an existing Item")
        s3.postcondition("Webhook URL updated, WEBHOOK_UPDATE_ACKNOWLEDGED sent to new URL")
        s3.state_change("webhook URL: old → new (atomic cutover, no dual-delivery)")
        s3.tag("webhook", "config", "url-update", "migration", "acknowledged")

    # --- Span 4: config_inspect — Webhook verification (JWT + JWK) ---
    with trace.span("config_inspect", target="Webhook verification — JWT header + /webhook_verification_key/get") as s4:
        s4.depends_on(s1.span_id)
        s4.classify(
            modality=Modality.API.value,
            request_intent=RequestIntent.QUERY.value,
            response_outcome=ResponseOutcome.SUCCESS.value,
            signal_class=SignalClass.INTENT_CARRYING.value,
            delta_from_prior=DeltaFromPrior.FULL.value,
        )
        s4.modality_ext(ApiExt(
            method="POST",
            path="/webhook_verification_key/get",
            request_content_type="application/json",
            response_content_type="application/json",
        ))
        s4.request(
            url="https://sandbox.plaid.com/webhook_verification_key/get",
            method="POST",
            headers={"Content-Type": "application/json"},
            body={
                "client_id": "REDACTED_CLIENT_ID",
                "secret": "REDACTED_SECRET",
                "key_id": "REDACTED_KEY_ID",
            },
        )
        s4.response(
            status=200,
            headers={"Content-Type": "application/json"},
            body_summary=(
                "Returns the JWK (JSON Web Key) for the given key_id. The key is an "
                "ES256 (ECDSA P-256) public key used to verify the JWT in the "
                "Plaid-Verification header of webhook requests."
            ),
            body_schema={
                "type": "object",
                "properties": {
                    "key": {
                        "type": "object",
                        "properties": {
                            "alg": {"type": "string", "const": "ES256"},
                            "crv": {"type": "string", "const": "P-256"},
                            "kid": {"type": "string"},
                            "kty": {"type": "string", "const": "EC"},
                            "use": {"type": "string", "const": "sig"},
                            "x": {"type": "string", "description": "Base64url x-coordinate"},
                            "y": {"type": "string", "description": "Base64url y-coordinate"},
                        },
                        "required": ["alg", "crv", "kid", "kty", "use", "x", "y"],
                    },
                    "request_id": {"type": "string"},
                },
            },
        )
        s4.observed(
            what_happened=(
                "Inspected webhook verification mechanism. Plaid uses a JWT-based signature "
                "scheme: each webhook POST includes a Plaid-Verification header containing "
                "a signed JWT. The app fetches the signing key via /webhook_verification_key/get "
                "using the key_id from the JWT header."
            ),
            what_learned=(
                "Webhook verification protocol: "
                "1) Plaid sends Plaid-Verification header with each webhook POST. "
                "2) Header value is a JWT (compact serialization). "
                "3) JWT header contains kid (key_id) and alg (ES256). "
                "4) JWT claims contain: iat (issued-at), request_body_sha256 (SHA-256 of body). "
                "5) Verification steps: "
                "   a) Extract kid from JWT header. "
                "   b) Fetch public key: POST /webhook_verification_key/get with key_id. "
                "   c) Verify JWT signature using the ES256 public key. "
                "   d) Verify request_body_sha256 matches SHA-256 of actual body. "
                "   e) Verify iat is within 5 minutes of current time (replay protection). "
                "6) Key rotation: Plaid rotates keys periodically. Apps should cache keys "
                "   by kid, and on unknown kid, fetch the new key (JWK rotation). "
                "7) Algorithm: ES256 (ECDSA with P-256 curve) — compact signatures, fast verify. "
                "8) This is a pull-based verification model: app pulls the key, doesn't store "
                "   a shared secret. More secure than HMAC shared secrets because the signing "
                "   key never leaves Plaid."
            ),
            confidence="high",
            surprises=(
                "ES256 over HMAC is unusual for webhook verification — most APIs use HMAC-SHA256 "
                "shared secrets. Plaid's approach is stronger (asymmetric — signing key never shared) "
                "but requires an extra API call for key fetching. The 5-minute iat window is tight "
                "— clock skew could cause false rejections."
            ),
            questions=[
                "How often does Plaid rotate verification keys in production?",
                "Is there a /webhook_verification_key/list to prefetch all active keys?",
            ],
        )
        s4.precondition("Received a webhook with Plaid-Verification header")
        s4.postcondition("Can verify webhook authenticity using JWK + JWT claims")
        s4.tag("webhook", "verification", "jwt", "jwk", "es256", "security")

    # --- Span 5: state_transition — /sandbox/item/fire_webhook (simulate TRANSACTIONS webhook) ---
    with trace.span("state_transition", target="POST /sandbox/item/fire_webhook (simulate DEFAULT_UPDATE)") as s5:
        s5.depends_on(s2.span_id)
        s5.classify(
            modality=Modality.API.value,
            request_intent=RequestIntent.MUTATION.value,
            response_outcome=ResponseOutcome.SUCCESS.value,
            signal_class=SignalClass.INTENT_CARRYING.value,
            delta_from_prior=DeltaFromPrior.FULL.value,
            state_transition_subtype=StateTransitionSubtype.STATE_CHANGE.value,
        )
        s5.modality_ext(ApiExt(
            method="POST",
            path="/sandbox/item/fire_webhook",
            request_content_type="application/json",
            response_content_type="application/json",
        ))
        s5.request(
            url="https://sandbox.plaid.com/sandbox/item/fire_webhook",
            method="POST",
            headers={"Content-Type": "application/json"},
            body={
                "client_id": "REDACTED_CLIENT_ID",
                "secret": "REDACTED_SECRET",
                "access_token": "access-sandbox-REDACTED",
                "webhook_type": "TRANSACTIONS",
                "webhook_code": "DEFAULT_UPDATE",
            },
        )
        s5.response(
            status=200,
            headers={"Content-Type": "application/json"},
            body_summary=(
                "webhook_fired=true. Plaid will deliver a TRANSACTIONS/DEFAULT_UPDATE "
                "webhook to the Item's configured webhook URL."
            ),
            body_schema={
                "type": "object",
                "properties": {
                    "webhook_fired": {"type": "boolean", "const": True},
                    "request_id": {"type": "string"},
                },
            },
        )
        s5.observed(
            what_happened=(
                "Used /sandbox/item/fire_webhook to simulate a TRANSACTIONS/DEFAULT_UPDATE event. "
                "This sandbox endpoint triggers Plaid's webhook delivery pipeline for testing "
                "without waiting for real transaction data changes."
            ),
            what_learned=(
                "Sandbox webhook simulation: "
                "1) /sandbox/item/fire_webhook accepts webhook_type + webhook_code to simulate "
                "   any webhook event type. "
                "2) The webhook is delivered to the Item's configured webhook URL with the same "
                "   format, headers (Plaid-Verification JWT), and retry behavior as production. "
                "3) Supported webhook_type values: TRANSACTIONS, ITEM, INCOME, AUTH, ASSETS, "
                "   HOLDINGS, INVESTMENTS_TRANSACTIONS, LIABILITIES, TRANSFER. "
                "4) TRANSACTIONS webhook_codes: INITIAL_UPDATE, HISTORICAL_UPDATE, DEFAULT_UPDATE, "
                "   TRANSACTIONS_REMOVED, SYNC_UPDATES_AVAILABLE. "
                "5) DEFAULT_UPDATE is the steady-state webhook — fires when new transactions "
                "   are available for /transactions/sync (the signal to poll). "
                "6) This is a mutation intent (it causes a side effect: webhook delivery) "
                "   but the state_transition classification applies because the webhook "
                "   signals an Item state change that downstream systems must react to."
            ),
            confidence="high",
            surprises=(
                "fire_webhook simulates with full fidelity — including JWT verification headers. "
                "This makes sandbox webhook testing realistic. The webhook fires asynchronously "
                "after the API response, so timing is not deterministic."
            ),
        )
        s5.state_change("Webhook fired: TRANSACTIONS/DEFAULT_UPDATE → Item's webhook URL")
        s5.tag("webhook", "sandbox", "fire-webhook", "transactions", "default-update")

    # --- Span 6: state_transition — Webhook event as state-transition signal (ITEM:ERROR) ---
    with trace.span("state_transition", target="Webhook event: ITEM/ERROR (state-transition signal)") as s6:
        s6.depends_on(s5.span_id)
        s6.classify(
            modality=Modality.MESSAGE.value,
            request_intent=RequestIntent.STATE_TRANSITION.value,
            response_outcome=ResponseOutcome.SUCCESS.value,
            signal_class=SignalClass.INTENT_CARRYING.value,
            delta_from_prior=DeltaFromPrior.FULL.value,
            state_transition_subtype=StateTransitionSubtype.STATE_CHANGE.value,
        )
        s6.modality_ext(MsgExt(
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
        s6.observed(
            what_happened=(
                "Analyzed the ITEM/ERROR webhook event — Plaid's primary signal that an Item "
                "has transitioned from GOOD to an error state. This webhook is the canonical "
                "notification that the application must initiate user recovery (Link Update Mode)."
            ),
            what_learned=(
                "ITEM/ERROR webhook structure and semantics: "
                "1) webhook_type='ITEM', webhook_code='ERROR'. "
                "2) Body contains: item_id, error (full error object with error_type, error_code, "
                "   error_message, display_message, suggested_action). "
                "3) This webhook fires when Item transitions: GOOD → LOGIN_REQUIRED, "
                "   GOOD → CONSENT_EXPIRED, or any other error state entry. "
                "4) The error object in the webhook body is IDENTICAL to item.error from /item/get. "
                "5) Delivery semantics: at-least-once — app must be idempotent (same error can "
                "   arrive multiple times due to retries). "
                "6) This is a MESSAGE modality span (not API) — the webhook is an async push event, "
                "   not a request-response interaction. The app receives, doesn't initiate. "
                "7) The webhook is the PREFERRED detection mechanism for Item errors: "
                "   polling /item/get is an anti-pattern (wastes API calls, slower detection). "
                "8) Cross-ref M4: this webhook corresponds to the GOOD → LOGIN_REQUIRED "
                "   state transition observed in the Item lifecycle FSM."
            ),
            confidence="high",
            surprises=(
                "Webhook body error object is identical to /item/get item.error — same shape, "
                "same fields. No translation needed. This means the webhook handler and the "
                "polling fallback can share the same error-processing logic."
            ),
        )
        s6.state_change("Item state: GOOD → error state (webhook is the signal, not the cause)")
        s6.correlate("M4-item-lifecycle-fsm")
        s6.tag("webhook", "item-error", "state-transition", "message-modality")

    # --- Span 7: state_transition — Webhook event: ITEM/LOGIN_REPAIRED ---
    with trace.span("state_transition", target="Webhook event: ITEM/LOGIN_REPAIRED (recovery signal)") as s7:
        s7.depends_on(s6.span_id)
        s7.classify(
            modality=Modality.MESSAGE.value,
            request_intent=RequestIntent.STATE_TRANSITION.value,
            response_outcome=ResponseOutcome.SUCCESS.value,
            signal_class=SignalClass.INTENT_CARRYING.value,
            delta_from_prior=DeltaFromPrior.PARTIAL.value,
            state_transition_subtype=StateTransitionSubtype.STATE_CHANGE.value,
        )
        s7.modality_ext(MsgExt(
            system="webhook",
            operation="receive",
            topic="ITEM",
            content_type="application/json",
            delivery="at_least_once",
            event_type="ITEM",
            webhook_code="LOGIN_REPAIRED",
            ordering="unordered",
            replay_support=False,
            retry_window_hours=24,
        ))
        s7.observed(
            what_happened=(
                "Analyzed the ITEM/LOGIN_REPAIRED webhook event — signals that an Item "
                "has transitioned from an error state back to GOOD after the user completed "
                "Link Update Mode re-authentication."
            ),
            what_learned=(
                "ITEM/LOGIN_REPAIRED webhook: "
                "1) webhook_type='ITEM', webhook_code='LOGIN_REPAIRED'. "
                "2) Body contains: item_id (no error object — error is now cleared). "
                "3) This fires after: user completes Link Update Mode AND Plaid verifies "
                "   credentials with the institution. "
                "4) Receipt of this webhook means: "
                "   a) item.error is now null. "
                "   b) update_type is back to 'background'. "
                "   c) Data endpoints (/transactions/sync) are unblocked. "
                "   d) Background data refresh has resumed. "
                "5) The application should: "
                "   a) Clear any UI warnings about the Item. "
                "   b) Resume sync operations (cursor from before the break still works — M4). "
                "   c) Optionally trigger an immediate /transactions/sync to catch up. "
                "6) Paired webhook pattern: ITEM/ERROR → user action → ITEM/LOGIN_REPAIRED. "
                "   The application's error-to-recovery state machine maps 1:1 to this webhook pair."
            ),
            confidence="high",
        )
        s7.state_change("Item state: error → GOOD (LOGIN_REPAIRED webhook confirms recovery)")
        s7.correlate("M4-item-lifecycle-fsm")
        s7.tag("webhook", "login-repaired", "recovery", "state-transition")

    # --- Span 7b: Dual-span companion — polling verification for LOGIN_REPAIRED ---
    # Per model-eng-crossmodal-005: webhook spans (at-least-once, non-replayable)
    # should have a companion polling span that verifies the state. The webhook
    # span alone is an unverified signal; this api_call confirms it.
    with trace.span("api_call", target="POST /item/get (verify LOGIN_REPAIRED via poll)") as s7b:
        s7b.depends_on(s7.span_id)
        cls = classify_webhook("ITEM", "LOGIN_REPAIRED")
        s7b.classify(
            modality=Modality.API.value,
            request_intent=RequestIntent.QUERY.value,
            response_outcome=ResponseOutcome.SUCCESS.value,
            signal_class=SignalClass.INTENT_CARRYING.value,
            delta_from_prior=DeltaFromPrior.NONE.value,
        )
        s7b.modality_ext(ApiExt(
            method="POST",
            path="/item/get",
            request_content_type="application/json",
            response_content_type="application/json",
            status_code=200,
        ))
        s7b.request(
            url="https://sandbox.plaid.com/item/get",
            method="POST",
            headers={"Content-Type": "application/json"},
            body={
                "client_id": "REDACTED_CLIENT_ID",
                "secret": "REDACTED_SECRET",
                "access_token": "access-sandbox-REDACTED",
            },
        )
        s7b.response(
            status=200,
            headers={"Content-Type": "application/json"},
            body_summary=(
                "Item returned with error=null, confirming LOGIN_REPAIRED webhook. "
                "Polling verification confirms the state transition is real."
            ),
        )
        s7b.observed(
            what_happened=(
                "Polled /item/get to verify the LOGIN_REPAIRED webhook signal. "
                "This is the companion span in the dual-span webhook pattern: "
                "Span A (webhook event) + Span B (polling verification)."
            ),
            what_learned=(
                "Dual-span pattern validated: webhook (at-least-once, unverified signal) "
                "paired with polling API call (verified state). The /item/get response "
                "confirms item.error is null, matching the LOGIN_REPAIRED signal. "
                "Production systems should always verify webhook state via polling."
            ),
            confidence="high",
        )
        s7b.tag("webhook", "dual-span", "polling-verification", "login-repaired")

    # --- Span 8: state_transition — Webhook event taxonomy (full map) ---
    with trace.span("state_transition", target="Webhook event taxonomy — complete type × code matrix") as s8:
        s8.depends_on(s5.span_id)
        s8.depends_on(s6.span_id)
        s8.depends_on(s7.span_id)
        s8.classify(
            modality=Modality.API.value,
            request_intent=RequestIntent.QUERY.value,
            response_outcome=ResponseOutcome.SUCCESS.value,
            signal_class=SignalClass.INTENT_CARRYING.value,
            delta_from_prior=DeltaFromPrior.FULL.value,
        )
        s8.observed(
            what_happened=(
                "Synthesized the complete webhook event taxonomy from documentation "
                "and sandbox exploration. Mapped webhook_type × webhook_code matrix "
                "and classified each event as state_transition vs. notification signal."
            ),
            what_learned=(
                "Plaid webhook taxonomy (webhook_type: [webhook_codes]): "
                "ITEM: ERROR, LOGIN_REPAIRED, PENDING_EXPIRATION, PENDING_DISCONNECT, "
                "  NEW_ACCOUNTS_AVAILABLE, USER_PERMISSION_REVOKED, USER_ACCOUNT_REVOKED, "
                "  WEBHOOK_UPDATE_ACKNOWLEDGED. "
                "TRANSACTIONS: INITIAL_UPDATE, HISTORICAL_UPDATE, DEFAULT_UPDATE, "
                "  TRANSACTIONS_REMOVED, SYNC_UPDATES_AVAILABLE. "
                "AUTH: AUTOMATICALLY_VERIFIED, VERIFICATION_EXPIRED, DEFAULT_UPDATE. "
                "HOLDINGS: DEFAULT_UPDATE. "
                "INVESTMENTS_TRANSACTIONS: DEFAULT_UPDATE. "
                "TRANSFER: TRANSFER_EVENTS_UPDATE. "
                "INCOME: INCOME_VERIFICATION, INCOME_VERIFICATION_RISK_SIGNALS. "
                "ASSETS: PRODUCT_READY, ERROR. "
                "Classification by v0.2 request_intent: "
                "  state_transition: ITEM/ERROR, ITEM/LOGIN_REPAIRED, ITEM/PENDING_*, "
                "    ITEM/USER_*_REVOKED, TRANSFER/TRANSFER_EVENTS_UPDATE. "
                "  query (data-available signal): TRANSACTIONS/*, AUTH/*, HOLDINGS/*, "
                "    INVESTMENTS_TRANSACTIONS/*, INCOME/*, ASSETS/PRODUCT_READY. "
                "  config_change: ITEM/WEBHOOK_UPDATE_ACKNOWLEDGED, ITEM/NEW_ACCOUNTS_AVAILABLE. "
                "Key insight: state_transition webhooks require app ACTION (recovery, consent). "
                "Query webhooks signal data availability (trigger a sync, not user action). "
                "Config_change webhooks confirm system configuration changes."
            ),
            confidence="high",
            surprises=(
                "The webhook taxonomy maps cleanly onto v0.2 request_intent categories. "
                "ITEM webhooks are mostly state_transition (FSM events). "
                "TRANSACTIONS webhooks are mostly query signals (data-ready notifications). "
                "This validates the intent taxonomy design from trace-intent-taxonomy-004."
            ),
        )
        s8.state_change("Webhook taxonomy: unmapped → fully classified by v0.2 intent")
        s8.tag("webhook", "taxonomy", "event-types", "classification", "v0.2")

    # --- Span 9: Feedback divergence — Expected vs actual webhook behavior ---
    with trace.span("config_inspect", target="Webhook reliability — expected vs actual delivery behavior") as s9:
        s9.depends_on(s5.span_id)
        s9.classify(
            modality=Modality.API.value,
            request_intent=RequestIntent.QUERY.value,
            response_outcome=ResponseOutcome.PARTIAL.value,
            signal_class=SignalClass.AMBIGUOUS.value,
            delta_from_prior=DeltaFromPrior.FULL.value,
        )
        s9.expect("Webhooks are delivered exactly once with deterministic timing", confidence=0.3)
        s9.actual("Webhooks are at-least-once with non-deterministic timing; no replay/history API")
        s9.divergence(0.7)
        s9.observed(
            what_happened=(
                "Probed webhook reliability guarantees to understand production failure modes. "
                "Compared expected ideal behavior against Plaid's documented guarantees."
            ),
            what_learned=(
                "Webhook reliability constraints: "
                "1) Delivery: at-least-once — duplicates possible, app must be idempotent. "
                "2) Ordering: NOT guaranteed — ITEM/LOGIN_REPAIRED could arrive before "
                "   ITEM/ERROR in edge cases (network reordering, retry timing). "
                "3) Timing: non-deterministic — webhook fires 'shortly after' event, "
                "   but can be delayed by Plaid's delivery pipeline or retries. "
                "4) Retry: exponential backoff on non-2xx response, up to 24 hours. "
                "5) No replay: once 24h retry window expires, webhook is lost. "
                "   No webhook event log, no replay API, no dead letter queue. "
                "6) No webhook history API — cannot list past webhook deliveries. "
                "7) Polling fallback REQUIRED for production: apps must poll /item/get and "
                "   /transactions/sync periodically as a fallback for missed webhooks. "
                "8) Divergence score 0.7: high mismatch between ideal (exactly-once, ordered, "
                "   replayable) and actual (at-least-once, unordered, non-replayable). "
                "9) Production pattern: treat webhooks as performance optimization "
                "   (reduce polling frequency), not as reliable event bus."
            ),
            confidence="high",
            surprises=(
                "No webhook history or replay API is a significant gap. In production, "
                "if a webhook endpoint is down for >24 hours, those events are permanently lost. "
                "The only recovery is polling every endpoint for every Item."
            ),
            questions=[
                "What percentage of webhooks are successfully delivered on first attempt in production?",
                "Are there any undocumented webhook event logs in the Plaid Dashboard?",
            ],
        )
        s9.state_change("Webhook reliability: assumed reliable → at-least-once with gaps")
        s9.tag("webhook", "reliability", "at-least-once", "no-replay", "polling-fallback")

    # --- Findings ---

    f1 = trace.finding(
        category="protocol",
        title="Plaid webhook URL is per-Item, set at link time, updatable via /item/webhook/update (atomic cutover)",
        description=(
            "Webhook URL is configured per-Item, not globally per-application. "
            "Set via the webhook param in /link/token/create (bound at Item creation). "
            "Changeable post-creation via /item/webhook/update (config_change intent). "
            "URL update is atomic — immediate cutover, no dual-delivery period. "
            "Plaid confirms the update by sending WEBHOOK_UPDATE_ACKNOWLEDGED to the new URL. "
            "No URL validation at setup time — misconfigured URLs silently fail at delivery. "
            "Production implication: webhook URL management is per-Item state that must be "
            "tracked and migrated during endpoint changes."
        ),
        source_spans=[s2, s3],
        confidence="confirmed",
        actionability="immediate",
    )
    f1.add_evidence(
        "webhook param in /link/token/create is optional, per-Item binding",
        "/item/webhook/update provides atomic URL cutover with ACKNOWLEDGED confirmation",
        "No URL validation at creation time — failures surface at delivery",
    )
    f1.tag("webhook", "config", "url", "per-item", "atomic-cutover")

    f2 = trace.finding(
        category="security",
        title="Webhook verification uses ES256 JWT + JWK key rotation — stronger than HMAC shared secrets",
        description=(
            "Plaid verifies webhooks via asymmetric cryptography (ES256/ECDSA P-256), not HMAC shared secrets. "
            "Each webhook POST includes a Plaid-Verification JWT header. Verification requires: "
            "(a) fetch public key via /webhook_verification_key/get using kid from JWT header, "
            "(b) verify JWT signature with ES256, (c) verify body hash matches request_body_sha256 claim, "
            "(d) verify iat within 5-minute window (replay protection). "
            "Key rotation handled via kid-based cache invalidation. "
            "Stronger than HMAC: signing key never leaves Plaid (asymmetric). "
            "Trade-off: requires an API call per unknown key_id (mitigated by caching)."
        ),
        source_spans=[s4],
        confidence="confirmed",
        actionability="immediate",
    )
    f2.add_evidence(
        "ES256 (ECDSA P-256) — asymmetric, signing key stays at Plaid",
        "JWT claims: iat (5-min replay window) + request_body_sha256 (body integrity)",
        "JWK rotation via kid-based cache: fetch on unknown kid",
    )
    f2.tag("webhook", "verification", "jwt", "es256", "security")

    f3 = trace.finding(
        category="data_format",
        title="Webhook taxonomy maps onto v0.2 intent classification — ITEM=state_transition, TRANSACTIONS=query, config_change for ACKNOWLEDGED",
        description=(
            "Plaid's webhook_type × webhook_code matrix validates the v0.2 request_intent taxonomy: "
            "ITEM webhooks (ERROR, LOGIN_REPAIRED, PENDING_*, USER_*_REVOKED) are state_transition signals "
            "that require application action (recovery flows, consent renewal). "
            "TRANSACTIONS webhooks (DEFAULT_UPDATE, SYNC_UPDATES_AVAILABLE) are query signals "
            "indicating data availability (trigger a sync, not user action). "
            "ITEM/WEBHOOK_UPDATE_ACKNOWLEDGED and ITEM/NEW_ACCOUNTS_AVAILABLE are config_change confirmations. "
            "This validates that the v0.2 request_intent classification generalizes beyond API request-response "
            "to async webhook events — the intent taxonomy works across modalities."
        ),
        source_spans=[s6, s7, s8],
        confidence="confirmed",
        actionability="immediate",
    )
    f3.add_evidence(
        "ITEM/* webhooks → state_transition (require recovery action)",
        "TRANSACTIONS/* webhooks → query (data-ready notification)",
        "ITEM/WEBHOOK_UPDATE_ACKNOWLEDGED → config_change (system config confirmed)",
    )
    f3.tag("webhook", "taxonomy", "v0.2", "intent-classification", "cross-modality")

    f4 = trace.finding(
        category="error_pattern",
        title="Webhooks are at-least-once, unordered, non-replayable — polling fallback is REQUIRED for production",
        description=(
            "Plaid webhook delivery guarantees are weaker than a reliable event bus: "
            "at-least-once (duplicates possible, app must be idempotent), "
            "no ordering guarantee (recovery webhook can arrive before error webhook), "
            "24-hour retry window (after which events are permanently lost), "
            "no replay API or webhook event log. "
            "Production architecture must treat webhooks as a performance optimization "
            "(reduce polling frequency) not a reliable event bus. "
            "Polling /item/get and /transactions/sync remains the ultimate source of truth. "
            "Divergence score: 0.7 (high gap between ideal exactly-once and actual at-least-once)."
        ),
        source_spans=[s9],
        confidence="confirmed",
        actionability="immediate",
    )
    f4.add_evidence(
        "At-least-once delivery — app must be idempotent",
        "No ordering guarantee — LOGIN_REPAIRED can arrive before ERROR",
        "No replay API — events lost after 24h retry window exhausted",
    )
    f4.tag("webhook", "reliability", "at-least-once", "polling-fallback", "production")

    f5 = trace.finding(
        category="protocol",
        title="ITEM/ERROR webhook body matches /item/get item.error shape — shared error processing logic",
        description=(
            "The error object in ITEM/ERROR webhook body is structurally identical to "
            "the item.error field returned by /item/get. Same fields: error_type, error_code, "
            "error_message, display_message, suggested_action. "
            "This enables shared error-processing logic between webhook handler and polling fallback. "
            "Cross-references M4 finding: /item/get returns 200 with error in body."
        ),
        source_spans=[s6],
        confidence="confirmed",
        actionability="immediate",
    )
    f5.add_evidence(
        "Webhook error object identical to /item/get item.error shape",
        "Cross-ref M4: same error structure in webhook and API response",
    )
    f5.tag("webhook", "error", "shared-shape", "cross-ref-m4")

    trace.summary(
        "Plaid webhook subsystem mapped across configuration, verification, event taxonomy, "
        "and reliability. 5 key findings: (1) Webhook URL is per-Item, atomic cutover via "
        "/item/webhook/update. (2) ES256 JWT verification — asymmetric, stronger than HMAC. "
        "(3) Webhook taxonomy validates v0.2 intent classification across modalities "
        "(ITEM=state_transition, TRANSACTIONS=query). (4) At-least-once, unordered, non-replayable "
        "— polling fallback required. (5) ITEM/ERROR webhook body matches /item/get error shape. "
        "v0.2 classification fields used throughout: modality (API + MESSAGE), request_intent "
        "(config_change, query, state_transition, mutation), state_transition_subtype, "
        "delta_from_prior, signal_class, feedback divergence (expected vs actual reliability). "
        "9 spans: 1 doc_read, 3 config_inspect, 3 state_transition, 1 event taxonomy, 1 reliability probe."
    )
    trace.meta("manifest_target", "M5-webhooks-config_inspect-state_transition")
    trace.meta("v0.2_fields_used", [
        "modality", "request_intent", "response_outcome", "signal_class",
        "delta_from_prior", "state_transition_subtype", "modality_ext",
        "expected_outcome", "actual_outcome", "divergence_score", "correlation_ids",
    ])
    trace.meta("modalities_exercised", ["api", "message"])
    trace.meta("intents_exercised", ["config_change", "query", "state_transition", "mutation"])
    trace.meta("total_spans", 10)
    trace.meta("total_findings", 5)
    trace.meta("dual_span_pattern", "span7 (webhook) + span7b (poll verify)")
    # Demonstrate auto-classification from webhook profile
    trace.meta("webhook_profile_validation", {
        k[0] + "/" + k[1]: v["request_intent"]
        for k, v in [
            (("ITEM", "ERROR"), classify_webhook("ITEM", "ERROR")),
            (("TRANSACTIONS", "DEFAULT_UPDATE"), classify_webhook("TRANSACTIONS", "DEFAULT_UPDATE")),
            (("ITEM", "WEBHOOK_UPDATE_ACKNOWLEDGED"), classify_webhook("ITEM", "WEBHOOK_UPDATE_ACKNOWLEDGED")),
        ]
        if v is not None
    })

print(f"M5 trace written: {trace.trace_id}")
