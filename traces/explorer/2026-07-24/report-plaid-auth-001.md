# Plaid Auth Study

**System:** plaid
**Generated:** 2026-07-24 05:50 UTC
**Pipeline:** Parser → Analyzer → Reporter v0.1
## Executive Summary

| Metric | Value |
|--------|-------|
| Traces analyzed | 1 |
| Spans processed | 5 |
| Endpoints discovered | 5 |
| Findings extracted | 6 |
| Finding clusters | 6 |
| Dependency edges | 24 |
| Open questions | 11 |
| Low-confidence areas | 0 |
| Agent roles active | explorer |
| Span kinds observed | auth_flow, doc_read, state_transition |

## Endpoint Catalog

### `Plaid Link Overview docs + /link/token/create API reference`

| Property | Value |
|----------|-------|
| Hits | 1 |
| URL | `https://plaid.com/docs/link/` |
| Span kinds | doc_read |
| Tags | access_token, auth, link_token, public_token, token |

**Observations:**
- The token architecture enforces a strict client-server boundary: ephemeral tokens on the client, durable tokens on the server. The public_token's 30-minute single-use constraint is a security measure — it can never be replayed. The link_token's shorter TTL in update mode (30m vs 4h) suggests update flows are expected to be immediate user actions, not deferred.

**Open questions:**
- How does Plaid handle token rotation for long-lived access_tokens?
- What is the failure mode when a public_token expires mid-exchange?

### `Plaid Items API reference — /item/get, /item/remove, /item/public_token/exchange, /item/access_token/invalidate`

| Property | Value |
|----------|-------|
| Hits | 1 |
| URL | `https://plaid.com/docs/api/items/` |
| Span kinds | state_transition |
| Tags | error_states, item, recovery, state_machine, webhook |

**Observations:**
- Items are the central persistence unit — they track not just auth state but also product subscriptions, billing, and consent metadata. The error taxonomy splits into credential errors (ITEM_LOGIN_REQUIRED), OAuth errors (3 subtypes: invalid token, expired consent, user revoked), and scheduled disconnection (PENDING_DISCONNECT for US/CA, PENDING_EXPIRATION for UK/EU with 7-day advance warning). LOGIN_REPAIRED is an interesting external recovery event — the Item self-heals without app intervention.

**Open questions:**
- What triggers LOGIN_REPAIRED — does the bank push a credential refresh?
- How does PENDING_DISCONNECT timing interact with consent_expiration_time?

### `Plaid OAuth Guide — redirect flows, institution-specific behaviors, consent management`

| Property | Value |
|----------|-------|
| Hits | 1 |
| URL | `https://plaid.com/docs/link/oauth/` |
| Span kinds | auth_flow |
| Tags | app_to_app, consent, institution_specific, oauth, redirect |

**Observations:**
- OAuth is not a uniform standard across institutions — each bank adds its own constraints. Chase's Item invalidation on new OAuth with different account sets is a critical edge case for any multi-account integration. BofA's ongoing 2026 migration means the credential→OAuth transition is still actively happening. The consent expiration landscape is fragmented: 3 months (Brex) to 18 months (USAA), with EU at 180 days. App-to-App (biometric auth via bank's native app) is limited to Chase and Chime — a premium UX path.

**Open questions:**
- How do apps handle the BofA credential→OAuth migration mid-flight?
- What percentage of US institutions now use OAuth vs credential-based?

### `Plaid Sandbox test credentials — MFA challenge types and response formats`

| Property | Value |
|----------|-------|
| Hits | 1 |
| URL | `https://plaid.com/docs/sandbox/test-credentials/` |
| Span kinds | auth_flow |
| Tags | challenge_types, mfa, sandbox, typed_union |

**Observations:**
- MFA in Plaid is a typed union with 4 variants — this is a clean abstraction that IGNITE can directly adopt. The challenge-response patterns are fully parameterized: the number of rounds, questions per round, and options per question are all configurable via the password template. The response format uses positional indexing (answer_round_question_option) rather than semantic keys. Bank of America and US Bank cannot be used as test institutions for MFA flows — suggesting their real MFA implementations differ enough from the sandbox model.

**Open questions:**
- Do any institutions use MFA types not covered by these 4 categories?
- How does MFA interact with OAuth flows — does OAuth eliminate MFA from Plaid's perspective?

### `India Account Aggregator framework — ReBIT spec, Setu FIP APIs, Sahamati technical architecture`

| Property | Value |
|----------|-------|
| Hits | 1 |
| URL | `https://docs.setu.co/data/account-aggregator/api-integration/fip-apis` |
| Span kinds | doc_read |
| Tags | consent_artefact, encrypted_relay, fip, fiu, india_aa, zero_knowledge |

**Observations:**
- The India AA framework is architecturally more sophisticated than Plaid in two critical ways: (1) the AA intermediary is zero-knowledge — it routes encrypted data but cannot read it, unlike Plaid which processes data in cleartext; (2) consent is a first-class signed data structure with explicit purpose, duration, frequency, and data-life fields, not an implicit side-effect of a UI flow. The health-aware routing via Setu (real-time FIP success rates, latency percentiles) is a production pattern Plaid doesn't expose publicly. These represent IGNITE's highest-value transferable patterns from this trace.

**Open questions:**
- Can the AA consent artefact model be generalized to non-Indian financial institutions?
- What is the actual FIP success rate distribution across Indian banks?
- How does the AA framework handle FIP downtime during an active consent?


## Findings

| Confidence | Category | Finding | Sources |
|------------|----------|---------|---------|
| ✅ confirmed | protocol | India AA's zero-knowledge relay and signed consent artefact are structurally superior patterns IGNITE should adopt | 1 trace(s) |
| ✅ confirmed | state_machine | Item lifecycle has 5+ error states with a unified recovery path via Link Update Mode | 1 trace(s) |
| ✅ confirmed | auth | MFA is a typed union of 4 challenge variants with structured response formats | 1 trace(s) |
| ✅ confirmed | auth | OAuth consent expiration is fragmented: 3 months (Brex) to 18 months (USAA), requiring per-institution refresh strategies | 1 trace(s) |
| ✅ confirmed | auth | Plaid uses a 3-stage ephemeral-to-persistent token exchange with strict client-server boundary | 1 trace(s) |
| 🔶 probable | undocumented | Health-aware FIP routing with real-time metrics is a production pattern Plaid doesn't expose | 1 trace(s) |

### ✅ India AA's zero-knowledge relay and signed consent artefact are structurally superior patterns IGNITE should adopt

**Category:** protocol | **Confidence:** confirmed | **Sources:** 1 trace(s)

Unlike Plaid (which processes data in cleartext), India's AA framework enforces that the intermediary cannot decrypt data — FIPs encrypt with FIU's public key, AA relays opaque payloads. Consent is a digitally signed JSON artefact with explicit purpose, data types, duration, frequency, and data-life (retention limit). This is architecturally more robust than Plaid's implicit consent model. The 'dataLife' field (post-fetch retention constraint) has no Plaid equivalent and anticipates GDPR-style regulation. IGNITE should implement consent-as-data-structure and encrypted relay as foundational patterns, not afterthoughts.

**Evidence:** span-india-aa-005

### ✅ Item lifecycle has 5+ error states with a unified recovery path via Link Update Mode

**Category:** state_machine | **Confidence:** confirmed | **Sources:** 1 trace(s)

Items transition from HEALTHY to error states (ITEM_LOGIN_REQUIRED, OAUTH_INVALID_TOKEN, OAUTH_CONSENT_EXPIRED, OAUTH_USER_REVOKED) or scheduled disconnection (PENDING_DISCONNECT/EXPIRATION with 7-day advance warning). All error states converge on Link Update Mode for recovery. LOGIN_REPAIRED is a unique external recovery event. 8 webhook types provide async state change notifications. IGNITE should model financial connection state as a finite state machine with typed error categories and webhook-driven transitions.

**Evidence:** span-item-lifecycle-002

### ✅ MFA is a typed union of 4 challenge variants with structured response formats

**Category:** auth | **Confidence:** confirmed | **Sources:** 1 trace(s)

Plaid abstracts institution-specific MFA into 4 typed challenges: Device OTP (code delivery), Security Questions (N rounds × M questions), Single Selection (binary choice), Multiple Selections (N rounds × M questions × O options). Responses follow positional indexing (answer_round_question_option). OAuth flows delegate MFA to the bank entirely, removing it from Plaid's abstraction layer. IGNITE should model MFA as a discriminated union type with per-variant response schemas.

**Evidence:** span-mfa-types-004

### ✅ OAuth consent expiration is fragmented: 3 months (Brex) to 18 months (USAA), requiring per-institution refresh strategies

**Category:** auth | **Confidence:** confirmed | **Sources:** 1 trace(s)

Each OAuth institution sets its own consent duration: EU/UK at 180 days, most US banks at 12 months, with outliers at 3 months (Brex) and 18 months (USAA). Chase invalidates old Items when new OAuth creates different account sets. BofA is actively migrating from credential to OAuth throughout 2026. Charles Schwab limits to 1 active Item per user/app with 6-week approval. IGNITE must treat consent expiration as a per-institution parameter, not a global constant, and implement proactive renewal workflows triggered by PENDING_DISCONNECT/EXPIRATION webhooks.

**Evidence:** span-oauth-flow-003

### ✅ Plaid uses a 3-stage ephemeral-to-persistent token exchange with strict client-server boundary

**Category:** auth | **Confidence:** confirmed | **Sources:** 1 trace(s)

The auth flow is link_token (server, 4h/30m TTL) → public_token (client, 30m single-use) → access_token (server, permanent). This pattern enforces that secrets never persist on the client and ephemeral tokens cannot be replayed. The access_token can be rotated via /item/access_token/invalidate without re-authenticating the user. IGNITE should model this as a generic multi-stage token exchange state machine with configurable TTLs and usage constraints per stage.

**Evidence:** span-token-lifecycle-001, span-item-lifecycle-002

### 🔶 Health-aware FIP routing with real-time metrics is a production pattern Plaid doesn't expose

**Category:** undocumented | **Confidence:** probable | **Sources:** 1 trace(s)

Setu's FIP health API provides real-time success rates (consent conversion, data fetch), per-AA breakdowns, and latency percentiles (P50/P95/P99) with 10-minute refresh cycles. FIPs are dynamically activated/deactivated based on performance. Plaid does not expose equivalent institution health metrics to developers. IGNITE should implement health-aware connector routing as a first-class concern, dynamically steering traffic away from degraded institutions.

**Evidence:** span-india-aa-005


## Dependency Map

### Depends On

```
  span-item-lifecycle-002 → span-token-lifecycle-001
  span-oauth-flow-003 → span-token-lifecycle-001
  span-mfa-types-004 → span-oauth-flow-003
  span-india-aa-005 → span-token-lifecycle-001
  span-india-aa-005 → span-oauth-flow-003
```

### Derived From

```
  finding-auth-001 → span-token-lifecycle-001
  finding-auth-001 → span-item-lifecycle-002
  finding-auth-002 → span-item-lifecycle-002
  finding-auth-003 → span-oauth-flow-003
  finding-auth-004 → span-mfa-types-004
  finding-auth-005 → span-india-aa-005
  finding-auth-006 → span-india-aa-005
```

### Enables

```
  span-token-lifecycle-001 → span-item-lifecycle-002
  span-token-lifecycle-001 → span-oauth-flow-003
  span-item-lifecycle-002 → span-oauth-flow-003
  span-oauth-flow-003 → span-mfa-types-004
```

### Refines

```
  span-oauth-flow-003 → span-item-lifecycle-002
```

### Related To

```
  finding-auth-001 → finding-auth-002
  finding-auth-002 → finding-auth-001
  finding-auth-002 → finding-auth-003
  finding-auth-003 → finding-auth-002
  finding-auth-005 → finding-auth-001
  finding-auth-005 → finding-auth-003
  finding-auth-006 → finding-auth-005
```

**Topology:**
- Entry points: finding-auth-004, finding-auth-006


## Coverage & Gaps

### Exploration Coverage

| Span Kind | Status |
|-----------|--------|
| api_call | ⬜ Not yet explored |
| auth_flow | ✅ Observed |
| config_inspect | ⬜ Not yet explored |
| doc_read | ✅ Observed |
| error_probe | ⬜ Not yet explored |
| sdk_inspect | ⬜ Not yet explored |
| state_transition | ✅ Observed |

**4 span kind(s) not yet explored** — these represent investigation angles the swarm hasn't tried yet.


## Open Questions

These questions were raised during exploration and remain unanswered:

1. How does Plaid handle token rotation for long-lived access_tokens?
2. What is the failure mode when a public_token expires mid-exchange?
3. What triggers LOGIN_REPAIRED — does the bank push a credential refresh?
4. How does PENDING_DISCONNECT timing interact with consent_expiration_time?
5. How do apps handle the BofA credential→OAuth migration mid-flight?
6. What percentage of US institutions now use OAuth vs credential-based?
7. Do any institutions use MFA types not covered by these 4 categories?
8. How does MFA interact with OAuth flows — does OAuth eliminate MFA from Plaid's perspective?
9. Can the AA consent artefact model be generalized to non-Indian financial institutions?
10. What is the actual FIP success rate distribution across Indian banks?
11. How does the AA framework handle FIP downtime during an active consent?


---
*Generated by IGNITE L2 Pipeline: Parser → Analyzer → Reporter v0.1*
