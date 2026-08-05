"""IGNITE Transport Bridge — FastAPI service wrapping the L2 pipeline.

Exposes the existing Python L2 pipeline (Parser → Analyzer → Spike Detection)
over HTTP for the browser extension to consume.

Endpoints:
  POST /traces         — ingest a trace JSON, run through L2, return analysis + spikes
  GET  /traces/spikes  — SSE stream of spike signals for real-time extension feedback
  GET  /health         — service health check
"""

from ignite_bridge.app import create_app

__all__ = ["create_app"]
