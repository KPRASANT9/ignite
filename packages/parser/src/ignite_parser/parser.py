"""IGNITE L2 Parser — validates and deserializes trace JSONL.

Follows the Catalyst Parser pattern: raw bytes → validated typed records.
All validation happens here so downstream stages can trust the data.
"""

from __future__ import annotations

import json
import re
from datetime import datetime, timezone
from pathlib import Path
from typing import Any

from ignite_parser.models import (
    Actionability,
    ActualOutcome,
    AgentRole,
    Confidence,
    ContainsContext,
    DeltaFromPrior,
    ExpectedOutcome,
    Finding,
    FindingCategory,
    FindingConfidence,
    Interaction,
    Modality,
    Observation,
    ParseResult,
    Relationships,
    Request,
    RequestIntent,
    Response,
    ResponseOutcome,
    SessionIntent,
    SignalClass,
    Span,
    SpanKind,
    SpanStructure,
    State,
    StateTransitionSubtype,
    Trace,
    TraceStatus,
    ValidationError,
)

SUPPORTED_VERSIONS = {"0.1", "0.2"}

# Patterns that suggest unsanitized secrets
SECRET_PATTERNS = [
    re.compile(r"(sk|pk|access|secret|token|key)[_-]?(live|test|prod)?[_-]?[a-zA-Z0-9]{20,}", re.I),
    re.compile(r"Bearer\s+[a-zA-Z0-9._-]{20,}", re.I),
]


def parse_trace(data: dict[str, Any], source: str = "<input>") -> ParseResult:
    """Parse and validate a single trace dict into a Trace object."""
    errors: list[ValidationError] = []
    warnings: list[ValidationError] = []

    # Schema version check
    version = data.get("schema_version")
    if not version:
        errors.append(ValidationError("schema_version", "missing required field"))
        return ParseResult(errors=errors)
    if version not in SUPPORTED_VERSIONS:
        errors.append(ValidationError("schema_version", f"unsupported version '{version}', supported: {SUPPORTED_VERSIONS}"))
        return ParseResult(errors=errors)

    # Required string fields
    for field_name in ("trace_id", "agent_id", "system", "objective"):
        if not data.get(field_name):
            errors.append(ValidationError(field_name, "missing or empty required field"))

    # Agent role enum
    agent_role = _parse_enum(data.get("agent_role"), AgentRole, "agent_role", errors)

    # Status enum
    status = _parse_enum(data.get("status"), TraceStatus, "status", errors)

    # Timestamps
    started_at = _parse_timestamp(data.get("started_at"), "started_at", errors)
    ended_at = _parse_timestamp(data.get("ended_at"), "ended_at", errors, required=False)

    if errors:
        return ParseResult(errors=errors, warnings=warnings)

    # Parse spans
    raw_spans = data.get("spans", [])
    spans = []
    prev_seq = 0
    for i, raw_span in enumerate(raw_spans):
        span, span_errors, span_warnings = _parse_span(raw_span, f"spans[{i}]")
        errors.extend(span_errors)
        warnings.extend(span_warnings)
        if not span_errors:
            # Validate monotonic sequence
            if span.sequence <= prev_seq and span.sequence != 0:
                if prev_seq > 0:
                    warnings.append(ValidationError(
                        f"spans[{i}].sequence",
                        f"non-monotonic sequence: {span.sequence} after {prev_seq}"
                    ))
            prev_seq = span.sequence
            spans.append(span)

    # Parse findings
    raw_findings = data.get("findings", [])
    findings = []
    span_ids = {s.span_id for s in spans}
    for i, raw_finding in enumerate(raw_findings):
        finding, f_errors, f_warnings = _parse_finding(raw_finding, f"findings[{i}]", span_ids)
        errors.extend(f_errors)
        warnings.extend(f_warnings)
        if not f_errors:
            findings.append(finding)

    # Sanitization check on the whole trace
    _check_secrets(data, "", warnings)

    if errors:
        return ParseResult(errors=errors, warnings=warnings)

    # Schema v13: session_intent (optional)
    session_intent = _parse_enum_optional(
        data.get("session_intent"), SessionIntent, "session_intent", warnings, default=None
    )

    trace = Trace(
        schema_version=version,
        trace_id=data["trace_id"],
        agent_id=data["agent_id"],
        agent_role=agent_role,
        system=data["system"],
        study_channel=data.get("study_channel", ""),
        session_id=data.get("session_id", ""),
        started_at=started_at,
        ended_at=ended_at,
        status=status,
        objective=data["objective"],
        findings_summary=data.get("findings_summary"),
        spans=spans,
        findings=findings,
        metadata=data.get("metadata", {}),
        session_intent=session_intent,
    )

    return ParseResult(traces=[trace], warnings=warnings)


def parse_jsonl(jsonl_text: str, source: str = "<input>") -> ParseResult:
    """Parse multiple traces from a JSONL string (one JSON object per line)."""
    all_traces: list[Trace] = []
    all_errors: list[ValidationError] = []
    all_warnings: list[ValidationError] = []

    for line_num, line in enumerate(jsonl_text.strip().splitlines(), start=1):
        line = line.strip()
        if not line:
            continue
        try:
            data = json.loads(line)
        except json.JSONDecodeError as e:
            all_errors.append(ValidationError(
                f"{source}:{line_num}", f"invalid JSON: {e}"
            ))
            continue

        result = parse_trace(data, source=f"{source}:{line_num}")
        all_traces.extend(result.traces)
        for err in result.errors:
            err.path = f"{source}:{line_num}.{err.path}"
            all_errors.append(err)
        for warn in result.warnings:
            warn.path = f"{source}:{line_num}.{warn.path}"
            all_warnings.append(warn)

    return ParseResult(traces=all_traces, errors=all_errors, warnings=all_warnings)


def parse_file(path: str | Path) -> ParseResult:
    """Parse a JSONL trace file."""
    path = Path(path)
    if not path.exists():
        return ParseResult(errors=[ValidationError(str(path), "file not found")])
    text = path.read_text(encoding="utf-8")
    return parse_jsonl(text, source=str(path))


# --- Internal helpers ---

def _parse_enum(value: Any, enum_cls: type, path: str, errors: list[ValidationError]):
    """Parse a string into an enum, appending an error if invalid."""
    if not value:
        errors.append(ValidationError(path, "missing required field"))
        return None
    try:
        return enum_cls(value)
    except ValueError:
        valid = [e.value for e in enum_cls]
        errors.append(ValidationError(path, f"invalid value '{value}', expected one of {valid}"))
        return None


def _parse_enum_optional(value: Any, enum_cls: type, path: str, warnings: list[ValidationError], default=None):
    """Parse a string into an enum, using a default if missing. Warns on invalid values."""
    if value is None:
        return default
    try:
        return enum_cls(value)
    except ValueError:
        valid = [e.value for e in enum_cls]
        warnings.append(ValidationError(path, f"invalid value '{value}', expected one of {valid}; using default"))
        return default


def _parse_timestamp(value: Any, path: str, errors: list[ValidationError], required: bool = True) -> datetime | None:
    """Parse an ISO-8601 timestamp string."""
    if not value:
        if required:
            errors.append(ValidationError(path, "missing required timestamp"))
        return None
    if isinstance(value, datetime):
        return value
    try:
        dt = datetime.fromisoformat(value.replace("Z", "+00:00"))
        if dt.tzinfo is None:
            dt = dt.replace(tzinfo=timezone.utc)
        return dt
    except (ValueError, AttributeError):
        errors.append(ValidationError(path, f"invalid ISO-8601 timestamp: '{value}'"))
        return None


def _parse_span(data: dict[str, Any], path: str) -> tuple[Span | None, list[ValidationError], list[ValidationError]]:
    """Parse and validate a single span dict."""
    errors: list[ValidationError] = []
    warnings: list[ValidationError] = []

    if not isinstance(data, dict):
        errors.append(ValidationError(path, "span must be an object"))
        return None, errors, warnings

    # Required fields
    span_id = data.get("span_id", "")
    if not span_id:
        errors.append(ValidationError(f"{path}.span_id", "missing required field"))

    kind = _parse_enum(data.get("kind"), SpanKind, f"{path}.kind", errors)
    started_at = _parse_timestamp(data.get("started_at"), f"{path}.started_at", errors)
    ended_at = _parse_timestamp(data.get("ended_at"), f"{path}.ended_at", errors, required=False)

    # Observation is required — this is the "think, not just log" mandate
    obs_data = data.get("observation", {})
    if not obs_data.get("what_happened"):
        errors.append(ValidationError(f"{path}.observation.what_happened", "missing required field — every span must record what happened"))
    if not obs_data.get("what_learned"):
        errors.append(ValidationError(f"{path}.observation.what_learned", "missing required field — every span must record what was learned"))

    obs_confidence = _parse_enum(
        obs_data.get("confidence", "low"), Confidence, f"{path}.observation.confidence", errors
    )

    if errors:
        return None, errors, warnings

    # Build sub-objects
    req_data = data.get("interaction", {}).get("request", {})
    resp_data = data.get("interaction", {}).get("response", {})
    int_data = data.get("interaction", {})
    state_data = data.get("state", {})
    rel_data = data.get("relationships", {})

    # v0.2 classification fields (optional, with defaults for v0.1 compat)
    modality = _parse_enum_optional(
        data.get("modality"), Modality, f"{path}.modality", warnings, default=Modality.API
    )
    request_intent = _parse_enum_optional(
        data.get("request_intent"), RequestIntent, f"{path}.request_intent", warnings, default=RequestIntent.QUERY
    )
    response_outcome = _parse_enum_optional(
        data.get("response_outcome"), ResponseOutcome, f"{path}.response_outcome", warnings, default=ResponseOutcome.SUCCESS
    )
    signal_class = _parse_enum_optional(
        data.get("signal_class"), SignalClass, f"{path}.signal_class", warnings, default=SignalClass.INTENT_CARRYING
    )
    delta_from_prior = _parse_enum_optional(
        data.get("delta_from_prior"), DeltaFromPrior, f"{path}.delta_from_prior", warnings, default=DeltaFromPrior.FULL
    )
    state_transition_subtype = None
    raw_subtype = data.get("state_transition_subtype")
    if raw_subtype is not None:
        state_transition_subtype = _parse_enum_optional(
            raw_subtype, StateTransitionSubtype, f"{path}.state_transition_subtype", warnings, default=None
        )

    # v0.2 feedback divergence fields (optional, null defaults)
    expected_outcome = None
    raw_expected = data.get("expected_outcome")
    if raw_expected is not None and isinstance(raw_expected, dict):
        expected_outcome = ExpectedOutcome(
            description=raw_expected.get("description", ""),
            confidence=float(raw_expected.get("confidence", 0.0)),
        )

    actual_outcome = None
    raw_actual = data.get("actual_outcome")
    if raw_actual is not None and isinstance(raw_actual, dict):
        actual_outcome = ActualOutcome(
            description=raw_actual.get("description", ""),
        )

    divergence_score = data.get("divergence_score")
    if divergence_score is not None:
        try:
            divergence_score = float(divergence_score)
        except (ValueError, TypeError):
            warnings.append(ValidationError(
                f"{path}.divergence_score",
                f"invalid divergence_score '{divergence_score}', expected float; ignoring"
            ))
            divergence_score = None

    # v0.2 modality extension (pass through as dict — typed on SDK side)
    modality_ext = data.get("modality_ext")
    if modality_ext is not None and not isinstance(modality_ext, dict):
        warnings.append(ValidationError(
            f"{path}.modality_ext",
            f"modality_ext must be an object; ignoring"
        ))
        modality_ext = None

    # Schema v13 fields
    span_structure = _parse_enum_optional(
        data.get("span_structure"), SpanStructure, f"{path}.span_structure", warnings, default=None
    )

    contains_contexts: list[ContainsContext] = []
    raw_contexts = data.get("contains_contexts")
    if raw_contexts is not None:
        if isinstance(raw_contexts, list):
            for j, ctx in enumerate(raw_contexts):
                if isinstance(ctx, dict):
                    contains_contexts.append(ContainsContext(
                        modality=ctx.get("modality", ""),
                        relationship=ctx.get("relationship", ""),
                        active=ctx.get("active", True),
                        context_id=ctx.get("context_id"),
                        description=ctx.get("description"),
                    ))
                else:
                    warnings.append(ValidationError(
                        f"{path}.contains_contexts[{j}]",
                        "contains_contexts entry must be an object; skipping"
                    ))
        else:
            warnings.append(ValidationError(
                f"{path}.contains_contexts",
                "contains_contexts must be an array; ignoring"
            ))

    compression_ratio = data.get("compression_ratio")
    if compression_ratio is not None:
        try:
            compression_ratio = float(compression_ratio)
        except (ValueError, TypeError):
            warnings.append(ValidationError(
                f"{path}.compression_ratio",
                f"invalid compression_ratio '{compression_ratio}', expected float; ignoring"
            ))
            compression_ratio = None

    span = Span(
        span_id=span_id,
        trace_id=data.get("trace_id", ""),
        parent_span_id=data.get("parent_span_id"),
        sequence=data.get("sequence", 0),
        kind=kind,
        started_at=started_at,
        ended_at=ended_at,
        duration_ms=data.get("duration_ms", 0),
        interaction=Interaction(
            target=int_data.get("target", ""),
            method=int_data.get("method"),
            request=Request(
                url=req_data.get("url"),
                headers=req_data.get("headers"),
                params=req_data.get("params"),
                body=req_data.get("body"),
            ),
            response=Response(
                status_code=resp_data.get("status_code"),
                headers=resp_data.get("headers"),
                body_summary=resp_data.get("body_summary", ""),
                body_schema=resp_data.get("body_schema"),
                error=resp_data.get("error"),
            ),
        ),
        observation=Observation(
            what_happened=obs_data.get("what_happened", ""),
            what_learned=obs_data.get("what_learned", ""),
            confidence=obs_confidence,
            surprises=obs_data.get("surprises"),
            questions_raised=obs_data.get("questions_raised", []),
        ),
        state=State(
            preconditions=state_data.get("preconditions", []),
            postconditions=state_data.get("postconditions", []),
            state_changes=state_data.get("state_changes", []),
        ),
        relationships=Relationships(
            depends_on=rel_data.get("depends_on", []),
            enables=rel_data.get("enables", []),
            contradicts=rel_data.get("contradicts", []),
            refines=rel_data.get("refines", []),
        ),
        tags=data.get("tags", []),
        metadata=data.get("metadata", {}),
        modality=modality,
        request_intent=request_intent,
        response_outcome=response_outcome,
        signal_class=signal_class,
        delta_from_prior=delta_from_prior,
        state_transition_subtype=state_transition_subtype,
        correlation_ids=data.get("correlation_ids", []),
        expected_outcome=expected_outcome,
        actual_outcome=actual_outcome,
        divergence_score=divergence_score,
        modality_ext=modality_ext,
        span_structure=span_structure,
        contains_contexts=contains_contexts,
        compression_ratio=compression_ratio,
    )

    return span, errors, warnings


def _parse_finding(
    data: dict[str, Any], path: str, valid_span_ids: set[str]
) -> tuple[Finding | None, list[ValidationError], list[ValidationError]]:
    """Parse and validate a single finding dict."""
    errors: list[ValidationError] = []
    warnings: list[ValidationError] = []

    if not isinstance(data, dict):
        errors.append(ValidationError(path, "finding must be an object"))
        return None, errors, warnings

    finding_id = data.get("finding_id", "")
    if not finding_id:
        errors.append(ValidationError(f"{path}.finding_id", "missing required field"))

    category = _parse_enum(data.get("category"), FindingCategory, f"{path}.category", errors)
    confidence = _parse_enum(data.get("confidence"), FindingConfidence, f"{path}.confidence", errors)
    actionability = _parse_enum(
        data.get("actionability", "informational"), Actionability, f"{path}.actionability", errors
    )

    if not data.get("title"):
        errors.append(ValidationError(f"{path}.title", "missing required field"))
    if not data.get("description"):
        errors.append(ValidationError(f"{path}.description", "missing required field"))

    # Validate source_spans reference real spans
    source_spans = data.get("source_spans", [])
    for sid in source_spans:
        if sid not in valid_span_ids:
            warnings.append(ValidationError(
                f"{path}.source_spans", f"references unknown span_id '{sid}'"
            ))

    if errors:
        return None, errors, warnings

    finding = Finding(
        finding_id=finding_id,
        trace_id=data.get("trace_id", ""),
        source_spans=source_spans,
        category=category,
        title=data["title"],
        description=data["description"],
        evidence=data.get("evidence", []),
        confidence=confidence,
        actionability=actionability,
        related_findings=data.get("related_findings", []),
        tags=data.get("tags", []),
        metadata=data.get("metadata", {}),
    )

    return finding, errors, warnings


def _check_secrets(obj: Any, path: str, warnings: list[ValidationError]) -> None:
    """Recursively scan for potential unsanitized secrets."""
    if isinstance(obj, str):
        for pattern in SECRET_PATTERNS:
            if pattern.search(obj):
                warnings.append(ValidationError(
                    path, f"possible unsanitized secret detected — review and redact"
                ))
                return
    elif isinstance(obj, dict):
        for key, val in obj.items():
            _check_secrets(val, f"{path}.{key}" if path else key, warnings)
    elif isinstance(obj, list):
        for i, item in enumerate(obj):
            _check_secrets(item, f"{path}[{i}]", warnings)
