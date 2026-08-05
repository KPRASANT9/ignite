"""IGNITE Bridge — FastAPI app wrapping the L2 pipeline.

Endpoints:
    POST /traces       — full trace ingestion
    POST /traces/web   — WebExt span ingestion (auto-wrapped in trace envelope)
    GET  /traces/spikes — SSE spike stream
    GET  /endpoints     — L2 endpoint catalog from analyzed traces
    GET  /spikes/recent — recent spike signals as JSON
    POST /mcp/tools/list — list MCP tools for a system/archetype
    POST /mcp/tools/call — invoke an MCP tool (proxied with auth forwarding)
    GET  /health       — health check
"""

from __future__ import annotations

import asyncio
import json
import uuid
from collections import deque
from dataclasses import asdict, dataclass, field
from datetime import datetime, timezone
from typing import Any

import httpx
from fastapi import FastAPI, Request
from fastapi.responses import JSONResponse, StreamingResponse

from ignite_parser.analyzer import EndpointRecord, analyze
from ignite_parser.models import Trace
from ignite_parser.parser import parse_trace
from ignite_parser.spike import SpikeSignal, detect_spikes


# --- In-memory state ---

_spike_buffer: deque[dict] = deque(maxlen=200)
_spike_signals: deque[dict] = deque(maxlen=200)
_recent_traces: deque[Trace] = deque(maxlen=100)

# --- MCP tool registry ---
# Maps (system, archetype) → list of tool definitions.
# P1: only mcp-sync tools for GitHub.

_mcp_tools: dict[tuple[str, str], list[dict]] = {
    ("github", "mcp-sync"): [
        {
            "name": "invoke",
            "description": "Invoke a single GitHub API endpoint via the bridge",
            "inputSchema": {
                "type": "object",
                "properties": {
                    "endpoint": {"type": "string", "description": "API path, e.g. /repos/{owner}/{repo}"},
                    "method": {"type": "string", "enum": ["GET", "POST", "PUT", "PATCH", "DELETE"], "default": "GET"},
                    "params": {"type": "object", "description": "Query params or JSON body"},
                },
                "required": ["endpoint"],
            },
        },
        {
            "name": "batch_invoke",
            "description": "Invoke multiple GitHub API endpoints in parallel",
            "inputSchema": {
                "type": "object",
                "properties": {
                    "requests": {
                        "type": "array",
                        "items": {
                            "type": "object",
                            "properties": {
                                "endpoint": {"type": "string"},
                                "method": {"type": "string", "default": "GET"},
                                "params": {"type": "object"},
                            },
                            "required": ["endpoint"],
                        },
                    },
                },
                "required": ["requests"],
            },
        },
        {
            "name": "analyze_latency",
            "description": "Analyze latency patterns for a GitHub endpoint from recent traces",
            "inputSchema": {
                "type": "object",
                "properties": {
                    "endpoint": {"type": "string", "description": "Target endpoint pattern to analyze"},
                },
                "required": ["endpoint"],
            },
        },
    ],
}


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

    # Run the real L2 spike detection pipeline
    spikes = detect_spikes([trace], system=trace.system)
    for spike in spikes:
        spike_dict = {
            "spike_type": spike.spike_type,
            "confidence": spike.confidence,
            "urgency": spike.urgency,
            "modalities": spike.modalities,
            "chain": spike.chain,
            "action_space": spike.action_space,
            "source_system": spike.source_system,
            "error_code": spike.error_code,
            "description": spike.description,
            "timestamp": datetime.now(timezone.utc).isoformat(),
        }
        _spike_signals.append(spike_dict)
        # Also feed the SSE buffer for backwards compat
        _spike_buffer.append(spike_dict)

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
                        yield f"data: {json.dumps(spike)}\n\n"
                    last_seen = current
                await asyncio.sleep(1)

        return StreamingResponse(generate(), media_type="text/event-stream")

    # --- L2 query endpoints ---

    @app.get("/endpoints")
    async def get_endpoints():
        """Return the endpoint catalog built from all ingested traces."""
        traces = list(_recent_traces)
        if not traces:
            return JSONResponse(content={"endpoints": [], "count": 0})
        result = analyze(traces)
        endpoints = []
        for key, rec in result.endpoints.items():
            endpoints.append({
                "target": rec.target,
                "method": rec.method,
                "url": rec.url,
                "hit_count": rec.hit_count,
                "status_codes": rec.status_codes,
                "error_patterns": rec.error_patterns,
                "tags": list(rec.tags),
            })
        return JSONResponse(content={"endpoints": endpoints, "count": len(endpoints)})

    @app.get("/spikes/recent")
    async def get_spikes_recent(limit: int = 50):
        """Return recent spike signals as JSON (not SSE)."""
        spikes = list(_spike_signals)[-limit:]
        return JSONResponse(content={"spikes": spikes, "count": len(spikes)})

    # --- MCP routing ---

    @app.post("/mcp/tools/list")
    async def mcp_tools_list(request: Request):
        """List available MCP tools for a system/archetype pair."""
        body = await request.json()
        system = body.get("system", "")
        archetype = body.get("archetype", "")
        key = (system.lower(), archetype.lower())
        tools = _mcp_tools.get(key, [])
        return JSONResponse(content={"tools": tools, "count": len(tools)})

    @app.post("/mcp/tools/call")
    async def mcp_tools_call(request: Request):
        """Invoke an MCP tool — proxies the request to the downstream system.

        The extension sends the Authorization header with the decrypted
        credential from its vault. The bridge forwards it as-is to the
        downstream API. Credentials never touch disk.
        """
        body = await request.json()
        system = body.get("system", "")
        archetype = body.get("archetype", "")
        tool_name = body.get("tool", "")
        arguments = body.get("arguments", {})

        # Validate tool exists
        key = (system.lower(), archetype.lower())
        tools = _mcp_tools.get(key, [])
        tool_def = next((t for t in tools if t["name"] == tool_name), None)
        if tool_def is None:
            return JSONResponse(
                content={"error": f"Unknown tool: {tool_name}", "available": [t["name"] for t in tools]},
                status_code=404,
            )

        # Forward auth header from extension → downstream API
        auth_header = request.headers.get("Authorization", "")

        # Route by system
        if system.lower() == "github":
            return await _handle_github_mcp(tool_name, arguments, auth_header)

        return JSONResponse(
            content={"error": f"No handler for system: {system}"},
            status_code=501,
        )

    return app


# --- MCP tool handlers ---

async def _handle_github_mcp(tool_name: str, arguments: dict, auth_header: str) -> JSONResponse:
    """Handle GitHub mcp-sync tool invocations."""
    base_url = "https://api.github.com"
    headers = {
        "Accept": "application/vnd.github+json",
        "X-GitHub-Api-Version": "2022-11-28",
    }
    if auth_header:
        headers["Authorization"] = auth_header

    async with httpx.AsyncClient(base_url=base_url, headers=headers, timeout=30.0) as client:
        if tool_name == "invoke":
            endpoint = arguments.get("endpoint", "")
            method = arguments.get("method", "GET").upper()
            params = arguments.get("params", {})
            if method == "GET":
                resp = await client.request(method, endpoint, params=params)
            else:
                resp = await client.request(method, endpoint, json=params)
            return JSONResponse(
                content={"status": resp.status_code, "body": resp.json() if resp.headers.get("content-type", "").startswith("application/json") else resp.text},
                status_code=200,
            )

        elif tool_name == "batch_invoke":
            requests = arguments.get("requests", [])
            results = []
            for req in requests:
                endpoint = req.get("endpoint", "")
                method = req.get("method", "GET").upper()
                params = req.get("params", {})
                if method == "GET":
                    resp = await client.request(method, endpoint, params=params)
                else:
                    resp = await client.request(method, endpoint, json=params)
                results.append({
                    "endpoint": endpoint,
                    "status": resp.status_code,
                    "body": resp.json() if resp.headers.get("content-type", "").startswith("application/json") else resp.text,
                })
            return JSONResponse(content={"results": results}, status_code=200)

        elif tool_name == "analyze_latency":
            endpoint_pattern = arguments.get("endpoint", "")
            traces = list(_recent_traces)
            matching_durations = []
            for trace in traces:
                for span in trace.spans:
                    if endpoint_pattern in (span.interaction.target or ""):
                        matching_durations.append(span.duration_ms)
            if not matching_durations:
                return JSONResponse(content={"endpoint": endpoint_pattern, "matches": 0, "stats": None})
            matching_durations.sort()
            stats = {
                "count": len(matching_durations),
                "min_ms": matching_durations[0],
                "max_ms": matching_durations[-1],
                "mean_ms": sum(matching_durations) / len(matching_durations),
                "p50_ms": matching_durations[len(matching_durations) // 2],
                "p95_ms": matching_durations[int(len(matching_durations) * 0.95)],
            }
            return JSONResponse(content={"endpoint": endpoint_pattern, "matches": len(matching_durations), "stats": stats})

    return JSONResponse(content={"error": f"Unknown tool: {tool_name}"}, status_code=400)
