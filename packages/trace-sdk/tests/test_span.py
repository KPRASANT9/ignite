"""Tests for SpanBuilder — fluent API, serialization, sanitization."""

from ignite_trace.span import SpanBuilder


class TestSpanBuilder:
    def _make_span(self, kind="api_call", target="POST /test") -> SpanBuilder:
        return SpanBuilder(kind=kind, target=target, sequence=1, trace_id="test-trace")

    def test_basic_span_serialization(self):
        s = self._make_span()
        s._started_at = s._ended_at = None  # bypass context manager
        s.observed(what_happened="called endpoint", what_learned="got response")
        d = s.to_dict()
        assert d["kind"] == "api_call"
        assert d["interaction"]["target"] == "POST /test"
        assert d["observation"]["what_happened"] == "called endpoint"
        assert d["observation"]["what_learned"] == "got response"
        assert d["sequence"] == 1

    def test_request_response(self):
        s = self._make_span()
        s.request(url="https://api.example.com/test", method="POST", body={"key": "value"})
        s.response(status=200, body_summary="OK response", error=None)
        s.observed(what_happened="h", what_learned="l")
        d = s.to_dict()
        assert d["interaction"]["request"]["url"] == "https://api.example.com/test"
        assert d["interaction"]["request"]["body"] == {"key": "value"}
        assert d["interaction"]["response"]["status_code"] == 200

    def test_state_changes(self):
        s = self._make_span()
        s.observed(what_happened="h", what_learned="l")
        s.state_change("token: valid → expired")
        s.precondition("token exists")
        s.postcondition("token invalidated")
        d = s.to_dict()
        assert d["state"]["state_changes"] == ["token: valid → expired"]
        assert d["state"]["preconditions"] == ["token exists"]
        assert d["state"]["postconditions"] == ["token invalidated"]

    def test_relationships(self):
        s = self._make_span()
        s.observed(what_happened="h", what_learned="l")
        s.depends_on("span-001")
        s.enables("span-002")
        s.contradicts("span-003")
        s.refines("span-004")
        d = s.to_dict()
        assert d["relationships"]["depends_on"] == ["span-001"]
        assert d["relationships"]["enables"] == ["span-002"]
        assert d["relationships"]["contradicts"] == ["span-003"]
        assert d["relationships"]["refines"] == ["span-004"]

    def test_tags_and_metadata(self):
        s = self._make_span()
        s.observed(what_happened="h", what_learned="l")
        s.tag("auth", "token", "plaid")
        s.meta("source_url", "https://plaid.com/docs")
        d = s.to_dict()
        assert d["tags"] == ["auth", "token", "plaid"]
        assert d["metadata"]["source_url"] == "https://plaid.com/docs"

    def test_context_manager_sets_timestamps(self):
        s = self._make_span()
        with s:
            s.observed(what_happened="h", what_learned="l")
        assert s._started_at is not None
        assert s._ended_at is not None
        d = s.to_dict()
        assert "started_at" in d
        assert "ended_at" in d

    def test_sanitization_on_request(self):
        s = self._make_span()
        s.request(body={"access_token": "access-sandbox-abc123def456", "client_id": "safe"})
        s.observed(what_happened="h", what_learned="l")
        d = s.to_dict()
        assert d["interaction"]["request"]["body"]["access_token"] == "[REDACTED]"
        assert d["interaction"]["request"]["body"]["client_id"] == "safe"

    def test_sanitization_on_headers(self):
        s = self._make_span()
        s.request(headers={"Authorization": "Bearer eyJhbGciOiJIUzI1NiIsInR5cCI6IkpXVCJ9"})
        s.observed(what_happened="h", what_learned="l")
        d = s.to_dict()
        assert d["interaction"]["request"]["headers"]["Authorization"] == "[REDACTED]"

    def test_questions_raised(self):
        s = self._make_span()
        s.observed(
            what_happened="h", what_learned="l",
            questions=["How does retry work?", "What is the TTL?"]
        )
        d = s.to_dict()
        assert len(d["observation"]["questions_raised"]) == 2

    def test_unique_span_ids(self):
        s1 = self._make_span()
        s2 = self._make_span()
        assert s1.span_id != s2.span_id
