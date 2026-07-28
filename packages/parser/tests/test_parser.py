"""Tests for the IGNITE L2 Parser."""

import json
from pathlib import Path

import pytest

from ignite_parser.models import (
    AgentRole,
    Confidence,
    FindingCategory,
    FindingConfidence,
    SpanKind,
    TraceStatus,
)
from ignite_parser.parser import parse_file, parse_jsonl, parse_trace

FIXTURES = Path(__file__).parent / "fixtures"


class TestParseTrace:
    """Tests for single trace parsing."""

    def test_valid_trace(self):
        data = json.loads((FIXTURES / "valid_trace.json").read_text())
        result = parse_trace(data)

        assert result.ok
        assert result.valid_count == 1

        trace = result.traces[0]
        assert trace.trace_id == "a1b2c3d4-e5f6-4a7b-8c9d-0e1f2a3b4c5d"
        assert trace.agent_id == "PlaidExplorer"
        assert trace.agent_role == AgentRole.EXPLORER
        assert trace.system == "plaid"
        assert trace.status == TraceStatus.COMPLETED
        assert len(trace.spans) == 2
        assert len(trace.findings) == 1

    def test_valid_spans(self):
        data = json.loads((FIXTURES / "valid_trace.json").read_text())
        result = parse_trace(data)
        trace = result.traces[0]

        span1 = trace.spans[0]
        assert span1.span_id == "span-001"
        assert span1.kind == SpanKind.DOC_READ
        assert span1.sequence == 1
        assert span1.observation.confidence == Confidence.HIGH
        assert "link_token" in span1.observation.what_learned.lower()

        span2 = trace.spans[1]
        assert span2.kind == SpanKind.API_CALL
        assert span2.sequence == 2
        assert span2.interaction.method == "POST"
        assert span2.interaction.response.status_code == 200

    def test_valid_finding(self):
        data = json.loads((FIXTURES / "valid_trace.json").read_text())
        result = parse_trace(data)
        finding = result.traces[0].findings[0]

        assert finding.category == FindingCategory.PROTOCOL
        assert finding.confidence == FindingConfidence.CONFIRMED
        assert len(finding.source_spans) == 2

    def test_missing_schema_version(self):
        data = {"trace_id": "test", "agent_id": "test"}
        result = parse_trace(data)
        assert not result.ok
        assert any("schema_version" in e.path for e in result.errors)

    def test_unsupported_schema_version(self):
        data = {"schema_version": "99.0", "trace_id": "test"}
        result = parse_trace(data)
        assert not result.ok
        assert any("unsupported" in e.message for e in result.errors)

    def test_missing_required_fields(self):
        data = {"schema_version": "0.1"}
        result = parse_trace(data)
        assert not result.ok
        field_names = {e.path for e in result.errors}
        assert "trace_id" in field_names
        assert "agent_id" in field_names
        assert "objective" in field_names

    def test_invalid_agent_role(self):
        data = {
            "schema_version": "0.1",
            "trace_id": "t1",
            "agent_id": "test",
            "agent_role": "invalid_role",
            "system": "plaid",
            "status": "active",
            "started_at": "2026-07-23T14:00:00Z",
            "objective": "test",
        }
        result = parse_trace(data)
        assert not result.ok
        assert any("agent_role" in e.path for e in result.errors)

    def test_invalid_timestamp(self):
        data = {
            "schema_version": "0.1",
            "trace_id": "t1",
            "agent_id": "test",
            "agent_role": "explorer",
            "system": "plaid",
            "status": "active",
            "started_at": "not-a-timestamp",
            "objective": "test",
        }
        result = parse_trace(data)
        assert not result.ok
        assert any("timestamp" in e.message for e in result.errors)

    def test_span_missing_observation(self):
        data = {
            "schema_version": "0.1",
            "trace_id": "t1",
            "agent_id": "test",
            "agent_role": "explorer",
            "system": "plaid",
            "status": "active",
            "started_at": "2026-07-23T14:00:00Z",
            "objective": "test",
            "spans": [{
                "span_id": "s1",
                "kind": "api_call",
                "started_at": "2026-07-23T14:00:00Z",
                "observation": {
                    "what_happened": "",
                    "what_learned": "",
                    "confidence": "low",
                },
            }],
        }
        result = parse_trace(data)
        assert not result.ok
        assert any("what_happened" in e.path for e in result.errors)
        assert any("what_learned" in e.path for e in result.errors)

    def test_finding_unknown_span_warns(self):
        data = {
            "schema_version": "0.1",
            "trace_id": "t1",
            "agent_id": "test",
            "agent_role": "explorer",
            "system": "plaid",
            "status": "active",
            "started_at": "2026-07-23T14:00:00Z",
            "objective": "test",
            "spans": [],
            "findings": [{
                "finding_id": "f1",
                "source_spans": ["nonexistent-span"],
                "category": "protocol",
                "confidence": "hypothesis",
                "title": "Test finding",
                "description": "Test description",
            }],
        }
        result = parse_trace(data)
        assert result.warning_count > 0
        assert any("nonexistent-span" in w.message for w in result.warnings)

    def test_secret_detection_warns(self):
        data = {
            "schema_version": "0.1",
            "trace_id": "t1",
            "agent_id": "test",
            "agent_role": "explorer",
            "system": "plaid",
            "status": "active",
            "started_at": "2026-07-23T14:00:00Z",
            "objective": "test",
            "spans": [],
            "metadata": {
                "leaked": "sk_" + "live_abc123def456ghi789jkl012mno345"
            },
        }
        result = parse_trace(data)
        assert result.warning_count > 0
        assert any("secret" in w.message for w in result.warnings)


class TestParseJsonl:
    """Tests for JSONL batch parsing."""

    def test_multi_line_jsonl(self):
        trace_data = json.loads((FIXTURES / "valid_trace.json").read_text())
        # Two identical traces
        jsonl = json.dumps(trace_data) + "\n" + json.dumps(trace_data) + "\n"
        result = parse_jsonl(jsonl)
        assert result.valid_count == 2
        assert result.ok

    def test_mixed_valid_invalid(self):
        trace_data = json.loads((FIXTURES / "valid_trace.json").read_text())
        invalid = json.dumps({"schema_version": "0.1"})  # missing fields
        jsonl = json.dumps(trace_data) + "\n" + invalid + "\n"
        result = parse_jsonl(jsonl)
        assert result.valid_count == 1
        assert result.error_count > 0

    def test_malformed_json_line(self):
        jsonl = '{"valid": true}\n{not json}\n'
        result = parse_jsonl(jsonl)
        assert result.error_count > 0
        assert any("invalid JSON" in e.message for e in result.errors)

    def test_empty_lines_skipped(self):
        trace_data = json.loads((FIXTURES / "valid_trace.json").read_text())
        jsonl = "\n" + json.dumps(trace_data) + "\n\n"
        result = parse_jsonl(jsonl)
        assert result.valid_count == 1


class TestParseFile:
    """Tests for file-based parsing."""

    def test_file_not_found(self):
        result = parse_file("/nonexistent/path.jsonl")
        assert not result.ok
        assert any("not found" in e.message for e in result.errors)

    def test_parse_fixture_as_jsonl(self, tmp_path):
        trace_data = json.loads((FIXTURES / "valid_trace.json").read_text())
        jsonl_file = tmp_path / "traces.jsonl"
        jsonl_file.write_text(json.dumps(trace_data) + "\n")
        result = parse_file(jsonl_file)
        assert result.ok
        assert result.valid_count == 1
