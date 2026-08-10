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
from ignite_parser.spike import SpikeSignal, detect_spikes, correlate_spans, compute_correlation_boost, classify_signal
from ignite_parser.optimizer import optimize, ExtractedFSM


# --- In-memory state ---

_spike_buffer: deque[dict] = deque(maxlen=200)
_spike_signals: deque[dict] = deque(maxlen=200)
_recent_traces: deque[Trace] = deque(maxlen=100)

# --- MCP tool registry ---
# Maps (system, archetype) → list of tool definitions.
# P1: only mcp-sync tools for GitHub.

_mcp_tools: dict[tuple[str, str], list[dict]] = {
    # --- Wildcard archetypes (available for all systems) ---
    ("*", "mcp-fsm"): [
        {"name": "extract_fsm", "description": "Extract FSMs from recent traces", "inputSchema": {"type": "object", "properties": {"system": {"type": "string"}}, "required": ["system"]}},
        {"name": "get_states", "description": "List states of extracted FSM", "inputSchema": {"type": "object", "properties": {"system": {"type": "string"}}, "required": ["system"]}},
        {"name": "get_transitions", "description": "List transitions of extracted FSM", "inputSchema": {"type": "object", "properties": {"system": {"type": "string"}}, "required": ["system"]}},
    ],
    ("*", "mcp-async"): [
        {"name": "correlate_spans", "description": "Correlate spans across modalities", "inputSchema": {"type": "object", "properties": {"system": {"type": "string"}, "window_ms": {"type": "number"}}, "required": ["system"]}},
        {"name": "get_temporal_patterns", "description": "Analyze temporal patterns", "inputSchema": {"type": "object", "properties": {"system": {"type": "string"}, "window_minutes": {"type": "number"}}, "required": ["system"]}},
        {"name": "detect_cadence", "description": "Detect request cadence stability", "inputSchema": {"type": "object", "properties": {"system": {"type": "string"}}, "required": ["system"]}},
    ],
    ("*", "mcp-scoring"): [
        {"name": "compute_boost", "description": "Compute cross-modality correlation boost", "inputSchema": {"type": "object", "properties": {"system": {"type": "string"}}, "required": ["system"]}},
        {"name": "score_system", "description": "Score overall system health", "inputSchema": {"type": "object", "properties": {"system": {"type": "string"}}, "required": ["system"]}},
        {"name": "get_modality_coverage", "description": "List observed modalities and signal rates", "inputSchema": {"type": "object", "properties": {"system": {"type": "string"}}, "required": ["system"]}},
    ],
    ("*", "mcp-orchestration"): [
        {"name": "generate_plan", "description": "Generate execution plan from traces", "inputSchema": {"type": "object", "properties": {"system": {"type": "string"}, "objective": {"type": "string"}}, "required": ["system"]}},
        {"name": "get_execution_order", "description": "Get topologically-sorted execution order", "inputSchema": {"type": "object", "properties": {"system": {"type": "string"}}, "required": ["system"]}},
        {"name": "validate_plan", "description": "Validate plan for circular dependencies", "inputSchema": {"type": "object", "properties": {"system": {"type": "string"}}, "required": ["system"]}},
    ],
    # --- System-specific archetypes ---
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
                "target": raw.get("selector", raw.get("page_url", "")),
                "method": None,
                "request": {},
                "response": {},
            },
            "observation": {
                "what_happened": f"Web interaction: {raw.get('action', 'unknown')} on {raw.get('selector', raw.get('page_url', 'unknown'))}",
                "what_learned": f"Captured {raw.get('action', 'unknown')} event on {raw.get('page_url', 'unknown')} via WebExt content script",
                "confidence": "low",
            },
            "metadata": {
                "modality": "web",
                "web_kind": "client",
                "action": raw.get("action", ""),
                "page_url": raw.get("page_url", ""),
                "page_title": raw.get("page_title", ""),
                "element_role": raw.get("element_role", ""),
                "element_text": raw.get("element_text", ""),
                "input_type": raw.get("input_type", ""),
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


# --- ApiExt span → trace envelope ---

def _wrap_api_spans(spans: list[dict], system: str = "unknown") -> dict:
    """Wrap an array of ApiExt-shaped spans (from webRequest API) into a full trace envelope."""
    trace_id = str(uuid.uuid4())
    now = datetime.now(timezone.utc).isoformat()

    wrapped_spans = []
    for i, raw in enumerate(spans):
        method = raw.get("method", "GET")
        target = raw.get("target", "")
        status_code = raw.get("status_code")
        request_intent = raw.get("request_intent", "query")
        span_system = raw.get("system", system)

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
                "target": target,
                "method": method,
                "request": {},
                "response": {"status_code": status_code} if status_code is not None else {},
            },
            "observation": {
                "what_happened": f"API call: {method} {target} → {status_code or '?'}",
                "what_learned": f"Captured {request_intent} request to {span_system} via webRequest API",
                "confidence": "low",
            },
            "metadata": {
                "modality": "api",
                "request_type": raw.get("request_type", ""),
                "request_intent": request_intent,
                "system": span_system,
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
        "objective": "Capture API observation trace from browser webRequest API",
        "spans": wrapped_spans,
        "findings": [],
        "metadata": {"modality": "api", "kind": "webRequest"},
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

    @app.post("/traces/api")
    async def post_traces_api(request: Request):
        body = await request.json()
        spans = body.get("spans", body if isinstance(body, list) else [])
        system = body.get("system", "unknown") if isinstance(body, dict) else "unknown"
        envelope = _wrap_api_spans(spans, system=system)
        resp = ingest_trace(envelope)
        status = 200 if not resp.errors else 422
        return JSONResponse(content=_trace_response_dict(resp), status_code=status)

    @app.get("/traces/spikes")
    async def spike_stream():
        async def generate():
            yield f"data: {json.dumps({'type': 'connected'})}\n\n"
            last_seen = len(_spike_buffer)
            while True:
                current = len(_spike_buffer)
                if current > last_seen:
                    for spike in list(_spike_buffer)[last_seen:current]:
                        yield f"data: {json.dumps({'type': 'spike', 'spike': spike})}\n\n"
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
        # Check system-specific first, then wildcard
        tools = _mcp_tools.get((system.lower(), archetype.lower()), [])
        if not tools:
            tools = _mcp_tools.get(("*", archetype.lower()), [])
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

        # Validate tool exists (check system-specific, then wildcard)
        key = (system.lower(), archetype.lower())
        tools = _mcp_tools.get(key, [])
        if not tools:
            tools = _mcp_tools.get(("*", archetype.lower()), [])
        tool_def = next((t for t in tools if t["name"] == tool_name), None)
        if tool_def is None:
            return JSONResponse(
                content={"error": f"Unknown tool: {tool_name}", "available": [t["name"] for t in tools]},
                status_code=404,
            )

        # Forward auth header from extension → downstream API
        auth_header = request.headers.get("Authorization", "")

        # Route by archetype first (wildcard handlers), then by system
        archetype_lower = archetype.lower()
        if archetype_lower == "mcp-fsm":
            return _handle_fsm_mcp(tool_name, arguments)
        if archetype_lower == "mcp-async":
            return _handle_async_mcp(tool_name, arguments)
        if archetype_lower == "mcp-scoring":
            return _handle_scoring_mcp(tool_name, arguments)
        if archetype_lower == "mcp-orchestration":
            return _handle_orchestration_mcp(tool_name, arguments)

        # System-specific routing
        if system.lower() == "github":
            return await _handle_github_mcp(tool_name, arguments, auth_header)

        return JSONResponse(
            content={"error": f"No handler for system: {system}, archetype: {archetype}"},
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


# --- mcp-fsm handlers (place coding) ---

def _handle_fsm_mcp(tool_name: str, arguments: dict) -> JSONResponse:
    """Handle mcp-fsm tool invocations — wraps L2 optimizer FSM extraction."""
    traces = list(_recent_traces)
    system = arguments.get("system", "")

    if not traces:
        return JSONResponse(content={"fsms": [], "message": "No traces available for FSM extraction"})

    # Run optimizer to extract FSMs
    from ignite_parser.analyzer import analyze
    analysis = analyze(traces)
    optimized = optimize(analysis, traces)

    fsms = optimized.fsms if hasattr(optimized, "fsms") else []

    if tool_name == "extract_fsm":
        return JSONResponse(content={
            "fsms": [
                {
                    "name": f.name,
                    "system": f.system,
                    "state_count": f.state_count,
                    "transition_count": f.transition_count,
                    "states": [{"name": s.name, "is_initial": s.is_initial, "is_terminal": s.is_terminal, "is_error": s.is_error} for s in f.states],
                    "transitions": [{"from_state": t.from_state, "to_state": t.to_state, "trigger": t.trigger} for t in f.transitions],
                }
                for f in fsms
            ],
            "count": len(fsms),
        })

    if tool_name == "get_states":
        all_states = []
        for f in fsms:
            for s in f.states:
                all_states.append({"fsm": f.name, "name": s.name, "is_initial": s.is_initial, "is_terminal": s.is_terminal, "is_error": s.is_error})
        return JSONResponse(content={"states": all_states, "count": len(all_states)})

    if tool_name == "get_transitions":
        all_transitions = []
        for f in fsms:
            for t in f.transitions:
                all_transitions.append({"fsm": f.name, "from_state": t.from_state, "to_state": t.to_state, "trigger": t.trigger})
        return JSONResponse(content={"transitions": all_transitions, "count": len(all_transitions)})

    return JSONResponse(content={"error": f"Unknown mcp-fsm tool: {tool_name}"}, status_code=400)


# --- mcp-async handlers (temporal coding) ---

def _handle_async_mcp(tool_name: str, arguments: dict) -> JSONResponse:
    """Handle mcp-async tool invocations — wraps L2 temporal correlation."""
    traces = list(_recent_traces)
    system = arguments.get("system", "")

    if not traces:
        return JSONResponse(content={"groups": [], "message": "No traces available"})

    all_spans = [span for trace in traces for span in trace.spans]

    if tool_name == "correlate_spans":
        window_ms = arguments.get("window_ms", 5000)
        groups = correlate_spans(all_spans, window_ms=window_ms)
        return JSONResponse(content={
            "groups": [
                {
                    "span_count": len(group),
                    "modalities": list({s.modality for s in group if s.modality}),
                    "trace_ids": list({s.trace_id for s in group if s.trace_id}),
                }
                for group in groups
            ],
            "total_groups": len(groups),
            "window_ms": window_ms,
        })

    if tool_name == "get_temporal_patterns":
        # Analyze timing intervals between spans
        timestamps = sorted([s.started_at for s in all_spans if s.started_at])
        intervals = []
        for i in range(1, len(timestamps)):
            try:
                from datetime import datetime
                t1 = datetime.fromisoformat(timestamps[i - 1].replace("Z", "+00:00"))
                t2 = datetime.fromisoformat(timestamps[i].replace("Z", "+00:00"))
                intervals.append((t2 - t1).total_seconds() * 1000)
            except (ValueError, TypeError):
                pass
        avg_interval = sum(intervals) / len(intervals) if intervals else 0
        variance = sum((x - avg_interval) ** 2 for x in intervals) / len(intervals) if intervals else 0
        return JSONResponse(content={
            "span_count": len(all_spans),
            "interval_count": len(intervals),
            "avg_interval_ms": round(avg_interval, 1),
            "variance_ms2": round(variance, 1),
            "is_stable": variance < (avg_interval ** 2 * 0.1) if avg_interval > 0 else False,
        })

    if tool_name == "detect_cadence":
        timestamps = sorted([s.started_at for s in all_spans if s.started_at])
        intervals = []
        for i in range(1, len(timestamps)):
            try:
                from datetime import datetime
                t1 = datetime.fromisoformat(timestamps[i - 1].replace("Z", "+00:00"))
                t2 = datetime.fromisoformat(timestamps[i].replace("Z", "+00:00"))
                intervals.append((t2 - t1).total_seconds() * 1000)
            except (ValueError, TypeError):
                pass
        if not intervals:
            return JSONResponse(content={"cadence_ms": 0, "stable": False, "confidence": 0})
        avg = sum(intervals) / len(intervals)
        variance = sum((x - avg) ** 2 for x in intervals) / len(intervals)
        stability = max(0.0, 1.0 - (variance / (avg ** 2 + 1e-6)))
        return JSONResponse(content={
            "cadence_ms": round(avg, 1),
            "stable": stability > 0.8,
            "confidence": round(stability, 3),
            "sample_count": len(intervals),
        })

    return JSONResponse(content={"error": f"Unknown mcp-async tool: {tool_name}"}, status_code=400)


# --- mcp-scoring handlers (population coding) ---

def _handle_scoring_mcp(tool_name: str, arguments: dict) -> JSONResponse:
    """Handle mcp-scoring tool invocations — wraps L2 cross-modality scoring."""
    traces = list(_recent_traces)
    system = arguments.get("system", "")

    if not traces:
        return JSONResponse(content={"score": 0, "message": "No traces available"})

    all_spans = [span for trace in traces for span in trace.spans]
    modalities = list({s.modality for s in all_spans if s.modality})
    modality_count = len(modalities)

    if tool_name == "compute_boost":
        boost = compute_correlation_boost(modality_count)
        return JSONResponse(content={
            "modality_count": modality_count,
            "modalities": modalities,
            "boost": boost,
        })

    if tool_name == "score_system":
        boost = compute_correlation_boost(modality_count)
        recent_spikes = list(_spike_signals)
        system_spikes = [s for s in recent_spikes if s.get("source_system") == system]
        error_count = sum(1 for s in system_spikes if s.get("spike_type") == "error")
        total_spans = len(all_spans)
        intent_carrying = sum(1 for s in all_spans if classify_signal(s) == "INTENT_CARRYING")
        signal_ratio = intent_carrying / total_spans if total_spans > 0 else 0
        health_score = max(0.0, min(1.0, (signal_ratio * boost) - (error_count * 0.1)))
        return JSONResponse(content={
            "system": system,
            "health_score": round(health_score, 3),
            "modality_count": modality_count,
            "boost": boost,
            "signal_ratio": round(signal_ratio, 3),
            "error_spikes": error_count,
            "total_spans": total_spans,
        })

    if tool_name == "get_modality_coverage":
        coverage = {}
        for mod in modalities:
            mod_spans = [s for s in all_spans if s.modality == mod]
            intent_count = sum(1 for s in mod_spans if classify_signal(s) == "INTENT_CARRYING")
            coverage[mod] = {
                "span_count": len(mod_spans),
                "intent_carrying": intent_count,
                "signal_rate": round(intent_count / len(mod_spans), 4) if mod_spans else 0,
            }
        return JSONResponse(content={"modalities": coverage, "total_modalities": modality_count})

    return JSONResponse(content={"error": f"Unknown mcp-scoring tool: {tool_name}"}, status_code=400)


# --- mcp-orchestration handlers (sequence coding) ---

def _handle_orchestration_mcp(tool_name: str, arguments: dict) -> JSONResponse:
    """Handle mcp-orchestration tool invocations — wraps L2 execution planning."""
    traces = list(_recent_traces)
    system = arguments.get("system", "")

    if not traces:
        return JSONResponse(content={"plan": None, "message": "No traces available for planning"})

    # Run the full L2 pipeline: analyze → optimize
    from ignite_parser.analyzer import analyze
    analysis = analyze(traces)
    optimized = optimize(analysis, traces)

    if tool_name == "generate_plan":
        # Build a plan from optimized findings
        findings = optimized.merged_findings if hasattr(optimized, "merged_findings") else []
        plan_steps = []
        for i, f in enumerate(findings[:20]):  # Cap at 20 steps
            plan_steps.append({
                "step": i + 1,
                "action": f.action if hasattr(f, "action") else "investigate",
                "target": f.target if hasattr(f, "target") else "",
                "description": f.description if hasattr(f, "description") else str(f),
                "priority": f.severity if hasattr(f, "severity") else "medium",
            })
        return JSONResponse(content={
            "plan": {"system": system, "steps": plan_steps, "step_count": len(plan_steps)},
        })

    if tool_name == "get_execution_order":
        # Return findings in dependency-sorted order (simplified topological sort)
        findings = optimized.merged_findings if hasattr(optimized, "merged_findings") else []
        # Group by severity for ordering: critical → high → medium → low
        severity_order = {"critical": 0, "high": 1, "medium": 2, "low": 3}
        ordered = sorted(
            [{"description": str(f), "severity": getattr(f, "severity", "medium")} for f in findings[:20]],
            key=lambda x: severity_order.get(x["severity"], 4),
        )
        return JSONResponse(content={"ordered_steps": ordered, "count": len(ordered)})

    if tool_name == "validate_plan":
        # Basic validation — check for duplicate targets, empty steps
        findings = optimized.merged_findings if hasattr(optimized, "merged_findings") else []
        targets = [getattr(f, "target", "") for f in findings]
        duplicates = [t for t in set(targets) if targets.count(t) > 1 and t]
        return JSONResponse(content={
            "valid": len(duplicates) == 0,
            "step_count": len(findings),
            "duplicate_targets": duplicates,
            "warnings": [f"Duplicate target: {d}" for d in duplicates],
        })

    return JSONResponse(content={"error": f"Unknown mcp-orchestration tool: {tool_name}"}, status_code=400)
