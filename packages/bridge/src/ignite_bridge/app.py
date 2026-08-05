"""IGNITE Transport Bridge — FastAPI application.

Wraps the existing L2 pipeline (Parser → Analyzer → Spike Detection) as HTTP
endpoints for the browser extension. This is the critical path between the
Chrome extension (TypeScript) and the Python intelligence layer.

Architecture (from EngineeringExplorer trace-eng-browser-arch-003):
  Content Script (captures WebExt spans)
    → chrome.runtime.sendMessage
    → Service Worker
    → REST POST to this bridge (/traces)
    → Python L2 pipeline runs
    → SSE pushes spike signals back to side panel (/traces/spikes)
"""

from __future__ import annotations

import asyncio
import json
import time
from collections import deque
from dataclasses import asdict
from typing import Any

from ignite_parser.parser import parse_trace
from ignite_parser.analyzer import analyze
from ignite_parser.spike import detect_spikes, SpikeSignal


# --- In-memory spike signal buffer (for SSE consumers) ---

_spike_buffer: deque[dict[str, Any]] = deque(maxlen=1000)
_spike_event = asyncio.Event()


def _notify_spike(spike_dict: dict[str, Any]) -> None:
    """Add a spike signal to the buffer and wake SSE consumers."""
    _spike_buffer.append(spike_dict)
    _spike_event.set()


# --- Request/Response models ---

class TraceResponse:
    """Response from trace ingestion."""

    def __init__(
        self,
        ok: bool,
        trace_id: str | None = None,
        span_count: int = 0,
        errors: list[str] | None = None,
        spikes: list[dict[str, Any]] | None = None,
        analysis_summary: dict[str, Any] | None = None,
    ):
        self.ok = ok
        self.trace_id = trace_id
        self.span_count = span_count
        self.errors = errors or []
        self.spikes = spikes or []
        self.analysis_summary = analysis_summary

    def to_dict(self) -> dict[str, Any]:
        return {
            "ok": self.ok,
            "trace_id": self.trace_id,
            "span_count": self.span_count,
            "errors": self.errors,
            "spikes": self.spikes,
            "analysis_summary": self.analysis_summary,
        }


# --- Core pipeline wrapper ---

def ingest_trace(trace_data: dict[str, Any]) -> TraceResponse:
    """Run a trace through the full L2 pipeline.

    Pipeline: parse_trace → analyze → detect_spikes
    Returns structured response with analysis summary and spike signals.
    """
    # Stage 1: Parse
    result = parse_trace(trace_data)
    if not result.ok:
        return TraceResponse(
            ok=False,
            errors=[e.message for e in result.errors],
        )

    trace = result.traces[0]

    # Stage 2: Analyze
    analysis = analyze([trace])

    # Stage 3: Spike detection
    system = trace_data.get("system", "unknown")
    spikes = detect_spikes([trace], system=system)

    # Convert spikes to dicts
    spike_dicts = []
    for spike in spikes:
        sd = {
            "spike_type": spike.spike_type,
            "confidence": spike.confidence,
            "urgency": spike.urgency,
            "modalities": spike.modalities,
            "chain": spike.chain,
            "action_space": spike.action_space,
            "source_system": spike.source_system,
            "correlation_boost": spike.correlation_boost,
            "description": spike.description,
        }
        spike_dicts.append(sd)
        _notify_spike(sd)

    # Build analysis summary
    summary: dict[str, Any] = {
        "endpoint_count": analysis.endpoint_count,
        "cluster_count": analysis.cluster_count,
        "modalities_seen": sorted(analysis.coverage.modalities_seen),
        "total_spans": analysis.coverage.total_spans,
        "total_findings": analysis.coverage.total_findings,
    }
    if analysis.modality_profiles:
        summary["modality_profiles"] = {
            k: {
                "span_count": v.span_count,
                "success_rate": round(v.success_rate, 3),
                "error_rate": round(v.error_rate, 3),
            }
            for k, v in analysis.modality_profiles.items()
        }

    return TraceResponse(
        ok=True,
        trace_id=trace.trace_id,
        span_count=len(trace.spans),
        spikes=spike_dicts,
        analysis_summary=summary,
    )


# --- FastAPI application factory ---

def create_app():
    """Create the FastAPI application.

    Import is deferred so the module works without fastapi installed
    (tests can import ingest_trace directly).
    """
    from fastapi import FastAPI, Request
    from fastapi.responses import JSONResponse, StreamingResponse
    from fastapi.middleware.cors import CORSMiddleware

    app = FastAPI(
        title="IGNITE Transport Bridge",
        description="L2 pipeline HTTP interface for the browser extension",
        version="0.1.0",
    )

    # Allow requests from Chrome extension (any origin for local dev)
    app.add_middleware(
        CORSMiddleware,
        allow_origins=["*"],
        allow_methods=["GET", "POST"],
        allow_headers=["*"],
    )

    @app.get("/health")
    async def health():
        return {"status": "ok", "pipeline": "ignite-l2", "timestamp": time.time()}

    @app.post("/traces")
    async def ingest(request: Request):
        body = await request.json()
        response = ingest_trace(body)
        status = 200 if response.ok else 422
        return JSONResponse(content=response.to_dict(), status_code=status)

    @app.get("/traces/spikes")
    async def spike_stream():
        """SSE endpoint — streams spike signals to the browser extension side panel."""
        async def event_generator():
            last_index = len(_spike_buffer)
            yield f"data: {json.dumps({'type': 'connected'})}\n\n"
            while True:
                _spike_event.clear()
                # Send any new spikes since last check
                current_len = len(_spike_buffer)
                if current_len > last_index:
                    for i in range(last_index, current_len):
                        spike = _spike_buffer[i]
                        yield f"data: {json.dumps({'type': 'spike', 'spike': spike})}\n\n"
                    last_index = current_len
                try:
                    await asyncio.wait_for(_spike_event.wait(), timeout=30.0)
                except asyncio.TimeoutError:
                    yield f"data: {json.dumps({'type': 'keepalive'})}\n\n"

        return StreamingResponse(
            event_generator(),
            media_type="text/event-stream",
            headers={
                "Cache-Control": "no-cache",
                "Connection": "keep-alive",
                "X-Accel-Buffering": "no",
            },
        )

    return app
