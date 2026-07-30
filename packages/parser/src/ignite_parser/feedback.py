"""IGNITE L2 Feedback Writer — Captures execution results as action_result traces.

Wraps plan execution in a TraceSession, recording each action's outcome
as an action_result span. The resulting trace feeds back through the pipeline
to close the intelligence loop.

v0.2 additions:
- expected_outcome / actual_outcome / divergence_score on each span
- ErrP correction strategy table: maps (intent, outcome) -> correction action
- compute_divergence() for semantic distance between expected and actual

Pipeline: ... -> Executor -> **Feedback** -> (re-enters Parser)
"""

from __future__ import annotations

from pathlib import Path
from typing import Any

from ignite_parser.executor import ActionResult, ActionStatus, ExecutionResult
from ignite_parser.models import CorrectionAction, RequestIntent, ResponseOutcome
from ignite_parser.planner import ActionKind, ExecutionPlan


# --- ErrP Correction Strategy Table ---
# Maps (request_intent, response_outcome) pairs to correction actions.
# From Fizz's cross-functional bridge spec.

ERRP_CORRECTION_TABLE: dict[tuple[str, str], CorrectionAction] = {
    # query + rate_limited → backoff + retry same query
    (RequestIntent.QUERY.value, ResponseOutcome.RATE_LIMITED.value): CorrectionAction.BACKOFF_RETRY,
    # query + auth_failure → re-authenticate, then retry
    (RequestIntent.QUERY.value, ResponseOutcome.AUTH_FAILURE.value): CorrectionAction.REAUTH_RETRY,
    # query + error → retry (reads are safe to retry)
    (RequestIntent.QUERY.value, ResponseOutcome.ERROR.value): CorrectionAction.BACKOFF_RETRY,
    # query + timeout → retry with backoff
    (RequestIntent.QUERY.value, ResponseOutcome.TIMEOUT.value): CorrectionAction.BACKOFF_RETRY,
    # mutation + error → log and escalate (do NOT auto-retry mutations)
    (RequestIntent.MUTATION.value, ResponseOutcome.ERROR.value): CorrectionAction.LOG_ESCALATE,
    # mutation + partial → check idempotency key, retry if safe
    (RequestIntent.MUTATION.value, ResponseOutcome.PARTIAL.value): CorrectionAction.IDEMPOTENCY_RETRY,
    # mutation + rate_limited → backoff (safe to retry, mutation not yet executed)
    (RequestIntent.MUTATION.value, ResponseOutcome.RATE_LIMITED.value): CorrectionAction.BACKOFF_RETRY,
    # mutation + auth_failure → re-authenticate, then retry
    (RequestIntent.MUTATION.value, ResponseOutcome.AUTH_FAILURE.value): CorrectionAction.REAUTH_RETRY,
    # mutation + timeout → log and escalate (unknown if side effect occurred)
    (RequestIntent.MUTATION.value, ResponseOutcome.TIMEOUT.value): CorrectionAction.LOG_ESCALATE,
    # state_transition + auth_failure → re-enter auth flow from beginning
    (RequestIntent.STATE_TRANSITION.value, ResponseOutcome.AUTH_FAILURE.value): CorrectionAction.RESTART_FLOW,
    # state_transition + error → restart the flow
    (RequestIntent.STATE_TRANSITION.value, ResponseOutcome.ERROR.value): CorrectionAction.RESTART_FLOW,
    # state_transition + timeout → restart the flow
    (RequestIntent.STATE_TRANSITION.value, ResponseOutcome.TIMEOUT.value): CorrectionAction.RESTART_FLOW,
    # config_change + error → escalate to human
    (RequestIntent.CONFIG_CHANGE.value, ResponseOutcome.ERROR.value): CorrectionAction.ESCALATE_HUMAN,
    # config_change + auth_failure → escalate to human
    (RequestIntent.CONFIG_CHANGE.value, ResponseOutcome.AUTH_FAILURE.value): CorrectionAction.ESCALATE_HUMAN,
    # decision + error → escalate to human (operator decisions need human review)
    (RequestIntent.DECISION.value, ResponseOutcome.ERROR.value): CorrectionAction.ESCALATE_HUMAN,
}


def _load_plaid_override(error_code: str) -> str | None:
    """Try to load Plaid profile's ErrP override for an error code."""
    try:
        from ignite_trace.profiles.plaid import lookup_errp_override
        return lookup_errp_override(error_code)
    except ImportError:
        return None


def _load_db_override(error_code: str) -> str | None:
    """Try to load DB profile's ErrP override for an error code."""
    try:
        from ignite_trace.profiles.db import lookup_db_errp_override
        return lookup_db_errp_override(error_code)
    except ImportError:
        return None


def lookup_correction(
    request_intent: str,
    response_outcome: str,
    error_code: str | None = None,
) -> CorrectionAction | None:
    """Look up the ErrP correction action, with error-code override.

    M15 correction: error code determines retry safety, not intent class.
    ITEM_LOCKED (query) must NOT be retried; INTERNAL_SERVER_ERROR (mutation) CAN.
    The error_code override takes precedence when available.

    Returns None if the pair has no correction (e.g., success outcomes).
    """
    if response_outcome == ResponseOutcome.SUCCESS.value:
        return None

    # P0: Error-code-level override — takes precedence over intent-class default
    # Check all available profiles (Plaid API, DB, etc.) for error code overrides
    if error_code is not None:
        for _profile_loader in (_load_plaid_override, _load_db_override):
            override = _profile_loader(error_code)
            if override is not None:
                return CorrectionAction(override)

    return ERRP_CORRECTION_TABLE.get((request_intent, response_outcome))


def compute_divergence(
    expected_description: str,
    actual_description: str,
    expected_confidence: float = 0.5,
) -> float:
    """Compute divergence score between expected and actual outcomes.

    Returns 0.0 (perfect match) to 1.0 (total mismatch).

    Strategy:
    - Exact match → 0.0
    - No expected outcome → 0.5 (unknown baseline)
    - Keyword overlap ratio → weighted divergence
    - High-confidence mismatches score higher divergence
    """
    if not expected_description and not actual_description:
        return 0.0
    if not expected_description:
        return 0.5  # no prediction = neutral divergence
    if not actual_description:
        return 0.8  # expected something, got nothing

    # Normalize for comparison
    expected_lower = expected_description.lower().strip()
    actual_lower = actual_description.lower().strip()

    if expected_lower == actual_lower:
        return 0.0

    # Token overlap — simple but effective for structured descriptions
    expected_tokens = set(expected_lower.split())
    actual_tokens = set(actual_lower.split())

    if not expected_tokens or not actual_tokens:
        return 1.0

    intersection = expected_tokens & actual_tokens
    union = expected_tokens | actual_tokens
    jaccard = len(intersection) / len(union) if union else 0.0

    # Raw divergence from token similarity
    raw_divergence = 1.0 - jaccard

    # Weight by confidence: high-confidence mismatches are more significant
    weighted = raw_divergence * (0.5 + 0.5 * expected_confidence)

    return min(1.0, max(0.0, round(weighted, 3)))


def capture_feedback(
    plan: ExecutionPlan,
    execution: ExecutionResult,
    output_dir: str | Path = ".",
    system: str = "ignite",
) -> Path | None:
    """Capture execution results as an action_result trace.

    Uses TraceSession to produce a valid trace that the Parser can
    re-ingest. Each ActionResult becomes an action_result span.

    Returns the path to the written trace file, or None if no results.
    """
    if not execution.results:
        return None

    try:
        from ignite_trace import TraceSession
    except ImportError:
        # Trace SDK not installed — write a plain JSON fallback
        return _write_fallback(plan, execution, output_dir)

    feedback_dir = Path(output_dir) / "feedback"
    feedback_dir.mkdir(parents=True, exist_ok=True)

    with TraceSession(
        agent="ignite-executor",
        system=system,
        objective=f"Execute plan with {plan.action_count} actions",
        agent_role="explorer",
        output_dir=str(feedback_dir),
    ) as trace:
        trace.summary(
            f"Executed {execution.total} actions: "
            f"{execution.succeeded} succeeded, "
            f"{execution.failed} failed, "
            f"{execution.skipped} skipped"
        )
        trace.meta("plan_action_count", plan.action_count)
        trace.meta("success_rate", execution.success_rate)

        for ar in execution.results:
            with trace.span("action_result", target=ar.title) as span:
                # v0.2: Set expected and actual outcomes for divergence detection
                expected_desc = _describe_expected(ar)
                actual_desc = _describe_outcome(ar)
                span.expect(expected_desc, confidence=0.8)
                span.actual(actual_desc)

                # Compute divergence: how far did the result deviate from expectation?
                div_score = compute_divergence(expected_desc, actual_desc, 0.8)
                span.divergence(div_score)

                # Classify the span for ErrP correction
                intent = _infer_intent(ar)
                outcome = _infer_outcome(ar)
                span.classify(
                    request_intent=intent,
                    response_outcome=outcome,
                )

                # Look up correction action
                correction = lookup_correction(intent, outcome)
                if correction is not None:
                    span.meta("correction_action", correction.value)

                span.observed(
                    what_happened=actual_desc,
                    what_learned=_describe_learning(ar),
                    confidence="high" if ar.status == ActionStatus.SUCCESS else "medium",
                )

                if ar.generated_files:
                    for f in ar.generated_files:
                        span.postcondition(f"Generated file: {f}")

                if ar.error:
                    span.state_change(f"Error: {ar.error}")

                span.meta("action_id", ar.action_id)
                span.meta("action_kind", ar.kind.value)
                span.meta("status", ar.status.value)
                span.meta("metrics", ar.metrics)

            # Create findings for significant outcomes
            if ar.status == ActionStatus.SUCCESS:
                trace.finding(
                    category="protocol",
                    title=f"Action completed: {ar.title}",
                    description=_describe_outcome(ar),
                    source_spans=[span],
                    confidence="confirmed",
                    actionability="informational",
                )
            elif ar.status == ActionStatus.FAILED:
                trace.finding(
                    category="error_pattern",
                    title=f"Action failed: {ar.title}",
                    description=f"Failed with error: {ar.error}",
                    source_spans=[span],
                    confidence="confirmed",
                    actionability="immediate",
                )

    # Find the written file
    written = list(feedback_dir.glob("trace-*.json"))
    return written[-1] if written else None


def _write_fallback(
    plan: ExecutionPlan,
    execution: ExecutionResult,
    output_dir: str | Path,
) -> Path:
    """Write a plain JSON feedback file when TraceSession is unavailable."""
    import json
    import uuid

    feedback_dir = Path(output_dir) / "feedback"
    feedback_dir.mkdir(parents=True, exist_ok=True)

    data = {
        "type": "execution_feedback",
        "plan_actions": plan.action_count,
        "total": execution.total,
        "succeeded": execution.succeeded,
        "failed": execution.failed,
        "skipped": execution.skipped,
        "success_rate": execution.success_rate,
        "results": [
            {
                "action_id": ar.action_id,
                "kind": ar.kind.value,
                "status": ar.status.value,
                "title": ar.title,
                "outputs": ar.outputs,
                "generated_files": ar.generated_files,
                "error": ar.error,
                "metrics": ar.metrics,
            }
            for ar in execution.results
        ],
    }

    filepath = feedback_dir / f"feedback-{uuid.uuid4().hex[:12]}.json"
    filepath.write_text(json.dumps(data, indent=2), encoding="utf-8")
    return filepath


def _describe_outcome(ar: ActionResult) -> str:
    """Generate a human-readable description of an action's outcome."""
    if ar.status == ActionStatus.SUCCESS:
        outputs = ", ".join(ar.outputs[:3]) if ar.outputs else "no outputs"
        return f"{ar.kind.value} completed successfully. Outputs: {outputs}"
    elif ar.status == ActionStatus.PARTIAL:
        return f"{ar.kind.value} partially completed. {ar.error or ''}"
    elif ar.status == ActionStatus.FAILED:
        return f"{ar.kind.value} failed: {ar.error or 'unknown error'}"
    else:
        return f"{ar.kind.value} skipped: {ar.error or 'dependencies not met'}"


def _describe_learning(ar: ActionResult) -> str:
    """Generate what was learned from executing this action."""
    if ar.status == ActionStatus.SUCCESS:
        files = len(ar.generated_files)
        return f"Successfully generated {files} file(s). Pipeline can produce {ar.kind.value} artifacts."
    elif ar.status == ActionStatus.FAILED:
        return f"Execution failed — {ar.error}. May need different approach or missing data."
    else:
        return f"Action {ar.status.value} — no new learning from this execution."


def _describe_expected(ar: ActionResult) -> str:
    """Generate what was expected to happen for this action."""
    kind_labels = {
        ActionKind.GENERATE_MODEL: "generate_model completed successfully with valid syntax",
        ActionKind.GENERATE_HANDLER: "generate_handler completed successfully with valid syntax",
        ActionKind.GENERATE_CLIENT: "generate_client completed successfully with valid syntax",
        ActionKind.EXPLORE: "explore completed successfully producing directive",
        ActionKind.VALIDATE: "validate completed successfully producing directive",
    }
    return kind_labels.get(ar.kind, f"{ar.kind.value} completed successfully")


def _infer_intent(ar: ActionResult) -> str:
    """Infer the request_intent from an action's kind."""
    if ar.kind in (ActionKind.GENERATE_MODEL, ActionKind.GENERATE_HANDLER, ActionKind.GENERATE_CLIENT):
        return RequestIntent.MUTATION.value
    elif ar.kind in (ActionKind.EXPLORE, ActionKind.VALIDATE):
        return RequestIntent.QUERY.value
    return RequestIntent.QUERY.value


def _infer_outcome(ar: ActionResult) -> str:
    """Infer the response_outcome from an action's execution status."""
    if ar.status == ActionStatus.SUCCESS:
        return ResponseOutcome.SUCCESS.value
    elif ar.status == ActionStatus.PARTIAL:
        return ResponseOutcome.PARTIAL.value
    elif ar.status == ActionStatus.FAILED:
        error_lower = (ar.error or "").lower()
        if "auth" in error_lower:
            return ResponseOutcome.AUTH_FAILURE.value
        if "rate" in error_lower or "limit" in error_lower:
            return ResponseOutcome.RATE_LIMITED.value
        if "timeout" in error_lower:
            return ResponseOutcome.TIMEOUT.value
        return ResponseOutcome.ERROR.value
    else:
        return ResponseOutcome.ERROR.value
