"""Entry point for running the IGNITE Bridge server.

Usage:
    python -m ignite_bridge
    ignite-bridge  (via console_scripts entry point)

Environment variables:
    IGNITE_BRIDGE_HOST  — bind address (default: 0.0.0.0)
    IGNITE_BRIDGE_PORT  — listen port  (default: 8400)
"""

from __future__ import annotations

import os

import uvicorn


def main() -> None:
    host = os.environ.get("IGNITE_BRIDGE_HOST", "0.0.0.0")
    port = int(os.environ.get("IGNITE_BRIDGE_PORT", "8400"))
    uvicorn.run(
        "ignite_bridge.app:create_app",
        factory=True,
        host=host,
        port=port,
    )


if __name__ == "__main__":
    main()
