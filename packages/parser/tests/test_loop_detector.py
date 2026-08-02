"""Tests for IGNITE Cross-Modality Loop Detection.

Covers:
- Complete BCI loop detection (Capture→Classify→Decode→Plan→Operate→Feedback)
- Partial chain detection (multi-modality but non-cyclic)
- No loops when no cross-modality edges
- Deduplication of same-signature cycles
- Summary output format
"""

from __future__ import annotations

import sys
from pathlib import Path
from datetime import datetime, timezone

import pytest

sys.path.insert(0, str(Path(__file__).parent.parent / "src"))

from ignite_parser.models import (
    ContainsContext,
    Modality,
    RequestIntent,
    ResponseOutcome,
    Span,
    SpanKind,
    Trace,
    TraceStatus,
    AgentRole,
    Observation,
    Interaction,
    Confidence,
)
from ignite_parser.loop_detector import (
    detect_loops,
    DetectedLoop,
    LoopDetectionResult,
    LoopStep,
)


# --- Helpers ---


def _span(
    span_id: str,
    modality: str = "api",
    contexts: list[ContainsContext] | None = None,
) -> Span:
    return Span(
        span_id=span_id,
        trace_id="trace-001",
        sequence=1,
        kind=SpanKind.API_CALL,
        started_at=datetime.now(timezone.utc),
        duration_ms=100,
        modality=Modality(modality),
        interaction=Interaction(target=f"target-{span_id}"),
        observation=Observation(
            what_happened=f"Span {span_id}",
            what_learned="Something",
            confidence=Confidence.HIGH,
        ),
        contains_contexts=contexts or [],
    )


def _trace(spans: list[Span]) -> Trace:
    return Trace(
        schema_version="0.2",
        trace_id="trace-001",
        agent_id="test",
        agent_role=AgentRole.EXPLORER,
        system="ignite",
        started_at=datetime.now(timezone.utc),
        status=TraceStatus.COMPLETED,
        objective="test",
        spans=spans,
    )


# --- Tests ---


class TestNoLoops:
    def test_empty_traces(self):
        result = detect_loops([])
        assert not result.has_complete_loop
        assert result.total_cross_modality_edges == 0

    def test_no_cross_modality_edges(self):
        """Spans with no contains_contexts produce no loops."""
        spans = [_span("s1"), _span("s2"), _span("s3")]
        result = detect_loops([_trace(spans)])
        assert not result.has_complete_loop
        assert result.total_cross_modality_edges == 0

    def test_same_modality_edges_ignored(self):
        """Same-modality references don't count as cross-modality edges."""
        spans = [
            _span("s1", modality="api", contexts=[
                ContainsContext(modality="api", relationship="processes", active=True),
            ]),
        ]
        result = detect_loops([_trace(spans)])
        assert result.total_cross_modality_edges == 0

    def test_inactive_edges_ignored(self):
        """Inactive (active=False) edges are skipped."""
        spans = [
            _span("s1", modality="api", contexts=[
                ContainsContext(modality="db", relationship="reads_from", active=False),
            ]),
        ]
        result = detect_loops([_trace(spans)])
        assert result.total_cross_modality_edges == 0


class TestCompleteLoops:
    def test_simple_triangle_cycle(self):
        """API→CLI→DB→API forms a complete 3-modality loop."""
        spans = [
            _span("s1", modality="api", contexts=[
                ContainsContext(modality="cli", relationship="triggers", active=True),
            ]),
            _span("s2", modality="cli", contexts=[
                ContainsContext(modality="db", relationship="writes_to", active=True),
            ]),
            _span("s3", modality="db", contexts=[
                ContainsContext(modality="api", relationship="learns_from", active=True),
            ]),
        ]
        result = detect_loops([_trace(spans)])
        assert result.has_complete_loop
        assert len(result.complete_loops) >= 1
        loop = result.complete_loops[0]
        assert loop.is_complete
        assert loop.cycle_length >= 3
        assert {"api", "cli", "db"} <= loop.modalities_involved

    def test_bci_loop_pattern(self):
        """Full BCI pattern: API→CLI→DB→DB→CLI→DB (capture→classify→decode→plan→operate→feedback)."""
        spans = [
            _span("s1", modality="api", contexts=[
                ContainsContext(modality="cli", relationship="captures", active=True),
            ]),
            _span("s2", modality="cli", contexts=[
                ContainsContext(modality="db", relationship="processes", active=True),
            ]),
            _span("s3", modality="db", contexts=[
                ContainsContext(modality="cli", relationship="analyzes", active=True),
            ]),
            _span("s4", modality="cli", contexts=[
                ContainsContext(modality="api", relationship="triggers", active=True),
            ]),
        ]
        result = detect_loops([_trace(spans)])
        assert result.has_complete_loop

    def test_loop_with_four_modalities(self):
        """API→CLI→DB→Message→API forms a 4-modality loop."""
        spans = [
            _span("s1", modality="api", contexts=[
                ContainsContext(modality="cli", relationship="triggers", active=True),
            ]),
            _span("s2", modality="cli", contexts=[
                ContainsContext(modality="db", relationship="writes_to", active=True),
            ]),
            _span("s3", modality="db", contexts=[
                ContainsContext(modality="message", relationship="triggers", active=True),
            ]),
            _span("s4", modality="message", contexts=[
                ContainsContext(modality="api", relationship="learns_from", active=True),
            ]),
        ]
        result = detect_loops([_trace(spans)])
        assert result.has_complete_loop
        loop = result.complete_loops[0]
        assert {"api", "cli", "db", "message"} <= loop.modalities_involved


class TestPartialChains:
    def test_two_hop_chain(self):
        """API→CLI→DB without closing the loop is a partial chain."""
        spans = [
            _span("s1", modality="api", contexts=[
                ContainsContext(modality="cli", relationship="triggers", active=True),
            ]),
            _span("s2", modality="cli", contexts=[
                ContainsContext(modality="db", relationship="writes_to", active=True),
            ]),
        ]
        result = detect_loops([_trace(spans)])
        assert not result.has_complete_loop
        assert len(result.loops) >= 1
        assert not result.loops[0].is_complete


class TestEdgeCounting:
    def test_counts_cross_modality_edges(self):
        spans = [
            _span("s1", modality="api", contexts=[
                ContainsContext(modality="cli", relationship="triggers", active=True),
                ContainsContext(modality="db", relationship="reads_from", active=True),
            ]),
            _span("s2", modality="cli", contexts=[
                ContainsContext(modality="db", relationship="writes_to", active=True),
            ]),
        ]
        result = detect_loops([_trace(spans)])
        assert result.total_cross_modality_edges == 3


class TestMultipleTraces:
    def test_loop_across_traces(self):
        """Edges from different traces combine to form loops."""
        trace1 = _trace([
            _span("s1", modality="api", contexts=[
                ContainsContext(modality="cli", relationship="triggers", active=True),
            ]),
        ])
        trace2 = _trace([
            _span("s2", modality="cli", contexts=[
                ContainsContext(modality="api", relationship="learns_from", active=True),
            ]),
        ])
        result = detect_loops([trace1, trace2])
        assert result.has_complete_loop


class TestSummary:
    def test_summary_format(self):
        spans = [
            _span("s1", modality="api", contexts=[
                ContainsContext(modality="cli", relationship="triggers", active=True),
            ]),
            _span("s2", modality="cli", contexts=[
                ContainsContext(modality="api", relationship="learns_from", active=True),
            ]),
        ]
        result = detect_loops([_trace(spans)])
        summary = result.summary()
        assert "total_loops" in summary
        assert "complete_loops" in summary
        assert "total_edges" in summary
        assert "modalities" in summary
        assert isinstance(summary["modalities"], list)

    def test_loop_summary(self):
        spans = [
            _span("s1", modality="api", contexts=[
                ContainsContext(modality="cli", relationship="triggers", active=True),
            ]),
            _span("s2", modality="cli", contexts=[
                ContainsContext(modality="api", relationship="learns_from", active=True),
            ]),
        ]
        result = detect_loops([_trace(spans)])
        assert result.has_complete_loop
        loop_summary = result.loops[0].summary()
        assert "loop_id" in loop_summary
        assert "is_complete" in loop_summary
        assert "stages" in loop_summary


class TestModalities:
    def test_modalities_seen_tracked(self):
        spans = [
            _span("s1", modality="api", contexts=[
                ContainsContext(modality="cli", relationship="triggers", active=True),
            ]),
            _span("s2", modality="db"),
        ]
        result = detect_loops([_trace(spans)])
        assert "api" in result.modalities_seen
        assert "cli" in result.modalities_seen
        assert "db" in result.modalities_seen


class TestStageLabeling:
    def test_known_relationships_get_stages(self):
        spans = [
            _span("s1", modality="api", contexts=[
                ContainsContext(modality="cli", relationship="captures", active=True),
            ]),
            _span("s2", modality="cli", contexts=[
                ContainsContext(modality="api", relationship="triggers", active=True),
            ]),
        ]
        result = detect_loops([_trace(spans)])
        assert result.has_complete_loop
        stages = result.loops[0].stages
        assert "capture" in stages or "operate" in stages
