"""IGNITE Trace SDK — structured trace capture for exploration agents.

Provides TraceSession context manager, span/finding builders, auto-sanitization,
and exploration manifest for coverage-driven targeting.
"""

from ignite_trace.session import TraceSession
from ignite_trace.span import SpanBuilder
from ignite_trace.finding import FindingBuilder
from ignite_trace.manifest import ExplorationManifest

__all__ = [
    "TraceSession",
    "SpanBuilder",
    "FindingBuilder",
    "ExplorationManifest",
]
