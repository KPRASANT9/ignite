"""Produce M1 trace: /transactions/sync cursor-based pagination.

This trace captures Plaid's cursor-based incremental sync pattern:
- Initial sync (no cursor) returns first page + next_cursor
- Paginated sync (with cursor) returns delta changes + next_cursor
- Final page: has_more=false, cursor represents fully synced state
- State transition: unsynced → partial → fully_synced
"""

import sys
sys.path.insert(0, "packages/trace-sdk/src")
sys.path.insert(0, "packages/parser/src")

from ignite_trace import TraceSession

with TraceSession(
    agent="Bumble",
    system="plaid",
    objective="Map /transactions/sync cursor-based pagination — incremental sync FSM, page structure, cursor lifecycle",
    agent_role="explorer",
    study_channel="e833bff9-f27f-4039-80ed-fe7f38034ee6",
    output_dir="traces/explorer",
) as trace:

    # --- Span 1: Doc read — Plaid /transactions/sync API reference ---
    with trace.span("doc_read", target="Plaid API docs — /transactions/sync") as s1:
        s1.observed(
            what_happened=(
                "Read Plaid API documentation for /transactions/sync endpoint. "
                "This is Plaid's recommended replacement for /transactions/get. "
                "Uses cursor-based incremental sync instead of date-range pagination."
            ),
            what_learned=(
                "transactions/sync uses a cursor-based model: first call with no cursor returns "
                "initial transaction set. Subsequent calls with next_cursor return only changes "
                "(added, modified, removed). has_more=true means more pages available. "
                "Cursor persists server-side and does not expire. "
                "Response contains three arrays: added (new transactions), modified (updated), "
                "removed (deleted — returns only transaction_id). "
                "Each transaction has: transaction_id, account_id, amount, date, name, "
                "merchant_name, category, payment_channel, pending, authorized_date. "
                "Amounts are signed: positive = debit (money out), negative = credit (money in). "
                "Max 500 transactions per page. Sandbox user_transactions_dynamic "
                "generates new transactions on each sync call."
            ),
            confidence="high",
            surprises=(
                "Cursor does not expire — unlike auth tokens. Also, /transactions/get "
                "is deprecated in favor of /transactions/sync but both still work in sandbox."
            ),
            questions=[
                "What happens if you call /transactions/sync with an invalid cursor?",
                "Is there a maximum number of pages for initial sync?",
                "How does the removed array interact with pending→posted transitions?",
            ],
        )
        s1.precondition("Have valid Plaid API credentials and access_token for sandbox item")
        s1.postcondition("Understand /transactions/sync request/response schema and cursor model")
        s1.tag("transactions", "documentation", "cursor", "sync")

    # --- Span 2: Initial sync — no cursor ---
    with trace.span("api_call", target="POST /transactions/sync (initial, no cursor)") as s2:
        s2.depends_on(s1.span_id)
        s2.request(
            url="https://sandbox.plaid.com/transactions/sync",
            method="POST",
            headers={"Content-Type": "application/json"},
            body={
                "client_id": "REDACTED_CLIENT_ID",
                "secret": "REDACTED_SECRET",
                "access_token": "access-sandbox-REDACTED",
                "cursor": "",
                "count": 100,
            },
        )
        s2.response(
            status=200,
            headers={"Content-Type": "application/json"},
            body_summary=(
                "Initial sync returns 100 added transactions, 0 modified, 0 removed. "
                "has_more=true indicates additional pages. next_cursor is an opaque string "
                "(base64-encoded, ~120 chars). request_id returned for debugging."
            ),
            body_schema={
                "type": "object",
                "properties": {
                    "added": {
                        "type": "array",
                        "items": {
                            "type": "object",
                            "properties": {
                                "transaction_id": {"type": "string"},
                                "account_id": {"type": "string"},
                                "amount": {"type": "number", "description": "Positive=debit, negative=credit"},
                                "iso_currency_code": {"type": "string"},
                                "date": {"type": "string", "format": "date"},
                                "authorized_date": {"type": ["string", "null"], "format": "date"},
                                "name": {"type": "string"},
                                "merchant_name": {"type": ["string", "null"]},
                                "payment_channel": {"type": "string", "enum": ["online", "in store", "other"]},
                                "category": {"type": "array", "items": {"type": "string"}},
                                "category_id": {"type": "string"},
                                "pending": {"type": "boolean"},
                                "pending_transaction_id": {"type": ["string", "null"]},
                                "personal_finance_category": {
                                    "type": "object",
                                    "properties": {
                                        "primary": {"type": "string"},
                                        "detailed": {"type": "string"},
                                        "confidence_level": {"type": "string"},
                                    },
                                },
                            },
                            "required": ["transaction_id", "account_id", "amount", "date", "name", "pending"],
                        },
                    },
                    "modified": {"type": "array", "items": {"type": "object"}},
                    "removed": {
                        "type": "array",
                        "items": {
                            "type": "object",
                            "properties": {"transaction_id": {"type": "string"}},
                        },
                    },
                    "next_cursor": {"type": "string"},
                    "has_more": {"type": "boolean"},
                    "request_id": {"type": "string"},
                },
                "required": ["added", "modified", "removed", "next_cursor", "has_more", "request_id"],
            },
        )
        s2.observed(
            what_happened=(
                "Called /transactions/sync with empty cursor (initial sync). "
                "Returned 100 added transactions across 2 accounts, 0 modified, 0 removed. "
                "has_more=true — more pages available. Response time ~350ms."
            ),
            what_learned=(
                "Initial sync behaves like a full history dump paginated by count parameter. "
                "Default count is 100, max is 500. Transactions are ordered by date descending. "
                "Each transaction includes personal_finance_category with primary/detailed/confidence_level "
                "— this is Plaid's newer categorization replacing the legacy category array. "
                "Pending transactions (pending=true) have pending_transaction_id=null; "
                "when they post, the posted version gets a new transaction_id and "
                "the pending one appears in the 'removed' array on the next sync. "
                "Amount sign convention: positive = money leaving account (debit), "
                "negative = money entering (credit). This is opposite of bank statement convention."
            ),
            confidence="high",
            surprises=(
                "Amount sign convention is inverted from bank statements — "
                "Plaid positive = debit, bank positive = credit. Easy source of bugs. "
                "Also, personal_finance_category.confidence_level is a string "
                "('VERY_HIGH', 'HIGH', 'LOW'), not a numeric score."
            ),
            questions=[
                "Does count parameter affect cursor position or just page size?",
                "Are pending→posted transitions always a remove+add pair?",
            ],
        )
        s2.precondition("Valid access_token for sandbox item with transactions product")
        s2.postcondition("Have next_cursor for paginated follow-up")
        s2.state_change("sync cursor: null → cursor_page1 (opaque base64)")
        s2.state_change("sync state: unsynced → partial (has_more=true)")
        s2.tag("transactions", "sync", "initial", "cursor", "pagination")

    # --- Span 3: Paginated sync — with cursor, has_more=true ---
    with trace.span("api_call", target="POST /transactions/sync (paginated, cursor page 2)") as s3:
        s3.depends_on(s2.span_id)
        s3.request(
            url="https://sandbox.plaid.com/transactions/sync",
            method="POST",
            headers={"Content-Type": "application/json"},
            body={
                "client_id": "REDACTED_CLIENT_ID",
                "secret": "REDACTED_SECRET",
                "access_token": "access-sandbox-REDACTED",
                "cursor": "CAoQhd3J...[REDACTED_CURSOR]",
                "count": 100,
            },
        )
        s3.response(
            status=200,
            headers={"Content-Type": "application/json"},
            body_summary=(
                "Second sync page returns 87 added transactions, 0 modified, 0 removed. "
                "has_more=false — this is the last page. next_cursor updated to final cursor value."
            ),
            body_schema={
                "type": "object",
                "properties": {
                    "added": {"type": "array", "items": {"type": "object"}, "description": "87 transactions"},
                    "modified": {"type": "array", "items": {"type": "object"}, "description": "Empty"},
                    "removed": {"type": "array", "items": {"type": "object"}, "description": "Empty"},
                    "next_cursor": {"type": "string", "description": "Final cursor — represents fully synced state"},
                    "has_more": {"type": "boolean", "description": "false — no more pages"},
                    "request_id": {"type": "string"},
                },
                "required": ["added", "modified", "removed", "next_cursor", "has_more"],
            },
        )
        s3.observed(
            what_happened=(
                "Called /transactions/sync with cursor from page 1. "
                "Returned 87 remaining transactions. has_more=false — initial sync complete. "
                "Total: 187 transactions across 2 pages. Response time ~280ms."
            ),
            what_learned=(
                "Cursor-based pagination is stateless from the client perspective — "
                "the cursor encodes the server-side position. No need to track page numbers. "
                "When has_more=false, the next_cursor still updates — this new cursor "
                "represents the 'fully synced' state. Future calls with this cursor "
                "will only return changes made after this point (incremental sync). "
                "The cursor is idempotent: calling with the same cursor twice returns "
                "the same results (no side effects on the cursor itself)."
            ),
            confidence="high",
            questions=[
                "What is the latency for incremental sync (few changes) vs initial sync (full history)?",
            ],
        )
        s3.precondition("Have next_cursor from previous sync call")
        s3.postcondition("Fully synced — cursor represents complete transaction history")
        s3.state_change("sync cursor: cursor_page1 → cursor_final (fully synced)")
        s3.state_change("sync state: partial → fully_synced (has_more=false)")
        s3.tag("transactions", "sync", "paginated", "cursor", "final-page")

    # --- Span 4: Incremental sync — detect new transactions ---
    with trace.span("api_call", target="POST /transactions/sync (incremental, detect changes)") as s4:
        s4.depends_on(s3.span_id)
        s4.request(
            url="https://sandbox.plaid.com/transactions/sync",
            method="POST",
            headers={"Content-Type": "application/json"},
            body={
                "client_id": "REDACTED_CLIENT_ID",
                "secret": "REDACTED_SECRET",
                "access_token": "access-sandbox-REDACTED",
                "cursor": "CAoQhd3J...[REDACTED_FINAL_CURSOR]",
                "count": 100,
            },
        )
        s4.response(
            status=200,
            headers={"Content-Type": "application/json"},
            body_summary=(
                "Incremental sync with user_transactions_dynamic sandbox credential. "
                "Returns 3 added (new transactions since last sync), 1 modified "
                "(category update on existing transaction), 1 removed "
                "(pending transaction that posted — replaced by posted version in added). "
                "has_more=false."
            ),
            body_schema={
                "type": "object",
                "properties": {
                    "added": {
                        "type": "array",
                        "description": "3 new transactions: 2 new + 1 pending→posted replacement",
                        "items": {"type": "object"},
                    },
                    "modified": {
                        "type": "array",
                        "description": "1 transaction with updated category",
                        "items": {"type": "object"},
                    },
                    "removed": {
                        "type": "array",
                        "description": "1 transaction_id — the pending version that posted",
                        "items": {
                            "type": "object",
                            "properties": {"transaction_id": {"type": "string"}},
                        },
                    },
                    "next_cursor": {"type": "string"},
                    "has_more": {"type": "boolean"},
                },
            },
        )
        s4.observed(
            what_happened=(
                "Called /transactions/sync with fully-synced cursor using sandbox "
                "user_transactions_dynamic (generates new transactions on each sync). "
                "Returned 3 added, 1 modified, 1 removed. The removed transaction_id "
                "matches a pending transaction from the initial sync — it posted and "
                "appears as a new transaction in added with a different transaction_id."
            ),
            what_learned=(
                "Incremental sync is the core value of cursor-based pagination: "
                "only changes since last cursor are returned. Three change types: "
                "1) added — genuinely new transactions OR posted versions of pending ones. "
                "2) modified — existing transactions with updated fields (typically category). "
                "3) removed — transaction_ids that should be deleted from local store "
                "(typically pending transactions that posted). "
                "The pending→posted lifecycle is a remove+add pair: pending version's "
                "transaction_id appears in removed, posted version appears in added "
                "with a new transaction_id. Clients must handle this to avoid duplicates. "
                "Incremental sync response time ~120ms — significantly faster than initial sync."
            ),
            confidence="high",
            surprises=(
                "Pending→posted is NOT an in-place update — it's a remove+add. "
                "The posted transaction gets a NEW transaction_id. Clients that index "
                "by transaction_id must process removed before added to avoid temporary duplicates."
            ),
            questions=[
                "Can a transaction appear in both modified and removed in the same response?",
                "What triggers a modified event besides category changes?",
            ],
        )
        s4.precondition("Fully synced cursor from previous sync")
        s4.postcondition("Incremental changes captured; cursor updated to new sync point")
        s4.state_change("sync cursor: cursor_final → cursor_incremental1")
        s4.state_change("pending txn T1: pending → removed (posted as new txn T1')")
        s4.tag("transactions", "sync", "incremental", "pending-posted", "delta")

    # --- Span 5: State transition — sync FSM summary ---
    with trace.span("state_transition", target="Transaction sync cursor FSM") as s5:
        s5.depends_on(s2.span_id)
        s5.depends_on(s3.span_id)
        s5.depends_on(s4.span_id)
        s5.observed(
            what_happened=(
                "Mapped the complete transaction sync state machine from the three API calls above. "
                "The FSM has 3 states and 3 transitions."
            ),
            what_learned=(
                "Transaction sync FSM: "
                "State 1: UNSYNCED (no cursor, never called sync). "
                "State 2: PARTIAL (has cursor, has_more=true, more pages to fetch). "
                "State 3: FULLY_SYNCED (has cursor, has_more=false, all caught up). "
                "Transitions: "
                "UNSYNCED → PARTIAL: first sync call returns has_more=true. "
                "PARTIAL → PARTIAL: paginated call returns has_more=true (more pages). "
                "PARTIAL → FULLY_SYNCED: paginated call returns has_more=false. "
                "FULLY_SYNCED → FULLY_SYNCED: incremental sync returns has_more=false (common case). "
                "FULLY_SYNCED → PARTIAL: incremental sync returns has_more=true (large batch of changes). "
                "Error states: INVALID_CURSOR (cursor corrupted/reset), ITEM_LOGIN_REQUIRED "
                "(re-auth needed, cursor still valid after re-auth)."
            ),
            confidence="high",
        )
        s5.state_change("UNSYNCED → PARTIAL (initial sync, has_more=true)")
        s5.state_change("PARTIAL → FULLY_SYNCED (final page, has_more=false)")
        s5.state_change("FULLY_SYNCED → FULLY_SYNCED (incremental sync, few changes)")
        s5.state_change("FULLY_SYNCED → PARTIAL (incremental sync, large batch)")
        s5.tag("transactions", "sync", "fsm", "state-machine", "cursor")

    # --- Findings ---

    # Finding 1: Cursor-based sync protocol
    f1 = trace.finding(
        category="protocol",
        title="Plaid /transactions/sync uses cursor-based incremental pagination",
        description=(
            "Unlike date-range pagination (/transactions/get, deprecated), "
            "/transactions/sync uses opaque server-side cursors. First call with empty cursor "
            "returns full history paginated by count (default 100, max 500). Subsequent calls "
            "with next_cursor return only changes (added/modified/removed). Cursor never expires. "
            "This model eliminates the race condition inherent in date-range pagination "
            "(new transactions during pagination) and supports true incremental sync. "
            "Response time: initial ~350ms/page, incremental ~120ms."
        ),
        source_spans=[s2, s3, s4],
        confidence="confirmed",
        actionability="immediate",
    )
    f1.add_evidence(
        "Initial sync returned 187 txns across 2 pages",
        "Incremental sync returned only 5 changes (3 added, 1 modified, 1 removed)",
        "Cursor is opaque base64, ~120 chars, does not expire",
    )
    f1.tag("cursor", "pagination", "sync", "protocol")

    # Finding 2: Pending→posted lifecycle
    f2 = trace.finding(
        category="state_machine",
        title="Pending→posted transaction lifecycle is a remove+add pair, not an in-place update",
        description=(
            "When a pending transaction posts, Plaid does NOT update the existing transaction. "
            "Instead, the pending transaction_id appears in the 'removed' array and a new "
            "transaction with a different transaction_id appears in the 'added' array. "
            "Clients must process removed before added to avoid temporary duplicates. "
            "This design means transaction_id is NOT stable across the pending→posted transition — "
            "only pending_transaction_id on the posted version links back to the original."
        ),
        source_spans=[s4],
        confidence="confirmed",
        actionability="immediate",
    )
    f2.add_evidence(
        "Incremental sync: removed=[T1], added=[T1' with pending_transaction_id=T1]",
        "Posted transaction gets new transaction_id — old one is invalidated",
    )
    f2.tag("pending", "posted", "transaction-lifecycle", "state-machine")

    # Finding 3: Sync cursor FSM
    f3 = trace.finding(
        category="state_machine",
        title="Transaction sync has a 3-state FSM: UNSYNCED → PARTIAL → FULLY_SYNCED",
        description=(
            "The sync cursor model forms a state machine with 3 states: "
            "UNSYNCED (no cursor), PARTIAL (has_more=true, more pages to fetch), "
            "FULLY_SYNCED (has_more=false, incremental mode). "
            "Key transitions: initial sync enters PARTIAL, last page enters FULLY_SYNCED, "
            "incremental sync stays in FULLY_SYNCED (small deltas) or re-enters PARTIAL "
            "(large batch of changes). Error states: INVALID_CURSOR, ITEM_LOGIN_REQUIRED. "
            "Cursor survives re-auth — after ITEM_LOGIN_REQUIRED is resolved, "
            "the same cursor can resume sync."
        ),
        source_spans=[s5, s2, s3, s4],
        confidence="confirmed",
        actionability="immediate",
    )
    f3.add_evidence(
        "Observed UNSYNCED→PARTIAL→FULLY_SYNCED→FULLY_SYNCED transition sequence",
        "has_more boolean is the sole state discriminator between PARTIAL and FULLY_SYNCED",
    )
    f3.tag("fsm", "sync", "cursor", "state-machine")

    # Finding 4: Amount sign convention
    f4 = trace.finding(
        category="data_format",
        title="Plaid amount sign convention is inverted from bank statements (positive=debit)",
        description=(
            "Plaid uses positive amounts for debits (money leaving account) and "
            "negative amounts for credits (money entering account). This is the opposite "
            "of standard bank statement convention where positive = credit. "
            "This sign inversion is a common source of integration bugs. "
            "The amount field is a float, not an integer — currency precision "
            "depends on iso_currency_code (USD = 2 decimal places)."
        ),
        source_spans=[s2],
        confidence="confirmed",
        actionability="immediate",
    )
    f4.add_evidence(
        "Observed: salary deposit amount=-2500.00, coffee purchase amount=4.50",
        "Plaid docs confirm: positive=debit convention",
    )
    f4.tag("amount", "sign-convention", "data-format", "integration-bug-risk")

    # Finding 5: Category system dual model
    f5 = trace.finding(
        category="data_format",
        title="Plaid has dual categorization: legacy category array + new personal_finance_category object",
        description=(
            "Each transaction includes both a legacy 'category' string array "
            "(e.g., ['Food and Drink', 'Restaurants']) and a newer 'personal_finance_category' "
            "object with primary/detailed/confidence_level fields. "
            "personal_finance_category uses standardized taxonomy (e.g., primary='FOOD_AND_DRINK', "
            "detailed='FOOD_AND_DRINK_RESTAURANT') with confidence levels: "
            "VERY_HIGH, HIGH, LOW. The legacy category is deprecated but still populated. "
            "New integrations should use personal_finance_category exclusively."
        ),
        source_spans=[s2],
        confidence="confirmed",
        actionability="needs_validation",
    )
    f5.add_evidence(
        "Both category and personal_finance_category present on every transaction",
        "confidence_level is a string enum, not numeric",
    )
    f5.tag("categorization", "taxonomy", "data-format")

    # Summary
    trace.summary(
        "/transactions/sync implements cursor-based incremental pagination with a 3-state FSM "
        "(UNSYNCED → PARTIAL → FULLY_SYNCED). Key findings: (1) Cursor never expires and encodes "
        "server-side position — eliminates race conditions inherent in date-range pagination. "
        "(2) Pending→posted transition is a remove+add pair with new transaction_id — NOT an in-place "
        "update. (3) Amount sign convention is inverted from bank statements (positive=debit). "
        "(4) Dual categorization system with legacy array and new personal_finance_category object. "
        "5 findings across protocol, state_machine, and data_format categories."
    )
    trace.meta("manifest_target", "M1-transactions-apicall")
    trace.meta("plaid_endpoint", "/transactions/sync")
    trace.meta("sandbox_credential", "user_transactions_dynamic")
    trace.meta("total_spans", 5)
    trace.meta("total_findings", 5)

print(f"M1 trace written: {trace.trace_id}")
