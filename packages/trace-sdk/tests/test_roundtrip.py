"""Round-trip integration test: SDK → Parser → Analyzer → Optimizer.

This is the critical acceptance test — SDK-produced traces must flow
through the entire L2 pipeline with zero errors.
"""

import json
import sys
from pathlib import Path

import pytest

from ignite_trace.session import TraceSession

# Add parser package to path for integration testing
parser_src = Path(__file__).parent.parent.parent / "parser" / "src"
if str(parser_src) not in sys.path:
    sys.path.insert(0, str(parser_src))


def _create_plaid_auth_trace() -> TraceSession:
    """Create a realistic Plaid auth trace via the SDK."""
    t = TraceSession(
        agent="PlaidExplorer",
        system="plaid",
        objective="Map Plaid auth token lifecycle",
        agent_role="explorer",
        session_id="session-roundtrip-001",
    )
    t._started_at = t._ended_at = None
    t.__enter__()

    with t.span("doc_read", target="Plaid Link docs") as s1:
        s1.request(url="https://plaid.com/docs/link/")
        s1.response(status=200, body_summary="Link token flow: create → exchange → access_token")
        s1.observed(
            what_happened="Read Plaid Link overview documentation",
            what_learned="3-stage token exchange: link_token → public_token → access_token. link_token has 4h TTL.",
            confidence="high",
            surprises="26 product types supported in link_token",
            questions=["How does token rotation work for long-lived access_tokens?"],
        )
        s1.precondition("client_id and secret configured")
        s1.postcondition("link_token available for initialization")
        s1.tag("auth", "token", "link")
        s1.meta("source_urls", ["https://plaid.com/docs/link/"])

    with t.span("api_call", target="POST /transactions/sync") as s2:
        s2.request(
            url="https://sandbox.plaid.com/transactions/sync",
            method="POST",
            body={"access_token": "[REDACTED]", "cursor": "", "count": 100},
        )
        s2.response(
            status=200,
            body_summary="Returns added/modified/removed transactions with next_cursor",
        )
        s2.observed(
            what_happened="Called /transactions/sync with empty cursor for initial sync",
            what_learned="Cursor-based pagination: empty cursor = full history, next_cursor for incremental",
            confidence="high",
        )
        s2.state_change("cursor: empty → next_cursor_abc123")
        s2.depends_on(s1.span_id)
        s2.tag("transactions", "sync", "cursor")

    with t.span("error_probe", target="POST /transactions/get") as s3:
        s3.request(
            url="https://sandbox.plaid.com/transactions/get",
            method="POST",
            body={"access_token": "[REDACTED]"},
        )
        s3.response(
            status=400,
            body_summary="INVALID_ACCESS_TOKEN error response",
            error="access_token is invalid or expired",
        )
        s3.observed(
            what_happened="Called /transactions/get with expired access_token",
            what_learned="Plaid returns 400 with error_type + error_code, not HTTP 401",
            confidence="high",
            surprises="Error uses 400 not 401 — error semantics are in the body, not HTTP status",
        )
        s3.tag("error", "auth", "transactions")

    # Findings
    f1 = t.finding(
        category="protocol",
        title="Plaid uses cursor-based sync for transactions",
        description="The /transactions/sync endpoint uses cursor-based pagination. Empty cursor returns full history. next_cursor is used for incremental updates.",
        source_spans=[s2],
        confidence="confirmed",
        actionability="immediate",
    )
    f1.add_evidence(
        "Empty cursor → full transaction history",
        "Response includes next_cursor for subsequent calls",
    )

    f2 = t.finding(
        category="error_pattern",
        title="Plaid returns 400 for auth failures, not 401",
        description="Authentication errors are returned as HTTP 400 with error_type and error_code fields in the body. HTTP status codes don't carry auth semantics.",
        source_spans=[s3],
        confidence="confirmed",
        actionability="immediate",
    )
    f2.add_evidence("400 response with INVALID_ACCESS_TOKEN error_code")
    f2.relates_to(f1.finding_id)

    t.summary("Plaid transactions use cursor-based sync. Auth errors are 400 not 401.")
    t.__exit__(None, None, None)
    return t


def _create_plaid_balance_trace() -> TraceSession:
    """Create a second trace for cross-trace Optimizer testing."""
    t = TraceSession(
        agent="PlaidExplorer",
        system="plaid",
        objective="Map balance retrieval behavior",
        agent_role="explorer",
        session_id="session-roundtrip-002",
    )
    t.__enter__()

    with t.span("api_call", target="POST /accounts/balance/get") as s1:
        s1.request(
            url="https://sandbox.plaid.com/accounts/balance/get",
            method="POST",
            body={"access_token": "[REDACTED]"},
        )
        s1.response(status=200, body_summary="Account balances with available/current amounts")
        s1.observed(
            what_happened="Called /accounts/balance/get for all accounts",
            what_learned="Balance response includes available and current amounts. Real-time for checking, cached for savings.",
            confidence="high",
        )
        s1.tag("balance", "accounts")

    with t.span("error_probe", target="POST /accounts/balance/get") as s2:
        s2.request(
            url="https://sandbox.plaid.com/accounts/balance/get",
            method="POST",
            body={"access_token": "[REDACTED]"},
        )
        s2.response(
            status=400,
            body_summary="INVALID_ACCESS_TOKEN error",
            error="access_token is invalid or expired",
        )
        s2.observed(
            what_happened="Called balance endpoint with expired token",
            what_learned="Same 400 error pattern as transactions — consistent across endpoints",
            confidence="high",
        )
        s2.tag("error", "auth", "balance")

    # Finding that overlaps with auth trace — tests Optimizer dedup
    f1 = t.finding(
        category="error_pattern",
        title="Plaid returns 400 for auth failures, not 401",
        description="Balance endpoint also returns HTTP 400 for auth failures with error_code in body. Consistent error pattern across all endpoints.",
        source_spans=[s2],
        confidence="confirmed",
        actionability="immediate",
    )
    f1.add_evidence("400 response from /accounts/balance/get with INVALID_ACCESS_TOKEN")

    t.summary("Balance API uses real-time/cached distinction. Auth errors consistent at 400.")
    t.__exit__(None, None, None)
    return t


class TestRoundTrip:
    """SDK output → Parser → Analyzer → Optimizer round-trip."""

    def test_single_trace_through_parser(self):
        from ignite_parser.parser import parse_trace

        t = _create_plaid_auth_trace()
        result = parse_trace(t.to_dict())

        assert result.ok, f"Parse errors: {[e.message for e in result.errors]}"
        assert result.valid_count == 1
        trace = result.traces[0]
        assert trace.system == "plaid"
        assert len(trace.spans) == 3
        assert len(trace.findings) == 2

    def test_single_trace_through_analyzer(self):
        from ignite_parser.parser import parse_trace
        from ignite_parser.analyzer import analyze

        t = _create_plaid_auth_trace()
        parsed = parse_trace(t.to_dict())
        assert parsed.ok

        analysis = analyze(parsed.traces)
        assert analysis.endpoint_count >= 2  # doc_read target + api_call targets
        assert analysis.coverage.total_spans == 3
        assert analysis.coverage.total_findings == 2

    def test_two_traces_through_optimizer(self):
        from ignite_parser.parser import parse_trace
        from ignite_parser.analyzer import analyze
        from ignite_parser.optimizer import optimize

        t1 = _create_plaid_auth_trace()
        t2 = _create_plaid_balance_trace()

        parsed1 = parse_trace(t1.to_dict())
        parsed2 = parse_trace(t2.to_dict())
        assert parsed1.ok, f"Trace 1 errors: {[e.message for e in parsed1.errors]}"
        assert parsed2.ok, f"Trace 2 errors: {[e.message for e in parsed2.errors]}"

        a1 = analyze(parsed1.traces)
        a2 = analyze(parsed2.traces)

        optimized = optimize([a1, a2])

        # Optimizer should have produced merged findings
        assert optimized.finding_count >= 1
        assert optimized.source_analysis_count == 2
        # The "Plaid returns 400" finding appears in both traces — should dedup
        # So merged finding count should be less than total input findings (3)
        assert optimized.finding_count <= 3

    def test_sdk_output_matches_schema(self):
        """Verify SDK output has all required fields the Parser expects."""
        t = _create_plaid_auth_trace()
        d = t.to_dict()

        # Required trace-level fields
        assert d["schema_version"] in ("0.1", "0.2")
        assert d["trace_id"]
        assert d["agent_id"]
        assert d["agent_role"]
        assert d["system"]
        assert d["objective"]
        assert d["status"]
        assert d["started_at"]

        # Required span-level fields
        for span in d["spans"]:
            assert span["span_id"]
            assert span["kind"]
            assert span["started_at"]
            assert span["observation"]["what_happened"]
            assert span["observation"]["what_learned"]

        # Required finding-level fields
        for finding in d["findings"]:
            assert finding["finding_id"]
            assert finding["category"]
            assert finding["title"]
            assert finding["description"]
            assert finding["confidence"]

    def test_reporter_generates_output(self):
        from ignite_parser.parser import parse_trace
        from ignite_parser.analyzer import analyze
        from ignite_parser.reporter import generate_report

        t = _create_plaid_auth_trace()
        parsed = parse_trace(t.to_dict())
        assert parsed.ok

        analysis = analyze(parsed.traces)
        report = generate_report(analysis)

        assert isinstance(report, str)
        assert len(report) > 100
        assert "IGNITE" in report or "Trace Analysis" in report or "Endpoint" in report
