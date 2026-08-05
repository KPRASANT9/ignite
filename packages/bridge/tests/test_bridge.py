"""Tests for the IGNITE Transport Bridge — L2 pipeline over HTTP.

Tests ingest_trace() directly (no FastAPI dependency needed for unit tests).
Uses existing trace fixtures from the parser package to validate the bridge
returns correct analysis summaries and spike signals.
"""

from __future__ import annotations

import json
import sys
from pathlib import Path

import pytest

# Add both bridge and parser+trace-sdk to path
sys.path.insert(0, str(Path(__file__).parent.parent / "src"))
sys.path.insert(0, str(Path(__file__).parent.parent.parent / "parser" / "src"))
sys.path.insert(0, str(Path(__file__).parent.parent.parent / "trace-sdk" / "src"))

from ignite_bridge.app import ingest_trace, TraceResponse, _spike_buffer


PARSER_FIXTURES = Path(__file__).parent.parent.parent / "parser" / "tests" / "fixtures"


@pytest.fixture(autouse=True)
def clear_spike_buffer():
    """Clear the spike buffer before each test."""
    _spike_buffer.clear()
    yield
    _spike_buffer.clear()


# --- Fixture loaders ---

@pytest.fixture
def valid_trace_data():
    return json.loads((PARSER_FIXTURES / "valid_trace.json").read_text())


@pytest.fixture
def msg_trace_data():
    return json.loads((PARSER_FIXTURES / "trace_msg_session_010.json").read_text())


@pytest.fixture
def cli_trace_data():
    return json.loads((PARSER_FIXTURES / "trace_cli_session_009.json").read_text())


@pytest.fixture
def db_trace_data():
    return json.loads((PARSER_FIXTURES / "trace_db_session_007.json").read_text())


@pytest.fixture
def web_trace_data():
    return json.loads((PARSER_FIXTURES / "trace_web_session_008.json").read_text())


# --- Basic ingestion tests ---


class TestIngestTrace:
    def test_valid_trace_returns_ok(self, valid_trace_data):
        resp = ingest_trace(valid_trace_data)
        assert resp.ok is True
        assert resp.trace_id is not None
        assert resp.span_count > 0
        assert len(resp.errors) == 0

    def test_response_has_analysis_summary(self, valid_trace_data):
        resp = ingest_trace(valid_trace_data)
        assert resp.analysis_summary is not None
        assert "endpoint_count" in resp.analysis_summary
        assert "total_spans" in resp.analysis_summary
        assert "modalities_seen" in resp.analysis_summary

    def test_response_to_dict(self, valid_trace_data):
        resp = ingest_trace(valid_trace_data)
        d = resp.to_dict()
        assert isinstance(d, dict)
        assert d["ok"] is True
        assert isinstance(d["spikes"], list)
        assert isinstance(d["analysis_summary"], dict)

    def test_invalid_trace_returns_errors(self):
        resp = ingest_trace({"invalid": True})
        assert resp.ok is False
        assert len(resp.errors) > 0

    def test_empty_trace_returns_errors(self):
        resp = ingest_trace({})
        assert resp.ok is False

    def test_trace_id_preserved(self, valid_trace_data):
        resp = ingest_trace(valid_trace_data)
        assert resp.trace_id == valid_trace_data["trace_id"]


# --- Modality-specific ingestion ---


class TestModalityIngestion:
    def test_message_trace(self, msg_trace_data):
        resp = ingest_trace(msg_trace_data)
        assert resp.ok is True
        assert resp.span_count == 7
        assert "message" in resp.analysis_summary["modalities_seen"]

    def test_cli_trace(self, cli_trace_data):
        resp = ingest_trace(cli_trace_data)
        assert resp.ok is True
        assert "cli" in resp.analysis_summary["modalities_seen"]

    def test_db_trace(self, db_trace_data):
        resp = ingest_trace(db_trace_data)
        assert resp.ok is True
        assert "db" in resp.analysis_summary["modalities_seen"]

    def test_web_trace(self, web_trace_data):
        resp = ingest_trace(web_trace_data)
        assert resp.ok is True
        assert "web" in resp.analysis_summary["modalities_seen"]

    def test_all_five_modalities_ingestible(
        self, valid_trace_data, msg_trace_data, cli_trace_data, db_trace_data, web_trace_data
    ):
        """All 5 modality fixtures pass through the bridge without error."""
        for data in [valid_trace_data, msg_trace_data, cli_trace_data, db_trace_data, web_trace_data]:
            resp = ingest_trace(data)
            assert resp.ok is True, f"Failed on trace {data.get('trace_id')}"


# --- Spike detection through bridge ---


class TestSpikeDetection:
    def test_msg_trace_produces_spikes(self, msg_trace_data):
        """Message trace with error span should produce spike signals."""
        resp = ingest_trace(msg_trace_data)
        assert len(resp.spikes) >= 1

    def test_spike_has_required_fields(self, msg_trace_data):
        resp = ingest_trace(msg_trace_data)
        if resp.spikes:
            spike = resp.spikes[0]
            assert "spike_type" in spike
            assert "confidence" in spike
            assert "urgency" in spike
            assert "modalities" in spike
            assert "action_space" in spike
            assert "source_system" in spike

    def test_spike_confidence_range(self, msg_trace_data):
        resp = ingest_trace(msg_trace_data)
        for spike in resp.spikes:
            assert 0.0 <= spike["confidence"] <= 1.0

    def test_spikes_added_to_buffer(self, msg_trace_data):
        """Spikes flow into the SSE buffer for real-time streaming."""
        assert len(_spike_buffer) == 0
        resp = ingest_trace(msg_trace_data)
        assert len(_spike_buffer) == len(resp.spikes)

    def test_spike_buffer_matches_response(self, msg_trace_data):
        resp = ingest_trace(msg_trace_data)
        for i, spike in enumerate(resp.spikes):
            assert _spike_buffer[i]["spike_type"] == spike["spike_type"]
            assert _spike_buffer[i]["confidence"] == spike["confidence"]


# --- Analysis summary ---


class TestAnalysisSummary:
    def test_endpoint_count(self, valid_trace_data):
        resp = ingest_trace(valid_trace_data)
        assert resp.analysis_summary["endpoint_count"] >= 0

    def test_modality_profiles(self, msg_trace_data):
        resp = ingest_trace(msg_trace_data)
        profiles = resp.analysis_summary.get("modality_profiles", {})
        if "message" in profiles:
            assert "span_count" in profiles["message"]
            assert "success_rate" in profiles["message"]
            assert "error_rate" in profiles["message"]

    def test_total_spans_matches(self, msg_trace_data):
        resp = ingest_trace(msg_trace_data)
        assert resp.analysis_summary["total_spans"] == resp.span_count

    def test_cluster_count(self, valid_trace_data):
        resp = ingest_trace(valid_trace_data)
        assert resp.analysis_summary["cluster_count"] >= 0


# --- TraceResponse ---


class TestTraceResponse:
    def test_error_response(self):
        resp = TraceResponse(ok=False, errors=["bad schema"])
        d = resp.to_dict()
        assert d["ok"] is False
        assert d["errors"] == ["bad schema"]
        assert d["spikes"] == []

    def test_success_response(self):
        resp = TraceResponse(
            ok=True,
            trace_id="t-1",
            span_count=5,
            spikes=[{"spike_type": "error", "confidence": 0.9}],
            analysis_summary={"endpoint_count": 3},
        )
        d = resp.to_dict()
        assert d["ok"] is True
        assert d["trace_id"] == "t-1"
        assert d["span_count"] == 5
        assert len(d["spikes"]) == 1
