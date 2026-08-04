"""IGNITE Bridge — FastAPI app wrapping the L2 pipeline.

Endpoints:
    POST /traces       — full trace ingestion
    POST /traces/web   — WebExt span ingestion (auto-wrapped in trace envelope)
    GET  /traces/spikes — SSE spike stream
    GET  /health       — health check
"""

from __future__ import annotations

import asyncio
import uuid
from collections import deque
from dataclasses import asdict, dataclass, field
from datetime import datetime, timezone
from typing import Any

from fastapi import FastAPI, Request
from fastapi.responses import JSONResponse, StreamingResponse

from ignite_parser.models import Trace
from ignite_parser.parser import parse_trace


# --- In-memory state ---

_spike_buffer: deque[dict] = deque(maxlen=200)
_recent_traces: deque[Trace] = deque(maxlen=100)


# --- Response helpers ---

@dataclass
class TraceResponse:
    trace_id: str
    span_count: int
    finding_count: int
    errors: list[dict] = field(default_factory=list)
    warnings: list[dict] = field(default_factory=list)


def _trace_response_dict(resp: TraceResponse) -> dict:
    return asdict(resp)


# --- Core ingestion ---

def ingest_trace(data: dict[str, Any]) -> TraceResponse:
    """Parse a trace dict, run spike detection, return response."""
    result = parse_trace(data)

    errors = [{"path": e.path, "message": e.message} for e in result.errors]
    warnings = [{"path": w.path, "message": w.message} for w in result.warnings]

    if not result.ok:
        return TraceResponse(
            trace_id=data.get("trace_id", ""),
            span_count=0,
            finding_count=0,
            errors=errors,
            warnings=warnings,
        )

    trace = result.traces[0]
    _recent_traces.append(trace)

    # Basic spike detection: flag spans with duration_ms > 5000
    for span in trace.spans:
        if span.duration_ms > 5000:
            spike = {
                "trace_id": trace.trace_id,
                "span_id": span.span_id,
                "type": "latency_spike",
                "value_ms": span.duration_ms,
                "target": span.interaction.target,
                "timestamp": datetime.now(timezone.utc).isoformat(),
            }
            _spike_buffer.append(spike)

    return TraceResponse(
        trace_id=trace.trace_id,
        span_count=len(trace.spans),
        finding_count=len(trace.findings),
        errors=errors,
        warnings=warnings,
    )


# --- WebExt span → trace envelope ---

def _wrap_web_spans(spans: list[dict], system: str = "unknown") -> dict:
    """Wrap an array of WebExt-shaped spans into a full trace envelope."""
    trace_id = str(uuid.uuid4())
    now = datetime.now(timezone.utc).isoformat()

    wrapped_spans = []
    for i, raw in enumerate(spans):
        wrapped_spans.append({
            "span_id": raw.get("span_id", str(uuid.uuid4())),
            "trace_id": trace_id,
            "parent_span_id": raw.get("parent_span_id"),
            "sequence": i + 1,
            "kind": raw.get("kind", "api_call"),
            "started_at": raw.get("timestamp", now),
            "ended_at": None,
            "duration_ms": raw.get("duration_ms", 0),
            "interaction": {
                "target": raw.get("target", raw.get("operation", "")),
                "method": None,
                "request": {},
                "response": {},
            },
            "observation": {
                "what_happened": f"Web interaction: {raw.get('operation', 'unknown')} on {raw.get('target', 'unknown')}",
                "what_learned": f"Captured {raw.get('operation', 'unknown')} event via WebExt content script",
                "confidence": "low",
            },
            "metadata": {
                "modality": "web",
                "web_kind": "client",
                "operation": raw.get("operation", ""),
                "attributes": raw.get("attributes", {}),
            },
        })

    return {
        "schema_version": "0.1",
        "trace_id": trace_id,
        "agent_id": "webext-bridge",
        "agent_role": "explorer",
        "system": system,
        "session_id": str(uuid.uuid4()),
        "started_at": now,
        "status": "completed",
        "objective": "Capture web interaction trace from browser extension",
        "spans": wrapped_spans,
        "findings": [],
        "metadata": {"modality": "web", "kind": "client"},
    }


# --- App factory ---

def create_app() -> FastAPI:
    app = FastAPI(title="IGNITE Bridge", version="0.1.0")

    @app.get("/health")
    async def health():
        return {"status": "ok"}

    @app.post("/traces")
    async def post_traces(request: Request):
        data = await request.json()
        resp = ingest_trace(data)
        status = 200 if not resp.errors else 422
        return JSONResponse(content=_trace_response_dict(resp), status_code=status)

    @app.post("/traces/web")
    async def post_traces_web(request: Request):
        body = await request.json()
        spans = body.get("spans", body if isinstance(body, list) else [])
        system = body.get("system", "unknown") if isinstance(body, dict) else "unknown"
        envelope = _wrap_web_spans(spans, system=system)
        resp = ingest_trace(envelope)
        status = 200 if not resp.errors else 422
        return JSONResponse(content=_trace_response_dict(resp), status_code=status)

    @app.get("/traces/spikes")
    async def spike_stream():
        async def generate():
            last_seen = len(_spike_buffer)
            while True:
                current = len(_spike_buffer)
                if current > last_seen:
                    for spike in list(_spike_buffer)[last_seen:current]:
                        import json
                        yield f"data: {json.dumps(spike)}\n\n"
                    last_seen = current
                await asyncio.sleep(1)

        return StreamingResponse(generate(), media_type="text/event-stream")

    return app
