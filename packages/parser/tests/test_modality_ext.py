"""Tests for IGNITE Batch 3 — Modality Extensions (parser side).

Covers:
- Parser parsing of modality_ext field
- Cross-modality analysis (ModalityProfile)
- CoverageReport.modalities_seen
- Backward compatibility (v0.1 traces still parse)
"""

from __future__ import annotations

import sys
from pathlib import Path

import pytest

sys.path.insert(0, str(Path(__file__).parent.parent / "src"))

from ignite_parser.parser import parse_trace
from ignite_parser.analyzer import analyze, ModalityProfile
from ignite_parser.models import (
    Modality,
    RequestIntent,
    ResponseOutcome,
    Span,
    SpanKind,
    Trace,
    TraceStatus,
    AgentRole,
)
from datetime import datetime, timezone


# --- Helpers ---


def _make_trace_data(spans=None, version="0.2"):
    base_span = {
        "span_id": "span-001",
        "kind": "api_call",
        "started_at": "2026-07-29T08:00:00Z",
        "duration_ms": 100,
        "interaction": {"target": "POST /test"},
        "observation": {
            "what_happened": "Called API",
            "what_learned": "API works",
            "confidence": "high",
        },
    }
    if spans is None:
        spans = [base_span]
    return {
        "schema_version": version,
        "trace_id": "trace-001",
        "agent_id": "bumble",
        "agent_role": "explorer",
        "system": "ignite",
        "status": "completed",
        "started_at": "2026-07-29T08:00:00Z",
        "objective": "Test modality extensions",
        "spans": spans,
        "findings": [],
    }


def _make_span_data(span_id="span-001", modality="api", modality_ext=None,
                    target="POST /test", duration_ms=100,
                    response_outcome="success", **kwargs):
    d = {
        "span_id": span_id,
        "kind": "api_call",
        "started_at": "2026-07-29T08:00:00Z",
        "duration_ms": duration_ms,
        "modality": modality,
        "response_outcome": response_outcome,
        "interaction": {"target": target},
        "observation": {
            "what_happened": f"Interacted with {target}",
            "what_learned": "Something useful",
            "confidence": "high",
        },
    }
    if modality_ext is not None:
        d["modality_ext"] = modality_ext
    d.update(kwargs)
    return d


# --- Parser: modality_ext ---


class TestParserModalityExt:
    def test_parse_with_api_ext(self):
        span = _make_span_data(modality_ext={
            "type": "api_ext",
            "method": "POST",
            "status_code": 200,
            "path": "/transactions/get",
        })
        result = parse_trace(_make_trace_data([span]))
        assert result.ok
        s = result.traces[0].spans[0]
        assert s.modality_ext is not None
        assert s.modality_ext["type"] == "api_ext"
        assert s.modality_ext["method"] == "POST"

    def test_parse_with_db_ext(self):
        span = _make_span_data(modality="db", modality_ext={
            "type": "db_ext",
            "db_system": "postgresql",
            "db_operation": "SELECT",
            "rows_affected": 42,
        })
        result = parse_trace(_make_trace_data([span]))
        assert result.ok
        s = result.traces[0].spans[0]
        assert s.modality.value == "db"
        assert s.modality_ext["db_system"] == "postgresql"
        assert s.modality_ext["rows_affected"] == 42

    def test_parse_without_modality_ext_is_none(self):
        span = _make_span_data()
        result = parse_trace(_make_trace_data([span]))
        assert result.ok
        s = result.traces[0].spans[0]
        assert s.modality_ext is None

    def test_invalid_modality_ext_warns(self):
        span = _make_span_data(modality_ext="not_a_dict")
        result = parse_trace(_make_trace_data([span]))
        assert result.ok
        s = result.traces[0].spans[0]
        assert s.modality_ext is None
        assert any("modality_ext" in w.path for w in result.warnings)

    def test_v01_backward_compat(self):
        span = _make_span_data()
        result = parse_trace(_make_trace_data([span], version="0.1"))
        assert result.ok
        s = result.traces[0].spans[0]
        assert s.modality_ext is None


# --- Cross-modality analysis ---


def _make_typed_trace(spans_data):
    """Build a Trace object from span data dicts for analyzer testing."""
    result = parse_trace(_make_trace_data(spans_data))
    assert result.ok
    return result.traces[0]


class TestModalityProfiles:
    def test_single_modality(self):
        trace = _make_typed_trace([
            _make_span_data("s1", duration_ms=100),
            _make_span_data("s2", duration_ms=200),
        ])
        result = analyze([trace])
        assert "api" in result.modality_profiles
        profile = result.modality_profiles["api"]
        assert profile.span_count == 2
        assert profile.avg_duration_ms == 150.0
        assert profile.total_duration_ms == 300

    def test_multiple_modalities(self):
        trace = _make_typed_trace([
            _make_span_data("s1", modality="api", duration_ms=100),
            _make_span_data("s2", modality="db", duration_ms=50, modality_ext={
                "type": "db_ext", "db_system": "postgresql",
            }),
            _make_span_data("s3", modality="api", duration_ms=200),
        ])
        result = analyze([trace])
        assert len(result.modality_profiles) == 2
        assert result.modality_profiles["api"].span_count == 2
        assert result.modality_profiles["db"].span_count == 1

    def test_success_and_error_rates(self):
        trace = _make_typed_trace([
            _make_span_data("s1", response_outcome="success"),
            _make_span_data("s2", response_outcome="error"),
            _make_span_data("s3", response_outcome="success"),
            _make_span_data("s4", response_outcome="auth_failure"),
        ])
        result = analyze([trace])
        profile = result.modality_profiles["api"]
        assert profile.success_rate == 0.5  # 2/4
        assert profile.error_rate == 0.5    # 2/4 (error + auth_failure)

    def test_unique_targets(self):
        trace = _make_typed_trace([
            _make_span_data("s1", target="POST /a"),
            _make_span_data("s2", target="POST /b"),
            _make_span_data("s3", target="POST /a"),  # duplicate
        ])
        result = analyze([trace])
        profile = result.modality_profiles["api"]
        assert len(profile.unique_targets) == 2

    def test_empty_traces(self):
        trace = _make_typed_trace([_make_span_data("s1")])
        # Remove spans to test empty
        trace.spans = []
        result = analyze([trace])
        assert len(result.modality_profiles) == 0


# --- CoverageReport.modalities_seen ---


class TestCoverageModalities:
    def test_modalities_seen(self):
        trace = _make_typed_trace([
            _make_span_data("s1", modality="api"),
            _make_span_data("s2", modality="db"),
        ])
        result = analyze([trace])
        assert "api" in result.coverage.modalities_seen
        assert "db" in result.coverage.modalities_seen

    def test_single_modality_default(self):
        trace = _make_typed_trace([_make_span_data("s1")])
        result = analyze([trace])
        assert result.coverage.modalities_seen == {"api"}


# --- Span modality_ext field ---


class TestSpanModalityExtField:
    def test_span_default_none(self):
        span = Span()
        assert span.modality_ext is None

    def test_span_with_ext(self):
        span = Span(modality_ext={"type": "db_ext", "db_system": "postgresql"})
        assert span.modality_ext["type"] == "db_ext"
