"""Tests for IGNITE Bridge expanded endpoints.

Covers: GET /endpoints, GET /spikes/recent, POST /mcp/tools/list,
        POST /mcp/tools/call (validation only — no real GitHub calls).
"""

import json
from pathlib import Path

import pytest
from httpx import ASGITransport, AsyncClient

from ignite_bridge.app import create_app, _recent_traces, _spike_signals, _spike_buffer


FIXTURE_DIR = Path(__file__).resolve().parent.parent.parent / "parser" / "tests" / "fixtures"


@pytest.fixture
def app():
    _recent_traces.clear()
    _spike_signals.clear()
    _spike_buffer.clear()
    return create_app()


@pytest.fixture
def valid_trace():
    with open(FIXTURE_DIR / "valid_trace.json") as f:
        return json.load(f)


@pytest.mark.anyio
async def test_health(app):
    async with AsyncClient(transport=ASGITransport(app=app), base_url="http://test") as client:
        resp = await client.get("/health")
        assert resp.status_code == 200
        assert resp.json()["status"] == "ok"


@pytest.mark.anyio
async def test_endpoints_empty(app):
    async with AsyncClient(transport=ASGITransport(app=app), base_url="http://test") as client:
        resp = await client.get("/endpoints")
        assert resp.status_code == 200
        data = resp.json()
        assert data["endpoints"] == []
        assert data["count"] == 0


@pytest.mark.anyio
async def test_endpoints_after_ingestion(app, valid_trace):
    async with AsyncClient(transport=ASGITransport(app=app), base_url="http://test") as client:
        # Ingest a trace first
        resp = await client.post("/traces", json=valid_trace)
        assert resp.status_code == 200

        # Now query endpoints
        resp = await client.get("/endpoints")
        assert resp.status_code == 200
        data = resp.json()
        assert data["count"] > 0
        # Each endpoint has required fields
        for ep in data["endpoints"]:
            assert "target" in ep
            assert "hit_count" in ep


@pytest.mark.anyio
async def test_spikes_recent_empty(app):
    async with AsyncClient(transport=ASGITransport(app=app), base_url="http://test") as client:
        resp = await client.get("/spikes/recent")
        assert resp.status_code == 200
        data = resp.json()
        assert data["spikes"] == []
        assert data["count"] == 0


@pytest.mark.anyio
async def test_spikes_recent_after_ingestion(app, valid_trace):
    async with AsyncClient(transport=ASGITransport(app=app), base_url="http://test") as client:
        # Ingest a trace
        await client.post("/traces", json=valid_trace)
        resp = await client.get("/spikes/recent")
        assert resp.status_code == 200
        data = resp.json()
        # Spikes may or may not be generated depending on trace content,
        # but the endpoint shape is correct
        assert "spikes" in data
        assert "count" in data


@pytest.mark.anyio
async def test_spikes_recent_limit(app):
    # Manually inject some spike signals
    for i in range(10):
        _spike_signals.append({"spike_type": "test", "index": i})
    async with AsyncClient(transport=ASGITransport(app=app), base_url="http://test") as client:
        resp = await client.get("/spikes/recent?limit=3")
        data = resp.json()
        assert data["count"] == 3
        # Should return the last 3
        assert data["spikes"][0]["index"] == 7


@pytest.mark.anyio
async def test_mcp_tools_list_github(app):
    async with AsyncClient(transport=ASGITransport(app=app), base_url="http://test") as client:
        resp = await client.post("/mcp/tools/list", json={"system": "github", "archetype": "mcp-sync"})
        assert resp.status_code == 200
        data = resp.json()
        assert data["count"] == 3
        tool_names = [t["name"] for t in data["tools"]]
        assert "invoke" in tool_names
        assert "batch_invoke" in tool_names
        assert "analyze_latency" in tool_names


@pytest.mark.anyio
async def test_mcp_tools_list_unknown(app):
    async with AsyncClient(transport=ASGITransport(app=app), base_url="http://test") as client:
        resp = await client.post("/mcp/tools/list", json={"system": "unknown", "archetype": "unknown"})
        assert resp.status_code == 200
        data = resp.json()
        assert data["count"] == 0
        assert data["tools"] == []


@pytest.mark.anyio
async def test_mcp_tools_call_unknown_tool(app):
    async with AsyncClient(transport=ASGITransport(app=app), base_url="http://test") as client:
        resp = await client.post("/mcp/tools/call", json={
            "system": "github",
            "archetype": "mcp-sync",
            "tool": "nonexistent",
            "arguments": {},
        })
        assert resp.status_code == 404
        data = resp.json()
        assert "error" in data
        assert "nonexistent" in data["error"]


@pytest.mark.anyio
async def test_mcp_tools_call_unknown_system(app):
    async with AsyncClient(transport=ASGITransport(app=app), base_url="http://test") as client:
        resp = await client.post("/mcp/tools/call", json={
            "system": "unknown",
            "archetype": "unknown",
            "tool": "invoke",
            "arguments": {},
        })
        assert resp.status_code == 404


@pytest.mark.anyio
async def test_mcp_tools_call_analyze_latency_no_traces(app):
    """analyze_latency should work even with no traces — returns zero matches."""
    async with AsyncClient(transport=ASGITransport(app=app), base_url="http://test") as client:
        resp = await client.post("/mcp/tools/call", json={
            "system": "github",
            "archetype": "mcp-sync",
            "tool": "analyze_latency",
            "arguments": {"endpoint": "/repos"},
        })
        assert resp.status_code == 200
        data = resp.json()
        assert data["matches"] == 0


@pytest.mark.anyio
async def test_traces_web_ingestion(app):
    async with AsyncClient(transport=ASGITransport(app=app), base_url="http://test") as client:
        resp = await client.post("/traces/web", json={
            "system": "github",
            "spans": [
                {"operation": "click", "target": "button.submit", "duration_ms": 50},
                {"operation": "navigate", "target": "/repos", "duration_ms": 200},
            ],
        })
        assert resp.status_code == 200
        data = resp.json()
        assert data["span_count"] == 2


@pytest.mark.anyio
async def test_traces_api_ingestion(app):
    """POST /traces/api auto-wraps API spans into a valid trace envelope."""
    async with AsyncClient(transport=ASGITransport(app=app), base_url="http://test") as client:
        resp = await client.post("/traces/api", json={
            "system": "github",
            "spans": [
                {
                    "target": "https://api.github.com/repos/foo/bar",
                    "method": "GET",
                    "status_code": 200,
                    "request_type": "xmlhttprequest",
                    "request_intent": "query",
                    "timestamp": "2026-08-10T09:00:00Z",
                },
                {
                    "target": "https://api.github.com/repos/foo/bar/issues",
                    "method": "POST",
                    "status_code": 201,
                    "request_type": "fetch",
                    "request_intent": "mutation",
                    "timestamp": "2026-08-10T09:00:01Z",
                },
            ],
        })
        assert resp.status_code == 200
        data = resp.json()
        assert data["span_count"] == 2
        assert data["trace_id"]  # non-empty trace ID


@pytest.mark.anyio
async def test_traces_api_unknown_system(app):
    """POST /traces/api defaults system to 'unknown' when not provided."""
    async with AsyncClient(transport=ASGITransport(app=app), base_url="http://test") as client:
        resp = await client.post("/traces/api", json={
            "spans": [
                {
                    "target": "https://internal.example.com/api/data",
                    "method": "GET",
                    "status_code": 200,
                    "request_type": "fetch",
                },
            ],
        })
        assert resp.status_code == 200
        data = resp.json()
        assert data["span_count"] == 1


@pytest.mark.anyio
async def test_traces_api_empty_spans(app):
    """POST /traces/api with empty spans still produces a valid envelope."""
    async with AsyncClient(transport=ASGITransport(app=app), base_url="http://test") as client:
        resp = await client.post("/traces/api", json={"spans": []})
        assert resp.status_code == 200
        data = resp.json()
        assert data["span_count"] == 0


@pytest.mark.anyio
async def test_auth_header_forwarding(app):
    """Verify the bridge reads Authorization header from requests."""
    async with AsyncClient(transport=ASGITransport(app=app), base_url="http://test") as client:
        # We can't make a real GitHub call, but we can verify the endpoint
        # accepts and processes the auth header without error up to the
        # point of the actual HTTP call. Use analyze_latency which doesn't
        # need a real downstream call.
        resp = await client.post(
            "/mcp/tools/call",
            json={
                "system": "github",
                "archetype": "mcp-sync",
                "tool": "analyze_latency",
                "arguments": {"endpoint": "/repos"},
            },
            headers={"Authorization": "Bearer test-token-123"},
        )
        assert resp.status_code == 200
