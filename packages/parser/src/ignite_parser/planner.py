"""IGNITE L2 Planner — Fifth stage of the pipeline (the decoder).

Consumes an OptimizedResult and produces an ExecutionPlan: an ordered list
of actions that agents can execute. This is the bridge between intelligence
(what the system knows) and intent (what the system should do next).

Pipeline: Parser → Analyzer → Optimizer → **Planner** → Operator

Finding-to-action mapping:
  state_machine finding + FSM  → GenerateModel (typed FSM dataclass)
  error_pattern finding        → GenerateHandler (error recovery routing)
  protocol finding             → GenerateClient (typed API client)
  coverage gap                 → Explore (manifest-driven next target)
  low-confidence finding       → Validate (re-explore with different approach)
"""

from __future__ import annotations

from dataclasses import dataclass, field
from enum import Enum
from typing import Any

from ignite_parser.models import FindingCategory, FindingConfidence, SpanKind
from ignite_parser.optimizer import (
    ExtractedFSM,
    MergedFinding,
    OptimizedResult,
)


# --- Action types ---


class ActionKind(str, Enum):
    """What kind of action the plan step represents."""
    GENERATE_MODEL = "generate_model"
    GENERATE_HANDLER = "generate_handler"
    GENERATE_CLIENT = "generate_client"
    EXPLORE = "explore"
    VALIDATE = "validate"


class ActionPriority(str, Enum):
    """Urgency of the action."""
    CRITICAL = "critical"
    HIGH = "high"
    MEDIUM = "medium"
    LOW = "low"


# --- Plan models ---


@dataclass
class PlanAction:
    """A single executable step in the plan."""
    action_id: str
    kind: ActionKind
    priority: ActionPriority
    title: str
    description: str
    source_finding_id: str | None = None
    source_fsm_name: str | None = None
    preconditions: list[str] = field(default_factory=list)
    depends_on: list[str] = field(default_factory=list)
    outputs: list[str] = field(default_factory=list)
    metadata: dict[str, Any] = field(default_factory=dict)


@dataclass
class ExecutionPlan:
    """Ordered list of actions derived from optimized intelligence."""
    actions: list[PlanAction] = field(default_factory=list)
    source_finding_count: int = 0
    source_fsm_count: int = 0
    coverage_gaps: list[str] = field(default_factory=list)

    @property
    def action_count(self) -> int:
        return len(self.actions)

    def actions_by_kind(self, kind: ActionKind) -> list[PlanAction]:
        return [a for a in self.actions if a.kind == kind]

    def actions_by_priority(self, priority: ActionPriority) -> list[PlanAction]:
        return [a for a in self.actions if a.priority == priority]

    def topological_order(self) -> list[PlanAction]:
        """Return actions in dependency-respecting order.

        Actions with no dependencies come first. Actions that depend on
        others come after their dependencies. Circular dependencies are
        broken by keeping the original order.
        """
        action_map = {a.action_id: a for a in self.actions}
        visited: set[str] = set()
        result: list[PlanAction] = []

        def _visit(action_id: str) -> None:
            if action_id in visited:
                return
            visited.add(action_id)
            action = action_map.get(action_id)
            if action is None:
                return
            for dep_id in action.depends_on:
                if dep_id not in visited:
                    _visit(dep_id)
            result.append(action)

        for action in self.actions:
            _visit(action.action_id)

        return result


# --- Planner ---


_action_counter = 0


def _next_action_id() -> str:
    global _action_counter
    _action_counter += 1
    return f"action-{_action_counter:03d}"


def _reset_counter() -> None:
    global _action_counter
    _action_counter = 0


def plan(optimized: OptimizedResult) -> ExecutionPlan:
    """Transform an OptimizedResult into an ordered ExecutionPlan.

    This is the main entry point — the decoder that maps compressed
    intelligence into intended actions.
    """
    _reset_counter()
    ep = ExecutionPlan(
        source_finding_count=optimized.finding_count,
        source_fsm_count=optimized.fsm_count,
    )

    # Phase 1: Generate models from extracted FSMs (highest value)
    _plan_fsm_models(optimized, ep)

    # Phase 2: Generate error handlers from error_pattern findings
    _plan_error_handlers(optimized, ep)

    # Phase 3: Generate API clients from protocol findings + endpoint catalog
    _plan_api_clients(optimized, ep)

    # Phase 4: Exploration directives from coverage gaps
    _plan_exploration(optimized, ep)

    # Phase 5: Validation directives for low-confidence findings
    _plan_validation(optimized, ep)

    return ep


def _plan_fsm_models(optimized: OptimizedResult, ep: ExecutionPlan) -> None:
    """Create GENERATE_MODEL actions for each extracted FSM.

    Each FSM becomes a typed Python dataclass with state enum,
    transition validator, and current-state tracker.
    """
    for fsm in optimized.fsms:
        if fsm.state_count == 0:
            continue

        states_desc = ", ".join(s.name for s in fsm.states[:10])
        initial = [s.name for s in fsm.states if s.is_initial]
        terminal = [s.name for s in fsm.states if s.is_terminal]
        error = [s.name for s in fsm.states if s.is_error]

        action = PlanAction(
            action_id=_next_action_id(),
            kind=ActionKind.GENERATE_MODEL,
            priority=ActionPriority.HIGH,
            title=f"Generate typed FSM model: {fsm.name[:60]}",
            description=(
                f"Generate a Python dataclass representing the '{fsm.name}' state machine. "
                f"States ({fsm.state_count}): {states_desc}. "
                f"Transitions: {fsm.transition_count}. "
                f"Initial states: {initial or ['unspecified']}. "
                f"Terminal states: {terminal or ['none']}. "
                f"Error states: {error or ['none']}. "
                f"The model should include: state enum, transition validator "
                f"(raise on invalid transitions), and current-state property."
            ),
            source_fsm_name=fsm.name,
            outputs=[
                f"dataclass: {_fsm_class_name(fsm.name)}",
                f"enum: {_fsm_class_name(fsm.name)}State",
                f"validator: {_fsm_class_name(fsm.name)}.validate_transition(from, to)",
            ],
            metadata={
                "state_count": fsm.state_count,
                "transition_count": fsm.transition_count,
                "states": [s.name for s in fsm.states],
                "has_error_states": bool(error),
            },
        )
        ep.actions.append(action)


def _plan_error_handlers(optimized: OptimizedResult, ep: ExecutionPlan) -> None:
    """Create GENERATE_HANDLER actions for error_pattern findings.

    Groups related error patterns into a single handler that routes
    error_code → recovery action.
    """
    error_findings = [
        mf for mf in optimized.merged_findings
        if mf.category == FindingCategory.ERROR_PATTERN
    ]

    if not error_findings:
        return

    # Group error findings into a single handler action
    # (one handler per system, not per finding)
    error_codes = []
    source_ids = []
    for mf in error_findings:
        error_codes.append(mf.canonical_title[:80])
        source_ids.extend(mf.source_finding_ids)

    action = PlanAction(
        action_id=_next_action_id(),
        kind=ActionKind.GENERATE_HANDLER,
        priority=ActionPriority.HIGH,
        title="Generate error handler with recovery routing",
        description=(
            f"Generate an error handler that maps {len(error_findings)} error patterns "
            f"to recovery actions. Patterns: {'; '.join(error_codes[:5])}. "
            f"The handler should: (1) parse error_type + error_code from response, "
            f"(2) classify as recoverable vs terminal (using suggested_action field), "
            f"(3) route to appropriate recovery: retry, re-auth, wait, or escalate."
        ),
        source_finding_id=source_ids[0] if source_ids else None,
        outputs=[
            "function: handle_error(response) -> RecoveryAction",
            "enum: RecoveryAction (retry, reauth, wait, escalate, abort)",
        ],
        metadata={
            "error_pattern_count": len(error_findings),
            "source_finding_count": len(source_ids),
        },
    )
    ep.actions.append(action)


def _plan_api_clients(optimized: OptimizedResult, ep: ExecutionPlan) -> None:
    """Create GENERATE_CLIENT actions from protocol findings + endpoint catalog.

    Each protocol finding that has matching endpoints in the catalog
    produces a typed client generation action.
    """
    protocol_findings = [
        mf for mf in optimized.merged_findings
        if mf.category == FindingCategory.PROTOCOL
    ]

    if not protocol_findings:
        return

    # Find endpoints with response schemas — these are concrete enough for clients
    typed_endpoints = {
        key: ep_rec for key, ep_rec in optimized.merged_endpoints.items()
        if ep_rec.response_schema is not None
    }

    if typed_endpoints:
        endpoint_names = list(typed_endpoints.keys())[:10]
        action = PlanAction(
            action_id=_next_action_id(),
            kind=ActionKind.GENERATE_CLIENT,
            priority=ActionPriority.MEDIUM,
            title="Generate typed API client from endpoint catalog",
            description=(
                f"Generate typed request/response models for {len(typed_endpoints)} endpoints "
                f"with known schemas: {', '.join(endpoint_names[:5])}. "
                f"Use response_schema for return type, request body for input type. "
                f"Include rate limit annotations from protocol findings."
            ),
            source_finding_id=protocol_findings[0].source_finding_ids[0] if protocol_findings[0].source_finding_ids else None,
            outputs=[
                f"client method for: {name}" for name in endpoint_names[:5]
            ],
            metadata={
                "endpoint_count": len(typed_endpoints),
                "endpoints": endpoint_names,
            },
        )
        ep.actions.append(action)


def _plan_exploration(optimized: OptimizedResult, ep: ExecutionPlan) -> None:
    """Create EXPLORE actions from coverage gaps.

    Compares what's been seen (span_kinds_seen) against the full set
    of span kinds, and generates exploration directives for gaps.
    """
    all_span_kinds = set(SpanKind)
    seen = optimized.coverage.span_kinds_seen
    missing = all_span_kinds - seen

    for kind in sorted(missing, key=lambda k: k.value):
        gap_desc = f"span kind '{kind.value}' has not been explored"
        ep.coverage_gaps.append(gap_desc)

        action = PlanAction(
            action_id=_next_action_id(),
            kind=ActionKind.EXPLORE,
            priority=ActionPriority.MEDIUM,
            title=f"Explore missing span kind: {kind.value}",
            description=(
                f"No traces cover the '{kind.value}' span kind. "
                f"Use manifest.next() to find the highest-priority unexplored target "
                f"for this span kind and produce a trace."
            ),
            outputs=[f"trace covering {kind.value} span kind"],
            metadata={"missing_span_kind": kind.value},
        )
        ep.actions.append(action)

    # Also flag open questions as exploration opportunities
    if optimized.coverage.open_questions:
        q_count = len(optimized.coverage.open_questions)
        top_questions = optimized.coverage.open_questions[:5]
        action = PlanAction(
            action_id=_next_action_id(),
            kind=ActionKind.EXPLORE,
            priority=ActionPriority.LOW,
            title=f"Investigate {q_count} open questions from traces",
            description=(
                f"Traces raised {q_count} unanswered questions. Top questions: "
                + "; ".join(top_questions)
            ),
            outputs=["answers or new traces addressing open questions"],
            metadata={
                "question_count": q_count,
                "top_questions": top_questions,
            },
        )
        ep.actions.append(action)


def _plan_validation(optimized: OptimizedResult, ep: ExecutionPlan) -> None:
    """Create VALIDATE actions for low-confidence or hypothesis findings.

    Findings that haven't been confirmed across multiple traces get
    validation directives — re-explore with different approach.
    """
    low_conf = [
        mf for mf in optimized.merged_findings
        if mf.confidence == FindingConfidence.HYPOTHESIS
        and len(mf.source_trace_ids) < 2
    ]

    for mf in low_conf:
        action = PlanAction(
            action_id=_next_action_id(),
            kind=ActionKind.VALIDATE,
            priority=ActionPriority.LOW,
            title=f"Validate hypothesis: {mf.canonical_title[:60]}",
            description=(
                f"Finding '{mf.canonical_title}' is a hypothesis from a single trace. "
                f"Re-explore using a different approach (different credentials, "
                f"different endpoint, or live API call instead of doc read) to confirm or refute."
            ),
            source_finding_id=mf.source_finding_ids[0] if mf.source_finding_ids else None,
            outputs=["confirmed or refuted finding"],
            metadata={
                "current_confidence": mf.confidence.value,
                "evidence_count": mf.evidence_count,
            },
        )
        ep.actions.append(action)


# --- Utilities ---


def _fsm_class_name(fsm_name: str) -> str:
    """Convert an FSM name into a PascalCase class name.

    'Transaction sync has a 3-state FSM...' -> 'TransactionSync'
    'Item lifecycle FSM...' -> 'ItemLifecycle'
    """
    # Take first few significant words, skip noise
    noise = {"has", "a", "the", "is", "an", "with", "from", "for", "and", "or", "of", "in", "to", "vs"}
    words = fsm_name.split()
    significant = []
    for w in words:
        clean = w.strip("—:,.()")
        if clean.lower() in noise or len(clean) < 2:
            continue
        # Skip tokens that start with a digit (e.g., "3-state")
        if clean[0].isdigit():
            continue
        # Stop at FSM/state/machine keywords — they're meta, not name
        if clean.lower() in {"fsm", "state", "machine", "states", "transition", "transitions"}:
            break
        significant.append(clean.capitalize())
        if len(significant) >= 3:
            break

    return "".join(significant) if significant else "UnnamedFSM"
