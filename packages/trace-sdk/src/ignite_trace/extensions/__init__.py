"""Typed modality extensions for the IGNITE universal trace surface.

Each extension captures modality-specific interaction details that don't
fit the generic interaction.request/response model. The base API modality
reuses the existing interaction fields — no rename needed.

Extensions:
- ApiExt: HTTP API interactions (existing interaction is already this)
- DbExt: Database operations (aligned with OTel DB semconv)
- CliExt: Command-line tool invocations
- MsgExt: Message/event bus interactions
- WebExt: Browser/web interactions (Phase 2 — Playwright MCP)
"""

from ignite_trace.extensions.api_ext import ApiExt
from ignite_trace.extensions.db_ext import DbExt
from ignite_trace.extensions.cli_ext import CliExt
from ignite_trace.extensions.msg_ext import MsgExt
from ignite_trace.extensions.web_ext import WebExt

__all__ = [
    "ApiExt",
    "DbExt",
    "CliExt",
    "MsgExt",
    "WebExt",
]
