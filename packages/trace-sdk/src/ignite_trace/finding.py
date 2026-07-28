"""Finding builder — structured findings linked to source spans."""

from __future__ import annotations

import uuid
from typing import Any, TYPE_CHECKING

if TYPE_CHECKING:
    from ignite_trace.span import SpanBuilder


class FindingBuilder:
    """Builder for a trace finding.

    Usage:
        trace.finding(
            category="error_pattern",
            title="Plaid returns 400 for auth failures",
            source_spans=[span1, span2],
            confidence="confirmed",
            description="...",
        )
    """

    def __init__(
        self,
        category: str,
        title: str,
        description: str,
        trace_id: str,
        source_spans: list[SpanBuilder] | None = None,
        confidence: str = "hypothesis",
        actionability: str = "informational",
    ):
        self.finding_id = f"finding-{uuid.uuid4().hex[:12]}"
        self.trace_id = trace_id
        self.category = category
        self.title = title
        self.description = description
        self.source_span_ids = [s.span_id for s in (source_spans or [])]
        self.confidence = confidence
        self.actionability = actionability
        self.evidence: list[str] = []
        self.related_findings: list[str] = []
        self.tags: list[str] = []
        self.metadata: dict[str, Any] = {}

    def add_evidence(self, *items: str) -> FindingBuilder:
        self.evidence.extend(items)
        return self

    def relates_to(self, finding_id: str) -> FindingBuilder:
        self.related_findings.append(finding_id)
        return self

    def tag(self, *tags: str) -> FindingBuilder:
        self.tags.extend(tags)
        return self

    def to_dict(self) -> dict[str, Any]:
        """Serialize finding to the dict format expected by ignite_parser."""
        return {
            "finding_id": self.finding_id,
            "trace_id": self.trace_id,
            "source_spans": self.source_span_ids,
            "category": self.category,
            "title": self.title,
            "description": self.description,
            "evidence": self.evidence,
            "confidence": self.confidence,
            "actionability": self.actionability,
            "related_findings": self.related_findings,
            "tags": self.tags,
            "metadata": self.metadata,
        }
