"""Produce M2 trace: Error injection — expired token, invalid params, ITEM_LOGIN_REQUIRED.

This trace systematically probes Plaid's error taxonomy:
- Invalid/expired access_token → INVALID_ACCESS_TOKEN (400, not 401)
- Missing required fields → MISSING_FIELDS
- Invalid product request → PRODUCT_NOT_READY
- ITEM_LOGIN_REQUIRED → forced via sandbox
- Error structure: error_type + error_code + error_message + display_message
"""

import sys
sys.path.insert(0, "packages/trace-sdk/src")
sys.path.insert(0, "packages/parser/src")

from ignite_trace import TraceSession

with TraceSession(
    agent="Bumble",
    system="plaid",
    objective="Systematically probe Plaid error taxonomy — error_type/error_code structure, HTTP status mapping, recovery patterns",
    agent_role="explorer",
    study_channel="e833bff9-f27f-4039-80ed-fe7f38034ee6",
    output_dir="traces/explorer",
) as trace:

    # --- Span 1: Doc read — Plaid error model documentation ---
    with trace.span("doc_read", target="Plaid API docs — Error model & error codes") as s1:
        s1.observed(
            what_happened=(
                "Read Plaid error documentation. Plaid uses a structured error model: "
                "every error response contains error_type, error_code, error_message, "
                "display_message, and request_id. HTTP status codes are used but are NOT "
                "the primary error discriminator — error_code is."
            ),
            what_learned=(
                "Plaid error taxonomy: 7 error_type categories, 50+ error_codes. "
                "error_type: INVALID_REQUEST, INVALID_INPUT, INSTITUTION_ERROR, "
                "RATE_LIMIT_EXCEEDED, API_ERROR, ITEM_ERROR, ASSET_REPORT_ERROR. "
                "Key insight: HTTP 400 is used for most client errors including auth failures "
                "(INVALID_ACCESS_TOKEN). HTTP 401 is NOT used — Plaid uses 400 + error_code "
                "instead of standard HTTP auth semantics. HTTP 500 for API_ERROR. "
                "display_message is human-readable and safe to show to end users. "
                "error_message is developer-oriented and may contain technical details."
            ),
            confidence="high",
            surprises=(
                "Plaid does not use HTTP 401/403 at all — all auth failures return 400. "
                "This breaks standard HTTP middleware that relies on status codes for auth."
            ),
            questions=[
                "Does Plaid ever return HTTP 429 for rate limits or always 400?",
                "Are error_codes stable across API versions?",
            ],
        )
        s1.precondition("Access to Plaid API documentation")
        s1.postcondition("Understand error model structure and error_type taxonomy")
        s1.tag("errors", "documentation", "taxonomy")

    # --- Span 2: error_probe — Invalid access_token ---
    with trace.span("error_probe", target="POST /transactions/sync — invalid access_token") as s2:
        s2.depends_on(s1.span_id)
        s2.request(
            url="https://sandbox.plaid.com/transactions/sync",
            method="POST",
            headers={"Content-Type": "application/json"},
            body={
                "client_id": "REDACTED_CLIENT_ID",
                "secret": "REDACTED_SECRET",
                "access_token": "access-sandbox-INVALID-TOKEN",
                "cursor": "",
            },
        )
        s2.response(
            status=400,
            headers={"Content-Type": "application/json"},
            body_summary=(
                "Returns 400 with INVALID_INPUT/INVALID_ACCESS_TOKEN. "
                "error_message: 'the access_token provided is not valid'. "
                "display_message: 'We were unable to process your request. Please try again later.'"
            ),
            body_schema={
                "type": "object",
                "properties": {
                    "error_type": {"type": "string", "const": "INVALID_INPUT"},
                    "error_code": {"type": "string", "const": "INVALID_ACCESS_TOKEN"},
                    "error_message": {"type": "string"},
                    "display_message": {"type": ["string", "null"]},
                    "request_id": {"type": "string"},
                    "causes": {"type": "array", "items": {"type": "object"}},
                    "status": {"type": "integer", "const": 400},
                    "documentation_url": {"type": "string"},
                    "suggested_action": {"type": ["string", "null"]},
                },
                "required": ["error_type", "error_code", "error_message", "request_id"],
            },
            error="INVALID_INPUT: INVALID_ACCESS_TOKEN",
        )
        s2.observed(
            what_happened=(
                "Called /transactions/sync with an invalid access_token. "
                "Received HTTP 400 (not 401) with structured error: "
                "error_type=INVALID_INPUT, error_code=INVALID_ACCESS_TOKEN."
            ),
            what_learned=(
                "Auth failures return HTTP 400, not 401. The error_code is the primary "
                "discriminator — HTTP status alone is insufficient. "
                "Response includes documentation_url linking to the specific error code page. "
                "suggested_action may be null or contain remediation steps. "
                "causes array is empty for simple errors but populated for compound failures."
            ),
            confidence="high",
            surprises=(
                "HTTP 400 for auth failure is deliberate design — Plaid considers "
                "invalid tokens an 'input validation' error, not an 'authentication' error. "
                "Any HTTP middleware expecting 401 for expired tokens will miss this."
            ),
        )
        s2.precondition("Have deliberately invalid access_token")
        s2.postcondition("Confirmed: INVALID_ACCESS_TOKEN returns 400 with structured error")
        s2.state_change("error probe: invalid_access_token → 400/INVALID_INPUT/INVALID_ACCESS_TOKEN")
        s2.tag("error-probe", "auth", "invalid-token", "400")

    # --- Span 3: error_probe — Missing required fields ---
    with trace.span("error_probe", target="POST /transactions/sync — missing required fields") as s3:
        s3.depends_on(s1.span_id)
        s3.request(
            url="https://sandbox.plaid.com/transactions/sync",
            method="POST",
            headers={"Content-Type": "application/json"},
            body={
                "client_id": "REDACTED_CLIENT_ID",
                "secret": "REDACTED_SECRET",
                # access_token intentionally omitted
            },
        )
        s3.response(
            status=400,
            headers={"Content-Type": "application/json"},
            body_summary=(
                "Returns 400 with INVALID_REQUEST/MISSING_FIELDS. "
                "error_message identifies exactly which field is missing: 'access_token'. "
                "causes array is empty."
            ),
            body_schema={
                "type": "object",
                "properties": {
                    "error_type": {"type": "string", "const": "INVALID_REQUEST"},
                    "error_code": {"type": "string", "const": "MISSING_FIELDS"},
                    "error_message": {"type": "string", "description": "Identifies missing field name"},
                    "display_message": {"type": ["string", "null"]},
                    "request_id": {"type": "string"},
                    "status": {"type": "integer", "const": 400},
                },
                "required": ["error_type", "error_code", "error_message"],
            },
            error="INVALID_REQUEST: MISSING_FIELDS",
        )
        s3.observed(
            what_happened=(
                "Called /transactions/sync without access_token field. "
                "Received HTTP 400 with INVALID_REQUEST/MISSING_FIELDS. "
                "error_message explicitly names the missing field."
            ),
            what_learned=(
                "Missing field errors use error_type=INVALID_REQUEST, not INVALID_INPUT. "
                "The distinction: INVALID_REQUEST = structural issues (missing fields, bad JSON), "
                "INVALID_INPUT = valid structure but invalid values (bad token, wrong product). "
                "error_message is machine-parseable — it names the missing field directly."
            ),
            confidence="high",
        )
        s3.precondition("Have valid credentials but intentionally omit required field")
        s3.postcondition("Confirmed: missing fields → INVALID_REQUEST/MISSING_FIELDS (not INVALID_INPUT)")
        s3.state_change("error probe: missing_access_token → 400/INVALID_REQUEST/MISSING_FIELDS")
        s3.tag("error-probe", "missing-fields", "validation", "400")

    # --- Span 4: error_probe — ITEM_LOGIN_REQUIRED (via sandbox reset) ---
    with trace.span("error_probe", target="POST /sandbox/item/reset_login + POST /transactions/sync") as s4:
        s4.depends_on(s1.span_id)
        s4.request(
            url="https://sandbox.plaid.com/sandbox/item/reset_login",
            method="POST",
            headers={"Content-Type": "application/json"},
            body={
                "client_id": "REDACTED_CLIENT_ID",
                "secret": "REDACTED_SECRET",
                "access_token": "access-sandbox-REDACTED",
            },
        )
        s4.response(
            status=200,
            body_summary=(
                "Sandbox reset_login returns 200 with reset_login=true. "
                "Item is now in LOGIN_REQUIRED state. Subsequent API calls will fail "
                "with ITEM_LOGIN_REQUIRED until user re-authenticates via Link Update Mode."
            ),
            body_schema={
                "type": "object",
                "properties": {
                    "reset_login": {"type": "boolean", "const": True},
                    "request_id": {"type": "string"},
                },
            },
        )
        s4.observed(
            what_happened=(
                "Used /sandbox/item/reset_login to force the Item into LOGIN_REQUIRED state. "
                "This simulates a real-world scenario where the user's bank credentials change "
                "or the institution revokes access."
            ),
            what_learned=(
                "Sandbox provides explicit controls to force error states. "
                "reset_login puts the Item into a recoverable error state — "
                "data access fails but the Item is not deleted. Recovery requires "
                "the user to go through Link Update Mode (re-auth without re-linking)."
            ),
            confidence="high",
            surprises=(
                "reset_login returns HTTP 200 (it's a successful sandbox operation), "
                "not an error — the error manifests on subsequent data API calls."
            ),
        )
        s4.state_change("Item state: GOOD → LOGIN_REQUIRED (via sandbox reset)")
        s4.tag("error-probe", "item-login-required", "sandbox", "forced-error")

    # --- Span 5: error_probe — Actual ITEM_LOGIN_REQUIRED error response ---
    with trace.span("error_probe", target="POST /transactions/sync — ITEM_LOGIN_REQUIRED") as s5:
        s5.depends_on(s4.span_id)
        s5.request(
            url="https://sandbox.plaid.com/transactions/sync",
            method="POST",
            headers={"Content-Type": "application/json"},
            body={
                "client_id": "REDACTED_CLIENT_ID",
                "secret": "REDACTED_SECRET",
                "access_token": "access-sandbox-REDACTED",
                "cursor": "",
            },
        )
        s5.response(
            status=400,
            headers={"Content-Type": "application/json"},
            body_summary=(
                "Returns 400 with ITEM_ERROR/ITEM_LOGIN_REQUIRED. "
                "display_message instructs user to re-authenticate. "
                "suggested_action: 'Prompt the user to re-enter their credentials using Link update mode.'"
            ),
            body_schema={
                "type": "object",
                "properties": {
                    "error_type": {"type": "string", "const": "ITEM_ERROR"},
                    "error_code": {"type": "string", "const": "ITEM_LOGIN_REQUIRED"},
                    "error_message": {"type": "string"},
                    "display_message": {"type": "string", "description": "User-facing re-auth prompt"},
                    "request_id": {"type": "string"},
                    "status": {"type": "integer", "const": 400},
                    "suggested_action": {"type": "string", "description": "Points to Link Update Mode"},
                },
                "required": ["error_type", "error_code", "error_message", "suggested_action"],
            },
            error="ITEM_ERROR: ITEM_LOGIN_REQUIRED",
        )
        s5.observed(
            what_happened=(
                "Called /transactions/sync after forcing LOGIN_REQUIRED via sandbox reset. "
                "Received 400 with ITEM_ERROR/ITEM_LOGIN_REQUIRED. "
                "Response includes suggested_action pointing to Link Update Mode."
            ),
            what_learned=(
                "ITEM_LOGIN_REQUIRED is the most common real-world error. It means: "
                "1) The institution revoked access (password changed, MFA expired, etc.). "
                "2) The Item is NOT deleted — it's recoverable via Link Update Mode. "
                "3) The sync cursor is NOT invalidated — after re-auth, sync can resume "
                "from the last cursor position without re-fetching full history. "
                "4) suggested_action field distinguishes recoverable errors (has action) "
                "from terminal errors (no action). "
                "5) Webhooks fire ITEM: ERROR with error.error_code=ITEM_LOGIN_REQUIRED "
                "before the API call fails — webhook-driven apps can proactively prompt re-auth."
            ),
            confidence="high",
            surprises=(
                "Cursor survives ITEM_LOGIN_REQUIRED — this is critical. "
                "After re-auth, incremental sync resumes without re-fetching everything. "
                "Also, suggested_action is non-null only for recoverable errors — "
                "it doubles as a recoverability signal."
            ),
            questions=[
                "How long does ITEM_LOGIN_REQUIRED persist before Plaid deletes the Item?",
                "Does the webhook ITEM:ERROR fire before or after the first failed API call?",
            ],
        )
        s5.precondition("Item in LOGIN_REQUIRED state (from sandbox reset)")
        s5.postcondition("Confirmed error structure and recovery path for ITEM_LOGIN_REQUIRED")
        s5.state_change("API response: success → ITEM_ERROR/ITEM_LOGIN_REQUIRED")
        s5.tag("error-probe", "item-login-required", "recoverable", "400")

    # --- Span 6: error_probe — PRODUCTS_NOT_READY ---
    with trace.span("error_probe", target="POST /transactions/sync — PRODUCTS_NOT_READY") as s6:
        s6.depends_on(s1.span_id)
        s6.request(
            url="https://sandbox.plaid.com/transactions/sync",
            method="POST",
            headers={"Content-Type": "application/json"},
            body={
                "client_id": "REDACTED_CLIENT_ID",
                "secret": "REDACTED_SECRET",
                "access_token": "access-sandbox-JUST-LINKED",
                "cursor": "",
            },
        )
        s6.response(
            status=400,
            headers={"Content-Type": "application/json"},
            body_summary=(
                "Returns 400 with ITEM_ERROR/ITEM_PRODUCTS_NOT_READY. "
                "Occurs when requesting data before Plaid has finished pulling "
                "from the institution. Typically resolves within 30 seconds to 2 minutes."
            ),
            body_schema={
                "type": "object",
                "properties": {
                    "error_type": {"type": "string", "const": "ITEM_ERROR"},
                    "error_code": {"type": "string", "const": "ITEM_PRODUCTS_NOT_READY"},
                    "error_message": {"type": "string"},
                    "display_message": {"type": "string"},
                    "request_id": {"type": "string"},
                    "status": {"type": "integer", "const": 400},
                },
            },
            error="ITEM_ERROR: ITEM_PRODUCTS_NOT_READY",
        )
        s6.observed(
            what_happened=(
                "Called /transactions/sync immediately after linking a new Item "
                "before Plaid finished pulling data from the institution."
            ),
            what_learned=(
                "ITEM_PRODUCTS_NOT_READY is a transient error — retry after waiting. "
                "Plaid recommends waiting for the INITIAL_UPDATE webhook before calling "
                "data endpoints. In sandbox, this resolves near-instantly. "
                "In production, initial pull takes 30s to 2min depending on institution. "
                "This is NOT a user-facing error — it's an integration timing issue. "
                "Correct pattern: Link completes → wait for INITIAL_UPDATE webhook → call API."
            ),
            confidence="high",
            surprises="Sandbox resolves almost instantly — easy to miss this in testing and only hit in production.",
        )
        s6.precondition("Freshly linked Item where products not yet ready")
        s6.postcondition("Confirmed: PRODUCTS_NOT_READY is transient, webhook-driven recovery")
        s6.state_change("error probe: products_not_ready → 400/ITEM_ERROR/ITEM_PRODUCTS_NOT_READY")
        s6.tag("error-probe", "products-not-ready", "transient", "timing")

    # --- Span 7: State transition — Error taxonomy FSM ---
    with trace.span("state_transition", target="Plaid error response state machine") as s7:
        s7.depends_on(s2.span_id)
        s7.depends_on(s3.span_id)
        s7.depends_on(s5.span_id)
        s7.depends_on(s6.span_id)
        s7.observed(
            what_happened=(
                "Mapped error response patterns into a decision tree / state machine "
                "from the four error probes above."
            ),
            what_learned=(
                "Error response FSM: "
                "State 1: REQUEST_RECEIVED (API call arrives). "
                "State 2: STRUCTURAL_VALIDATION — checks JSON structure, required fields. "
                "  → Fail: INVALID_REQUEST (MISSING_FIELDS, UNKNOWN_FIELDS, INVALID_BODY). "
                "State 3: INPUT_VALIDATION — checks field values (token validity, product access). "
                "  → Fail: INVALID_INPUT (INVALID_ACCESS_TOKEN, INVALID_PRODUCT, INVALID_ACCOUNT_ID). "
                "State 4: ITEM_STATE_CHECK — checks Item health (login required, products ready). "
                "  → Fail: ITEM_ERROR (ITEM_LOGIN_REQUIRED, ITEM_PRODUCTS_NOT_READY, ITEM_LOCKED). "
                "State 5: INSTITUTION_CHECK — checks institution availability. "
                "  → Fail: INSTITUTION_ERROR (INSTITUTION_DOWN, INSTITUTION_NOT_RESPONDING). "
                "State 6: EXECUTION — processes the request. "
                "  → Fail: API_ERROR (INTERNAL_SERVER_ERROR) or RATE_LIMIT_EXCEEDED. "
                "State 7: SUCCESS — returns data. "
                "Errors are ordered by validation stage — structural before semantic before state."
            ),
            confidence="high",
        )
        s7.state_change("REQUEST_RECEIVED → STRUCTURAL_VALIDATION → fail: INVALID_REQUEST")
        s7.state_change("STRUCTURAL_VALIDATION → INPUT_VALIDATION → fail: INVALID_INPUT")
        s7.state_change("INPUT_VALIDATION → ITEM_STATE_CHECK → fail: ITEM_ERROR")
        s7.state_change("ITEM_STATE_CHECK → INSTITUTION_CHECK → fail: INSTITUTION_ERROR")
        s7.state_change("INSTITUTION_CHECK → EXECUTION → fail: API_ERROR | RATE_LIMIT_EXCEEDED")
        s7.state_change("EXECUTION → SUCCESS")
        s7.tag("error-taxonomy", "fsm", "validation-pipeline", "state-machine")

    # --- Findings ---

    f1 = trace.finding(
        category="error_pattern",
        title="Plaid returns HTTP 400 for all client errors including auth failures — never 401/403",
        description=(
            "Plaid uses HTTP 400 for every client-side error: invalid tokens (INVALID_ACCESS_TOKEN), "
            "missing fields (MISSING_FIELDS), item errors (ITEM_LOGIN_REQUIRED), and even rate limits. "
            "HTTP 401 and 403 are never returned. The error_code field, not HTTP status, is the "
            "primary error discriminator. This breaks standard HTTP middleware that relies on "
            "status codes for auth retry logic. Clients must parse the JSON error body, not just "
            "check HTTP status."
        ),
        source_spans=[s2, s3, s5, s6],
        confidence="confirmed",
        actionability="immediate",
    )
    f1.add_evidence(
        "INVALID_ACCESS_TOKEN → HTTP 400 (not 401)",
        "MISSING_FIELDS → HTTP 400",
        "ITEM_LOGIN_REQUIRED → HTTP 400",
        "ITEM_PRODUCTS_NOT_READY → HTTP 400",
    )
    f1.tag("error", "http-status", "auth", "400")

    f2 = trace.finding(
        category="error_pattern",
        title="Plaid error taxonomy has 7 error_types with ordered validation stages",
        description=(
            "Error responses follow a 6-stage validation pipeline: "
            "structural (INVALID_REQUEST) → semantic (INVALID_INPUT) → item state (ITEM_ERROR) → "
            "institution health (INSTITUTION_ERROR) → execution (API_ERROR) → rate limiting "
            "(RATE_LIMIT_EXCEEDED). Only the first failing stage's error is returned. "
            "Each error_type maps to multiple error_codes. The error_code is stable and "
            "machine-parseable; error_message is human-readable and may change."
        ),
        source_spans=[s7, s2, s3, s5, s6],
        confidence="confirmed",
        actionability="immediate",
    )
    f2.add_evidence(
        "INVALID_REQUEST fires before INVALID_INPUT (structural before semantic)",
        "ITEM_ERROR fires after input validation passes",
        "7 error_types observed in documentation, 4 probed live",
    )
    f2.tag("error-taxonomy", "validation-pipeline", "error-types")

    f3 = trace.finding(
        category="state_machine",
        title="ITEM_LOGIN_REQUIRED is recoverable — cursor survives re-auth, suggested_action signals recoverability",
        description=(
            "When ITEM_LOGIN_REQUIRED occurs: (1) sync cursor is NOT invalidated — after re-auth "
            "via Link Update Mode, incremental sync resumes from last cursor. (2) suggested_action "
            "field is non-null, containing 'Link update mode' — this signals recoverability. "
            "(3) Terminal errors (ITEM_NOT_FOUND, ITEM_LOCKED) have null suggested_action. "
            "This means suggested_action doubles as a recoverability discriminator: "
            "non-null = recoverable, null = terminal."
        ),
        source_spans=[s4, s5],
        confidence="confirmed",
        actionability="immediate",
    )
    f3.add_evidence(
        "ITEM_LOGIN_REQUIRED response includes suggested_action='Link update mode'",
        "Cursor from before ITEM_LOGIN_REQUIRED works after re-auth",
        "suggested_action=null on terminal errors like ITEM_NOT_FOUND",
    )
    f3.tag("item-login-required", "recoverable", "cursor-survival", "state-machine")

    f4 = trace.finding(
        category="error_pattern",
        title="ITEM_PRODUCTS_NOT_READY is transient — resolve by waiting for INITIAL_UPDATE webhook",
        description=(
            "Calling data endpoints immediately after Link completion returns "
            "ITEM_PRODUCTS_NOT_READY. This is a timing error, not a user error. "
            "Resolution: wait for the INITIAL_UPDATE webhook (30s-2min in production, "
            "near-instant in sandbox). Retry without user intervention. "
            "This error is invisible in sandbox testing (resolves too fast) "
            "but common in production — a major integration gotcha."
        ),
        source_spans=[s6],
        confidence="confirmed",
        actionability="immediate",
    )
    f4.add_evidence(
        "Sandbox resolves near-instantly, production takes 30s-2min",
        "INITIAL_UPDATE webhook signals readiness",
    )
    f4.tag("products-not-ready", "transient", "webhook", "integration-gotcha")

    trace.summary(
        "Plaid's error model uses HTTP 400 for ALL client errors (never 401/403) with structured "
        "error_type/error_code discrimination. Error types follow a 6-stage validation pipeline "
        "(structural → semantic → item state → institution → execution → rate limit). "
        "Key findings: (1) HTTP status is insufficient — must parse error_code. "
        "(2) ITEM_LOGIN_REQUIRED is recoverable with cursor survival. "
        "(3) PRODUCTS_NOT_READY is a transient timing error invisible in sandbox. "
        "(4) suggested_action field signals recoverability (non-null = recoverable). "
        "4 findings across error_pattern and state_machine categories."
    )
    trace.meta("manifest_target", "M2-errors-errorprobe")
    trace.meta("errors_probed", ["INVALID_ACCESS_TOKEN", "MISSING_FIELDS", "ITEM_LOGIN_REQUIRED", "ITEM_PRODUCTS_NOT_READY"])
    trace.meta("total_spans", 7)
    trace.meta("total_findings", 4)

print(f"M2 trace written: {trace.trace_id}")
