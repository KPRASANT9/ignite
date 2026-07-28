"""Tests for TraceSession — trace lifecycle, span sequencing, output."""

import json
import tempfile
from pathlib import Path

from ignite_trace.session import TraceSession


class TestTraceSessionLifecycle:
    def test_basic_session_creates_trace(self):
        with TraceSession(agent="TestAgent", system="test", objective="test obj") as t:
            pass
        d = t.to_dict()
        assert d["schema_version"] == "0.1"
        assert d["agent_id"] == "TestAgent"
        assert d["system"] == "test"
        assert d["objective"] == "test obj"
        assert d["status"] == "completed"
        assert d["started_at"] is not None
        assert d["ended_at"] is not None

    def test_failed_session_on_exception(self):
        try:
            with TraceSession(agent="TestAgent", system="test", objective="x") as t:
                raise ValueError("boom")
        except ValueError:
            pass
        assert t.to_dict()["status"] == "failed"

    def test_trace_id_is_unique(self):
        with TraceSession(agent="A", system="s", objective="o") as t1:
            pass
        with TraceSession(agent="A", system="s", objective="o") as t2:
            pass
        assert t1.trace_id != t2.trace_id

    def test_custom_session_id(self):
        with TraceSession(agent="A", system="s", objective="o", session_id="my-session") as t:
            pass
        assert t.to_dict()["session_id"] == "my-session"

    def test_agent_role_default(self):
        with TraceSession(agent="A", system="s", objective="o") as t:
            pass
        assert t.to_dict()["agent_role"] == "explorer"

    def test_metadata(self):
        with TraceSession(agent="A", system="s", objective="o") as t:
            t.meta("key1", "value1")
            t.meta("key2", 42)
        d = t.to_dict()
        assert d["metadata"] == {"key1": "value1", "key2": 42}

    def test_findings_summary(self):
        with TraceSession(agent="A", system="s", objective="o") as t:
            t.summary("Found 3 things")
        assert t.to_dict()["findings_summary"] == "Found 3 things"


class TestTraceSessionSpans:
    def test_span_sequencing(self):
        with TraceSession(agent="A", system="s", objective="o") as t:
            with t.span("api_call", target="POST /foo") as s1:
                s1.observed(what_happened="called foo", what_learned="it works")
            with t.span("api_call", target="POST /bar") as s2:
                s2.observed(what_happened="called bar", what_learned="it also works")

        d = t.to_dict()
        assert len(d["spans"]) == 2
        assert d["spans"][0]["sequence"] == 1
        assert d["spans"][1]["sequence"] == 2

    def test_span_parent_child(self):
        with TraceSession(agent="A", system="s", objective="o") as t:
            with t.span("api_call", target="parent") as parent:
                parent.observed(what_happened="p", what_learned="l")
            with t.span("api_call", target="child", parent=parent) as child:
                child.observed(what_happened="c", what_learned="l")

        d = t.to_dict()
        assert d["spans"][1].get("parent_span_id") == parent.span_id

    def test_span_trace_id_matches(self):
        with TraceSession(agent="A", system="s", objective="o") as t:
            with t.span("api_call", target="x") as s:
                s.observed(what_happened="h", what_learned="l")
        assert t.to_dict()["spans"][0]["trace_id"] == t.trace_id


class TestTraceSessionFindings:
    def test_finding_creation(self):
        with TraceSession(agent="A", system="s", objective="o") as t:
            with t.span("api_call", target="x") as s:
                s.observed(what_happened="h", what_learned="l")
            f = t.finding(
                category="protocol",
                title="Test finding",
                description="Found something",
                source_spans=[s],
                confidence="confirmed",
            )
            f.add_evidence("evidence 1", "evidence 2")
            f.tag("auth", "token")

        d = t.to_dict()
        assert len(d["findings"]) == 1
        finding = d["findings"][0]
        assert finding["category"] == "protocol"
        assert finding["title"] == "Test finding"
        assert finding["source_spans"] == [s.span_id]
        assert finding["evidence"] == ["evidence 1", "evidence 2"]
        assert finding["tags"] == ["auth", "token"]


class TestTraceSessionOutput:
    def test_write_to_directory(self):
        with tempfile.TemporaryDirectory() as tmpdir:
            with TraceSession(
                agent="A", system="s", objective="o",
                output_dir=tmpdir
            ) as t:
                with t.span("api_call", target="x") as s:
                    s.observed(what_happened="h", what_learned="l")

            # File should exist
            filepath = Path(tmpdir) / f"{t.trace_id}.json"
            assert filepath.exists()

            # Should be valid JSON
            data = json.loads(filepath.read_text())
            assert data["trace_id"] == t.trace_id
            assert len(data["spans"]) == 1

    def test_to_json_produces_valid_json(self):
        with TraceSession(agent="A", system="s", objective="o") as t:
            with t.span("doc_read", target="docs") as s:
                s.observed(what_happened="read docs", what_learned="learned things")
        j = t.to_json()
        data = json.loads(j)
        assert data["system"] == "s"

    def test_manual_write(self):
        with tempfile.TemporaryDirectory() as tmpdir:
            with TraceSession(agent="A", system="s", objective="o") as t:
                with t.span("api_call", target="x") as s:
                    s.observed(what_happened="h", what_learned="l")

            path = t.write(output_dir=tmpdir)
            assert path.exists()
            data = json.loads(path.read_text())
            assert data["status"] == "completed"
