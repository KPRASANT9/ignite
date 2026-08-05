"""Span builder — fluent API for constructing trace spans.

Each span captures one unit of observation: an API call, a doc read,
an auth flow, an error probe, a state transition, etc.
"""

from __future__ import annotations

import uuid
from datetime import datetime, timezone
from typing import Any

from ignite_trace.modality import (
    DeltaFromPrior,
    Modality,
    RequestIntent,
    ResponseOutcome,
    SignalClass,
    SpanStructure,
    StateTransitionSubtype,
)
from ignite_trace.sanitizer import sanitize_dict, sanitize_string, truncate_body


class SpanBuilder:
    """Fluent builder for a single trace span.

    Usage:
        with trace.span("api_call", target="POST /transactions/get") as s:
            s.request(url="https://...", method="POST", body={...})
            s.response(status=200, body_summary="...")
            s.observed(what_happened="...", what_learned="...")
    """

    def __init__(self, kind: str, target: str, sequence: int, trace_id: str, parent_span_id: str | None = None):
        self.span_id = f"span-{uuid.uuid4().hex[:12]}"
        self.trace_id = trace_id
        self.parent_span_id = parent_span_id
        self.kind = kind
        self.target = target
        self.sequence = sequence
        self._started_at: datetime | None = None
        self._ended_at: datetime | None = None
        self._method: str | None = None

        # Interaction
        self._request_url: str | None = None
        self._request_headers: dict[str, Any] | None = None
        self._request_params: dict[str, Any] | None = None
        self._request_body: dict[str, Any] | None = None
        self._response_status: int | None = None
        self._response_headers: dict[str, Any] | None = None
        self._response_body_summary: str = ""
        self._response_body_schema: dict[str, Any] | None = None
        self._response_error: str | None = None

        # Observation
        self._what_happened: str = ""
        self._what_learned: str = ""
        self._confidence: str = "low"
        self._surprises: str | None = None
        self._questions_raised: list[str] = []

        # State
        self._preconditions: list[str] = []
        self._postconditions: list[str] = []
        self._state_changes: list[str] = []

        # Relationships
        self._depends_on: list[str] = []
        self._enables: list[str] = []
        self._contradicts: list[str] = []
        self._refines: list[str] = []

        # Tags & metadata
        self._tags: list[str] = []
        self._metadata: dict[str, Any] = {}

        # v0.2 classification fields
        self._modality: str = Modality.API.value
        self._request_intent: str = RequestIntent.QUERY.value
        self._response_outcome: str = ResponseOutcome.SUCCESS.value
        self._signal_class: str = SignalClass.INTENT_CARRYING.value
        self._delta_from_prior: str = DeltaFromPrior.FULL.value
        self._state_transition_subtype: str | None = None
        self._correlation_ids: list[str] = []

        # v0.2 feedback divergence fields
        self._expected_outcome: dict[str, Any] | None = None
        self._actual_outcome: dict[str, Any] | None = None
        self._divergence_score: float | None = None

        # v0.2 modality extension
        self._modality_ext: dict[str, Any] | None = None

        # Schema v13 fields
        self._span_structure: str | None = None
        self._contains_contexts: list[dict[str, Any]] = []
        self._compression_ratio: float | None = None

    def __enter__(self) -> SpanBuilder:
        self._started_at = datetime.now(timezone.utc)
        return self

    def __exit__(self, *args: Any) -> None:
        self._ended_at = datetime.now(timezone.utc)

    def request(
        self,
        url: str | None = None,
        method: str | None = None,
        headers: dict[str, Any] | None = None,
        params: dict[str, Any] | None = None,
        body: dict[str, Any] | None = None,
    ) -> SpanBuilder:
        """Record request details."""
        self._request_url = url
        if method:
            self._method = method
        if headers:
            self._request_headers = sanitize_dict(headers)
        if params:
            self._request_params = sanitize_dict(params)
        if body:
            self._request_body = sanitize_dict(body)
        return self

    def response(
        self,
        status: int | None = None,
        headers: dict[str, Any] | None = None,
        body_summary: str = "",
        body_schema: dict[str, Any] | None = None,
        error: str | None = None,
    ) -> SpanBuilder:
        """Record response details."""
        self._response_status = status
        if headers:
            self._response_headers = sanitize_dict(headers)
        self._response_body_summary = truncate_body(sanitize_string(body_summary))
        if body_schema:
            self._response_body_schema = body_schema
        if error:
            self._response_error = sanitize_string(error)
        return self

    def observed(
        self,
        what_happened: str,
        what_learned: str,
        confidence: str = "high",
        surprises: str | None = None,
        questions: list[str] | None = None,
    ) -> SpanBuilder:
        """Record cognitive observation — the epistemic layer."""
        self._what_happened = what_happened
        self._what_learned = what_learned
        self._confidence = confidence
        self._surprises = surprises
        if questions:
            self._questions_raised.extend(questions)
        return self

    def state_change(self, change: str) -> SpanBuilder:
        """Record a state change (e.g., 'token status: valid → expired')."""
        self._state_changes.append(change)
        return self

    def precondition(self, condition: str) -> SpanBuilder:
        self._preconditions.append(condition)
        return self

    def postcondition(self, condition: str) -> SpanBuilder:
        self._postconditions.append(condition)
        return self

    def depends_on(self, span_id: str) -> SpanBuilder:
        self._depends_on.append(span_id)
        return self

    def enables(self, span_id: str) -> SpanBuilder:
        self._enables.append(span_id)
        return self

    def contradicts(self, span_id: str) -> SpanBuilder:
        self._contradicts.append(span_id)
        return self

    def refines(self, span_id: str) -> SpanBuilder:
        self._refines.append(span_id)
        return self

    # --- v0.2 classification setters ---

    def classify(
        self,
        modality: str | None = None,
        request_intent: str | None = None,
        response_outcome: str | None = None,
        signal_class: str | None = None,
        delta_from_prior: str | None = None,
        state_transition_subtype: str | None = None,
    ) -> SpanBuilder:
        """Set v0.2 classification fields in one call."""
        if modality is not None:
            self._modality = modality
        if request_intent is not None:
            self._request_intent = request_intent
        if response_outcome is not None:
            self._response_outcome = response_outcome
        if signal_class is not None:
            self._signal_class = signal_class
        if delta_from_prior is not None:
            self._delta_from_prior = delta_from_prior
        if state_transition_subtype is not None:
            self._state_transition_subtype = state_transition_subtype
        return self

    def correlate(self, *ids: str) -> SpanBuilder:
        """Add correlation IDs for cross-device/cross-session tracking."""
        self._correlation_ids.extend(ids)
        return self

    def expect(self, description: str, confidence: float = 0.5) -> SpanBuilder:
        """Set expected outcome before execution — the ErrP prediction."""
        self._expected_outcome = {"description": description, "confidence": confidence}
        return self

    def actual(self, description: str) -> SpanBuilder:
        """Set actual outcome after execution — compared against expected."""
        self._actual_outcome = {"description": description}
        return self

    def divergence(self, score: float) -> SpanBuilder:
        """Set divergence score (0.0 = matched expectation, 1.0 = total mismatch)."""
        self._divergence_score = max(0.0, min(1.0, score))
        return self

    def modality_ext(self, ext: Any) -> SpanBuilder:
        """Set a typed modality extension (ApiExt, DbExt, CliExt, MsgExt, WebExt).

        The extension must have a to_dict() method.
        """
        if hasattr(ext, "to_dict"):
            self._modality_ext = ext.to_dict()
        elif isinstance(ext, dict):
            self._modality_ext = ext
        return self

    # --- Schema v13 setters ---

    def structure(self, span_structure: str) -> SpanBuilder:
        """Set span structure — how this span relates to others in the session."""
        self._span_structure = span_structure
        return self

    def contains_context(
        self,
        modality: str,
        relationship: str,
        active: bool = True,
        context_id: str | None = None,
        description: str | None = None,
    ) -> SpanBuilder:
        """Add a cross-modality context reference."""
        ctx: dict[str, Any] = {
            "modality": modality,
            "relationship": relationship,
            "active": active,
        }
        if context_id is not None:
            ctx["context_id"] = context_id
        if description is not None:
            ctx["description"] = description
        self._contains_contexts.append(ctx)
        return self

    def compression(self, ratio: float) -> SpanBuilder:
        """Set the compression ratio (rows_scanned / rows_returned)."""
        self._compression_ratio = ratio
        return self

    def tag(self, *tags: str) -> SpanBuilder:
        self._tags.extend(tags)
        return self

    def meta(self, key: str, value: Any) -> SpanBuilder:
        self._metadata[key] = value
        return self

    @property
    def duration_ms(self) -> int:
        if self._started_at and self._ended_at:
            return int((self._ended_at - self._started_at).total_seconds() * 1000)
        return 0

    def to_dict(self) -> dict[str, Any]:
        """Serialize span to the dict format expected by ignite_parser."""
        d: dict[str, Any] = {
            "span_id": self.span_id,
            "trace_id": self.trace_id,
            "sequence": self.sequence,
            "kind": self.kind,
            "started_at": self._started_at.isoformat() if self._started_at else datetime.now(timezone.utc).isoformat(),
            "duration_ms": self.duration_ms,
            "interaction": {
                "target": self.target,
                "method": self._method,
                "request": {
                    "url": self._request_url,
                    "headers": self._request_headers,
                    "params": self._request_params,
                    "body": self._request_body,
                },
                "response": {
                    "status_code": self._response_status,
                    "headers": self._response_headers,
                    "body_summary": self._response_body_summary,
                    "body_schema": self._response_body_schema,
                    "error": self._response_error,
                },
            },
            "observation": {
                "what_happened": self._what_happened,
                "what_learned": self._what_learned,
                "confidence": self._confidence,
                "surprises": self._surprises,
                "questions_raised": self._questions_raised,
            },
            "state": {
                "preconditions": self._preconditions,
                "postconditions": self._postconditions,
                "state_changes": self._state_changes,
            },
            "relationships": {
                "depends_on": self._depends_on,
                "enables": self._enables,
                "contradicts": self._contradicts,
                "refines": self._refines,
            },
            "tags": self._tags,
            "metadata": self._metadata,
            # v0.2 classification fields
            "modality": self._modality,
            "request_intent": self._request_intent,
            "response_outcome": self._response_outcome,
            "signal_class": self._signal_class,
            "delta_from_prior": self._delta_from_prior,
        }

        if self._state_transition_subtype is not None:
            d["state_transition_subtype"] = self._state_transition_subtype

        if self._correlation_ids:
            d["correlation_ids"] = self._correlation_ids

        if self._expected_outcome is not None:
            d["expected_outcome"] = self._expected_outcome

        if self._actual_outcome is not None:
            d["actual_outcome"] = self._actual_outcome

        if self._divergence_score is not None:
            d["divergence_score"] = self._divergence_score

        if self._modality_ext is not None:
            d["modality_ext"] = self._modality_ext

        if self._span_structure is not None:
            d["span_structure"] = self._span_structure

        if self._contains_contexts:
            d["contains_contexts"] = self._contains_contexts

        if self._compression_ratio is not None:
            d["compression_ratio"] = self._compression_ratio

        if self.parent_span_id:
            d["parent_span_id"] = self.parent_span_id

        if self._ended_at:
            d["ended_at"] = self._ended_at.isoformat()

        return d
