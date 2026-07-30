"""TraceSession — context manager that orchestrates trace capture.

This is the main entry point for exploration agents. Handles trace lifecycle,
span sequencing, finding collection, and JSONL output.
"""

from __future__ import annotations

import json
import uuid
from datetime import datetime, timezone
from pathlib import Path
from typing import Any

from ignite_trace.finding import FindingBuilder
from ignite_trace.span import SpanBuilder


class TraceSession:
    """Context manager for capturing a complete trace.

    Usage:
        with TraceSession(
            agent="PlaidExplorer",
            system="plaid",
            objective="Map /transactions/sync cursor pagination"
        ) as trace:
            with trace.span("api_call", target="POST /transactions/sync") as s:
                s.request(url="https://sandbox.plaid.com/transactions/sync")
                s.response(status=200, body_summary="...")
                s.observed(what_happened="...", what_learned="...")
            trace.finding(
                category="protocol",
                title="Cursor-based sync uses next_cursor field",
                description="...",
                source_spans=[s],
            )
        # Auto: validates, writes JSONL to output_dir
    """

    def __init__(
        self,
        agent: str,
        system: str,
        objective: str,
        agent_role: str = "explorer",
        session_id: str | None = None,
        output_dir: str | Path | None = None,
        study_channel: str = "",
        session_intent: str | None = None,
    ):
        self.trace_id = f"trace-{uuid.uuid4().hex[:12]}"
        self.agent = agent
        self.system = system
        self.objective = objective
        self.agent_role = agent_role
        self.session_id = session_id or f"session-{uuid.uuid4().hex[:8]}"
        self.study_channel = study_channel
        self.output_dir = Path(output_dir) if output_dir else None

        self._started_at: datetime | None = None
        self._ended_at: datetime | None = None
        self._sequence_counter = 0
        self._spans: list[SpanBuilder] = []
        self._findings: list[FindingBuilder] = []
        self._status = "active"
        self._findings_summary: str | None = None
        self._metadata: dict[str, Any] = {}
        self._session_intent: str | None = session_intent

    def __enter__(self) -> TraceSession:
        self._started_at = datetime.now(timezone.utc)
        return self

    def __exit__(self, exc_type: Any, exc_val: Any, exc_tb: Any) -> None:
        self._ended_at = datetime.now(timezone.utc)
        if exc_type is not None:
            self._status = "failed"
        else:
            self._status = "completed"

        if self.output_dir:
            self.write()

    def span(
        self,
        kind: str,
        target: str,
        parent: SpanBuilder | None = None,
    ) -> SpanBuilder:
        """Create a new span builder. Use as a context manager."""
        self._sequence_counter += 1
        sb = SpanBuilder(
            kind=kind,
            target=target,
            sequence=self._sequence_counter,
            trace_id=self.trace_id,
            parent_span_id=parent.span_id if parent else None,
        )
        self._spans.append(sb)
        return sb

    def finding(
        self,
        category: str,
        title: str,
        description: str,
        source_spans: list[SpanBuilder] | None = None,
        confidence: str = "hypothesis",
        actionability: str = "informational",
    ) -> FindingBuilder:
        """Create and register a finding."""
        fb = FindingBuilder(
            category=category,
            title=title,
            description=description,
            trace_id=self.trace_id,
            source_spans=source_spans,
            confidence=confidence,
            actionability=actionability,
        )
        self._findings.append(fb)
        return fb

    def summary(self, text: str) -> TraceSession:
        """Set the findings summary for this trace."""
        self._findings_summary = text
        return self

    def meta(self, key: str, value: Any) -> TraceSession:
        """Add metadata to the trace."""
        self._metadata[key] = value
        return self

    def intent(self, session_intent: str) -> TraceSession:
        """Set the session-level intent for this trace."""
        self._session_intent = session_intent
        return self

    def to_dict(self) -> dict[str, Any]:
        """Serialize the complete trace to dict format."""
        d: dict[str, Any] = {
            "schema_version": "0.2",
            "trace_id": self.trace_id,
            "agent_id": self.agent,
            "agent_role": self.agent_role,
            "system": self.system,
            "study_channel": self.study_channel,
            "session_id": self.session_id,
            "started_at": self._started_at.isoformat() if self._started_at else None,
            "ended_at": self._ended_at.isoformat() if self._ended_at else None,
            "status": self._status,
            "objective": self.objective,
            "findings_summary": self._findings_summary,
            "spans": [s.to_dict() for s in self._spans],
            "findings": [f.to_dict() for f in self._findings],
            "metadata": self._metadata,
        }
        if self._session_intent is not None:
            d["session_intent"] = self._session_intent
        return d

    def to_json(self, indent: int = 2) -> str:
        """Serialize the complete trace to JSON string."""
        return json.dumps(self.to_dict(), indent=indent, default=str)

    def write(self, output_dir: str | Path | None = None) -> Path:
        """Write the trace to a JSONL file in the output directory."""
        out = Path(output_dir) if output_dir else self.output_dir
        if out is None:
            out = Path(".")

        out.mkdir(parents=True, exist_ok=True)
        filepath = out / f"{self.trace_id}.json"
        filepath.write_text(self.to_json(), encoding="utf-8")
        return filepath
