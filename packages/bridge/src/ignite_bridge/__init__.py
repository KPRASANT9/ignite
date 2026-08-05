"""IGNITE Bridge — FastAPI service wrapping the L2 pipeline.

Exposes the Python L2 pipeline (Parser → Analyzer → Spike Detection)
over HTTP for the browser extension to consume. Includes MCP routing
and auth header forwarding for downstream API access.
"""

from ignite_bridge.app import create_app

__all__ = ["create_app"]
