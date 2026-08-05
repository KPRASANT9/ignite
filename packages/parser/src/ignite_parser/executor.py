"""IGNITE L2 Executor — Sixth stage of the pipeline (the operator).

Consumes an ExecutionPlan and dispatches each PlanAction to its handler.
Returns structured ActionResult objects that feed into the Feedback Writer.

Pipeline: Parser -> Analyzer -> Optimizer -> Planner -> **Executor** -> Feedback
"""

from __future__ import annotations

import ast
import textwrap
from dataclasses import dataclass, field
from enum import Enum
from pathlib import Path
from typing import Any

from ignite_parser.optimizer import ExtractedFSM, OptimizedResult
from ignite_parser.planner import ActionKind, ExecutionPlan, PlanAction


# --- Result types ---


class ActionStatus(str, Enum):
    """Outcome of executing a plan action."""
    SUCCESS = "success"
    PARTIAL = "partial"
    FAILED = "failed"
    SKIPPED = "skipped"


@dataclass
class ActionResult:
    """Result of executing a single PlanAction."""
    action_id: str
    kind: ActionKind
    status: ActionStatus
    title: str
    outputs: list[str] = field(default_factory=list)
    generated_files: list[str] = field(default_factory=list)
    error: str | None = None
    metrics: dict[str, Any] = field(default_factory=dict)


@dataclass
class ExecutionResult:
    """Aggregate result of executing an entire plan."""
    results: list[ActionResult] = field(default_factory=list)
    total: int = 0
    succeeded: int = 0
    failed: int = 0
    skipped: int = 0

    @property
    def success_rate(self) -> float:
        return self.succeeded / self.total if self.total > 0 else 0.0

    def by_status(self, status: ActionStatus) -> list[ActionResult]:
        return [r for r in self.results if r.status == status]


# --- Handlers ---


def _handle_generate_model(action: PlanAction, optimized: OptimizedResult, output_dir: Path) -> ActionResult:
    """Generate a typed FSM dataclass from an extracted state machine.

    Produces a Python file with:
    - State enum with all states
    - Dataclass with transition validation
    - Current-state tracking
    """
    fsm_name = action.source_fsm_name
    if not fsm_name:
        return ActionResult(
            action_id=action.action_id,
            kind=action.kind,
            status=ActionStatus.FAILED,
            title=action.title,
            error="No source_fsm_name in action metadata",
        )

    # Find the FSM in optimized data
    fsm = next((f for f in optimized.fsms if f.name == fsm_name), None)
    if fsm is None:
        return ActionResult(
            action_id=action.action_id,
            kind=action.kind,
            status=ActionStatus.FAILED,
            title=action.title,
            error=f"FSM '{fsm_name}' not found in optimized data",
        )

    class_name = _fsm_class_name(fsm_name)
    code = _generate_fsm_code(fsm, class_name)

    # Write the generated file
    filename = f"{_to_snake_case(class_name)}_fsm.py"
    models_dir = output_dir / "models"
    models_dir.mkdir(parents=True, exist_ok=True)
    filepath = models_dir / filename
    filepath.write_text(code, encoding="utf-8")

    # Validate syntax
    try:
        ast.parse(code)
        syntax_valid = True
    except SyntaxError as e:
        syntax_valid = False
        return ActionResult(
            action_id=action.action_id,
            kind=action.kind,
            status=ActionStatus.PARTIAL,
            title=action.title,
            outputs=[f"Generated {filepath} but has syntax error: {e}"],
            generated_files=[str(filepath)],
            error=f"Syntax error in generated code: {e}",
            metrics={"syntax_valid": False},
        )

    return ActionResult(
        action_id=action.action_id,
        kind=action.kind,
        status=ActionStatus.SUCCESS,
        title=action.title,
        outputs=[f"Generated typed FSM model: {class_name}"],
        generated_files=[str(filepath)],
        metrics={
            "syntax_valid": True,
            "state_count": fsm.state_count,
            "transition_count": fsm.transition_count,
            "class_name": class_name,
        },
    )


def _handle_generate_handler(action: PlanAction, optimized: OptimizedResult, output_dir: Path) -> ActionResult:
    """Generate an error handler with recovery routing.

    Produces a Python file with:
    - RecoveryAction enum (retry, reauth, wait, escalate, abort)
    - handle_error() function mapping error patterns to recovery actions
    """
    from ignite_parser.models import FindingCategory

    error_findings = [
        mf for mf in optimized.merged_findings
        if mf.category == FindingCategory.ERROR_PATTERN
    ]

    if not error_findings:
        return ActionResult(
            action_id=action.action_id,
            kind=action.kind,
            status=ActionStatus.SKIPPED,
            title=action.title,
            error="No error_pattern findings to generate handler from",
        )

    code = _generate_error_handler_code(error_findings)

    handlers_dir = output_dir / "handlers"
    handlers_dir.mkdir(parents=True, exist_ok=True)
    filepath = handlers_dir / "error_handler.py"
    filepath.write_text(code, encoding="utf-8")

    try:
        ast.parse(code)
    except SyntaxError as e:
        return ActionResult(
            action_id=action.action_id,
            kind=action.kind,
            status=ActionStatus.PARTIAL,
            title=action.title,
            generated_files=[str(filepath)],
            error=f"Syntax error: {e}",
            metrics={"syntax_valid": False},
        )

    return ActionResult(
        action_id=action.action_id,
        kind=action.kind,
        status=ActionStatus.SUCCESS,
        title=action.title,
        outputs=[f"Generated error handler for {len(error_findings)} error patterns"],
        generated_files=[str(filepath)],
        metrics={
            "syntax_valid": True,
            "error_pattern_count": len(error_findings),
        },
    )


def _handle_generate_client(action: PlanAction, optimized: OptimizedResult, output_dir: Path) -> ActionResult:
    """Generate typed API client methods from endpoint catalog.

    Produces a Python file with typed request/response for each endpoint
    that has known schemas.
    """
    typed_endpoints = {
        key: ep for key, ep in optimized.merged_endpoints.items()
        if ep.response_schema is not None
    }

    if not typed_endpoints:
        return ActionResult(
            action_id=action.action_id,
            kind=action.kind,
            status=ActionStatus.SKIPPED,
            title=action.title,
            error="No endpoints with response schemas to generate client from",
        )

    code = _generate_client_code(typed_endpoints)

    clients_dir = output_dir / "clients"
    clients_dir.mkdir(parents=True, exist_ok=True)
    filepath = clients_dir / "api_client.py"
    filepath.write_text(code, encoding="utf-8")

    try:
        ast.parse(code)
    except SyntaxError as e:
        return ActionResult(
            action_id=action.action_id,
            kind=action.kind,
            status=ActionStatus.PARTIAL,
            title=action.title,
            generated_files=[str(filepath)],
            error=f"Syntax error: {e}",
            metrics={"syntax_valid": False},
        )

    return ActionResult(
        action_id=action.action_id,
        kind=action.kind,
        status=ActionStatus.SUCCESS,
        title=action.title,
        outputs=[f"Generated typed client for {len(typed_endpoints)} endpoints"],
        generated_files=[str(filepath)],
        metrics={
            "syntax_valid": True,
            "endpoint_count": len(typed_endpoints),
        },
    )


def _handle_explore(action: PlanAction, optimized: OptimizedResult, output_dir: Path) -> ActionResult:
    """Generate an exploration directive for coverage gaps.

    Does not execute the exploration — produces a structured directive
    that an exploration agent can consume.
    """
    missing_kind = action.metadata.get("missing_span_kind")
    questions = action.metadata.get("top_questions", [])

    directive = {
        "action_id": action.action_id,
        "directive": "explore",
        "target_span_kind": missing_kind,
        "questions": questions,
        "description": action.description,
    }

    directives_dir = output_dir / "directives"
    directives_dir.mkdir(parents=True, exist_ok=True)
    filepath = directives_dir / f"explore_{missing_kind or 'questions'}.json"

    import json
    filepath.write_text(json.dumps(directive, indent=2), encoding="utf-8")

    return ActionResult(
        action_id=action.action_id,
        kind=action.kind,
        status=ActionStatus.SUCCESS,
        title=action.title,
        outputs=[f"Exploration directive: {missing_kind or 'open questions'}"],
        generated_files=[str(filepath)],
        metrics={"target_span_kind": missing_kind},
    )


def _handle_validate(action: PlanAction, optimized: OptimizedResult, output_dir: Path) -> ActionResult:
    """Generate a validation directive for low-confidence findings.

    Does not execute validation — produces a structured directive
    for re-exploration with a different approach.
    """
    directive = {
        "action_id": action.action_id,
        "directive": "validate",
        "source_finding_id": action.source_finding_id,
        "description": action.description,
        "current_confidence": action.metadata.get("current_confidence"),
        "evidence_count": action.metadata.get("evidence_count"),
    }

    directives_dir = output_dir / "directives"
    directives_dir.mkdir(parents=True, exist_ok=True)
    filepath = directives_dir / f"validate_{action.action_id}.json"

    import json
    filepath.write_text(json.dumps(directive, indent=2), encoding="utf-8")

    return ActionResult(
        action_id=action.action_id,
        kind=action.kind,
        status=ActionStatus.SUCCESS,
        title=action.title,
        outputs=[f"Validation directive for: {action.title}"],
        generated_files=[str(filepath)],
        metrics={
            "source_finding_id": action.source_finding_id,
            "current_confidence": action.metadata.get("current_confidence"),
        },
    )


# --- Main executor ---


_HANDLERS = {
    ActionKind.GENERATE_MODEL: _handle_generate_model,
    ActionKind.GENERATE_HANDLER: _handle_generate_handler,
    ActionKind.GENERATE_CLIENT: _handle_generate_client,
    ActionKind.EXPLORE: _handle_explore,
    ActionKind.VALIDATE: _handle_validate,
}


def execute(
    plan: ExecutionPlan,
    optimized: OptimizedResult,
    output_dir: str | Path = ".",
) -> ExecutionResult:
    """Execute all actions in an ExecutionPlan, respecting topological order.

    Each action is dispatched to its handler. Results are collected
    into an ExecutionResult for the Feedback Writer.
    """
    output_path = Path(output_dir)
    output_path.mkdir(parents=True, exist_ok=True)

    result = ExecutionResult(total=plan.action_count)
    completed_ids: set[str] = set()

    for action in plan.topological_order():
        # Check dependencies
        unmet = [dep for dep in action.depends_on if dep not in completed_ids]
        if unmet:
            ar = ActionResult(
                action_id=action.action_id,
                kind=action.kind,
                status=ActionStatus.SKIPPED,
                title=action.title,
                error=f"Unmet dependencies: {unmet}",
            )
            result.results.append(ar)
            result.skipped += 1
            continue

        handler = _HANDLERS.get(action.kind)
        if handler is None:
            ar = ActionResult(
                action_id=action.action_id,
                kind=action.kind,
                status=ActionStatus.FAILED,
                title=action.title,
                error=f"No handler for action kind: {action.kind}",
            )
            result.results.append(ar)
            result.failed += 1
            continue

        try:
            ar = handler(action, optimized, output_path)
        except Exception as e:
            ar = ActionResult(
                action_id=action.action_id,
                kind=action.kind,
                status=ActionStatus.FAILED,
                title=action.title,
                error=str(e),
            )

        result.results.append(ar)
        if ar.status == ActionStatus.SUCCESS:
            result.succeeded += 1
            completed_ids.add(action.action_id)
        elif ar.status == ActionStatus.PARTIAL:
            result.succeeded += 1  # partial still counts as progress
            completed_ids.add(action.action_id)
        elif ar.status == ActionStatus.FAILED:
            result.failed += 1
        else:
            result.skipped += 1

    return result


# --- Code generation helpers ---


def _fsm_class_name(fsm_name: str) -> str:
    """Convert FSM name to PascalCase class name."""
    from ignite_parser.planner import _fsm_class_name as planner_fsm_name
    return planner_fsm_name(fsm_name)


def _to_snake_case(name: str) -> str:
    """Convert PascalCase to snake_case."""
    result = []
    for i, c in enumerate(name):
        if c.isupper() and i > 0:
            result.append("_")
        result.append(c.lower())
    return "".join(result)


def _sanitize_identifier(name: str) -> str:
    """Convert a state name into a valid Python identifier."""
    cleaned = name.upper().replace(" ", "_").replace("-", "_").replace("/", "_")
    cleaned = "".join(c if c.isalnum() or c == "_" else "" for c in cleaned)
    if cleaned and cleaned[0].isdigit():
        cleaned = f"STATE_{cleaned}"
    return cleaned or "UNKNOWN"


def _generate_fsm_code(fsm: ExtractedFSM, class_name: str) -> str:
    """Generate Python source for a typed FSM model."""
    state_names = [_sanitize_identifier(s.name) for s in fsm.states]
    if not state_names:
        state_names = ["INITIAL"]

    # Build state enum members
    enum_members = "\n".join(f'    {name} = "{name.lower()}"' for name in state_names)

    # Build transition map
    transitions = []
    for t in fsm.transitions:
        from_s = _sanitize_identifier(t.from_state)
        to_s = _sanitize_identifier(t.to_state)
        trigger = t.trigger or "auto"
        transitions.append(f'    ({class_name}State.{from_s}, {class_name}State.{to_s}): "{trigger}",')

    transition_map = "\n".join(transitions) if transitions else "    # No transitions defined"

    # Determine initial state
    initial_states = [_sanitize_identifier(s.name) for s in fsm.states if s.is_initial]
    initial = initial_states[0] if initial_states else state_names[0]

    # Determine terminal + error states
    terminal_states = [_sanitize_identifier(s.name) for s in fsm.states if s.is_terminal]
    error_states = [_sanitize_identifier(s.name) for s in fsm.states if s.is_error]

    terminal_set = ", ".join(f"{class_name}State.{s}" for s in terminal_states) if terminal_states else ""
    error_set = ", ".join(f"{class_name}State.{s}" for s in error_states) if error_states else ""

    lines = [
        f'"""Generated FSM model: {class_name}',
        f'',
        f'Source: {fsm.name}',
        f'System: {fsm.system}',
        f'States: {fsm.state_count} | Transitions: {fsm.transition_count}',
        f'"""',
        f'',
        f'from __future__ import annotations',
        f'',
        f'from dataclasses import dataclass, field',
        f'from enum import Enum',
        f'',
        f'',
        f'class {class_name}State(str, Enum):',
        f'    """States in the {class_name} state machine."""',
        enum_members,
        f'',
        f'',
        f'# Valid transitions: (from_state, to_state) -> trigger',
        f'_TRANSITIONS: dict[tuple[{class_name}State, {class_name}State], str] = {{',
        transition_map,
        f'}}',
        f'',
        f'_TERMINAL_STATES: set[{class_name}State] = {{{terminal_set}}}',
        f'_ERROR_STATES: set[{class_name}State] = {{{error_set}}}',
        f'',
        f'',
        f'@dataclass',
        f'class {class_name}:',
        f'    """Typed FSM model with transition validation."""',
        f'',
        f'    current_state: {class_name}State = {class_name}State.{initial}',
        f'    history: list[{class_name}State] = field(default_factory=list)',
        f'',
        f'    @property',
        f'    def is_terminal(self) -> bool:',
        f'        return self.current_state in _TERMINAL_STATES',
        f'',
        f'    @property',
        f'    def is_error(self) -> bool:',
        f'        return self.current_state in _ERROR_STATES',
        f'',
        f'    def validate_transition(self, to_state: {class_name}State) -> bool:',
        f'        """Check if a transition from current state to to_state is valid."""',
        f'        return (self.current_state, to_state) in _TRANSITIONS',
        f'',
        f'    def transition(self, to_state: {class_name}State) -> "{class_name}":',
        f'        """Transition to a new state. Raises ValueError on invalid transition."""',
        f'        if not self.validate_transition(to_state):',
        f'            raise ValueError(',
        f'                f"Invalid transition: {{self.current_state.value}} -> {{to_state.value}}"',
        f'            )',
        f'        self.history.append(self.current_state)',
        f'        self.current_state = to_state',
        f'        return self',
        f'',
        f'    def valid_transitions(self) -> "list[{class_name}State]":',
        f'        """Return all valid next states from current state."""',
        f'        return [',
        f'            to_state for (from_state, to_state) in _TRANSITIONS',
        f'            if from_state == self.current_state',
        f'        ]',
    ]
    return "\n".join(lines) + "\n"


def _generate_error_handler_code(error_findings: list) -> str:
    """Generate Python source for an error handler with recovery routing."""
    # Build error pattern entries from findings
    patterns = []
    for i, mf in enumerate(error_findings):
        pattern_name = _sanitize_identifier(mf.canonical_title[:40])
        patterns.append(f'    "{pattern_name}": RecoveryAction.RETRY,  # {mf.canonical_title[:60]}')

    pattern_entries = "\n".join(patterns) if patterns else '    # No patterns defined'

    lines = [
        f'"""Generated error handler with recovery routing.',
        f'',
        f'Source: {len(error_findings)} error_pattern findings from Optimizer',
        f'"""',
        f'',
        f'from __future__ import annotations',
        f'',
        f'from dataclasses import dataclass',
        f'from enum import Enum',
        f'from typing import Any',
        f'',
        f'',
        f'class RecoveryAction(str, Enum):',
        f'    """Recovery strategy for a given error."""',
        f'    RETRY = "retry"',
        f'    REAUTH = "reauth"',
        f'    WAIT = "wait"',
        f'    ESCALATE = "escalate"',
        f'    ABORT = "abort"',
        f'',
        f'',
        f'@dataclass',
        f'class ErrorClassification:',
        f'    """Classified error with recovery recommendation."""',
        f'    error_type: str',
        f'    error_code: str | None',
        f'    message: str',
        f'    recovery: RecoveryAction',
        f'    is_recoverable: bool',
        f'',
        f'',
        f'# Known error patterns -> recovery actions',
        f'_ERROR_PATTERNS: dict[str, RecoveryAction] = {{',
        pattern_entries,
        f'}}',
        f'',
        f'',
        f'def classify_error(error_type: str, error_code: str | None = None,',
        f'                  message: str = "") -> ErrorClassification:',
        f'    """Classify an error and recommend recovery action."""',
        f'    key = error_type.upper().replace(" ", "_")',
        f'',
        f'    if key in _ERROR_PATTERNS:',
        f'        recovery = _ERROR_PATTERNS[key]',
        f'    elif error_code and "INVALID" in (error_code or "").upper():',
        f'        recovery = RecoveryAction.ABORT',
        f'    elif "token" in message.lower() or "auth" in message.lower():',
        f'        recovery = RecoveryAction.REAUTH',
        f'    elif "rate" in message.lower() or "limit" in message.lower():',
        f'        recovery = RecoveryAction.WAIT',
        f'    else:',
        f'        recovery = RecoveryAction.ESCALATE',
        f'',
        f'    is_recoverable = recovery in (RecoveryAction.RETRY, RecoveryAction.REAUTH, RecoveryAction.WAIT)',
        f'',
        f'    return ErrorClassification(',
        f'        error_type=error_type,',
        f'        error_code=error_code,',
        f'        message=message,',
        f'        recovery=recovery,',
        f'        is_recoverable=is_recoverable,',
        f'    )',
        f'',
        f'',
        f'def handle_error(response: dict[str, Any]) -> RecoveryAction:',
        f'    """Extract error info from response and return recovery action."""',
        f'    error_type = response.get("error_type", "UNKNOWN")',
        f'    error_code = response.get("error_code")',
        f'    message = response.get("error_message", "")',
        f'',
        f'    classification = classify_error(error_type, error_code, message)',
        f'    return classification.recovery',
    ]
    return "\n".join(lines) + "\n"


def _generate_client_code(typed_endpoints: dict) -> str:
    """Generate Python source for typed API client methods."""
    method_lines = []
    for key, ep in typed_endpoints.items():
        method_name = _to_snake_case(
            key.replace("/", "_").replace("-", "_").strip("_")
        )
        method_name = "".join(c if c.isalnum() or c == "_" else "" for c in method_name)
        if not method_name or method_name[0].isdigit():
            method_name = f"call_{method_name}"

        method_str = ep.method or "POST"
        url_str = ep.url or key
        status_codes = ep.status_codes or "unknown"

        method_lines.extend([
            f'',
            f'    def {method_name}(self, **kwargs) -> dict:',
            f'        """Call {key}',
            f'',
            f'        Status codes seen: {status_codes}',
            f'        Hit count: {ep.hit_count}',
            f'        """',
            f'        return self._call("{method_str}", "{url_str}", **kwargs)',
        ])

    if not method_lines:
        method_lines = [f'', f'    pass']

    lines = [
        f'"""Generated typed API client.',
        f'',
        f'Source: {len(typed_endpoints)} endpoints with known schemas from Optimizer',
        f'"""',
        f'',
        f'from __future__ import annotations',
        f'',
        f'from typing import Any',
        f'',
        f'',
        f'class GeneratedClient:',
        f'    """Typed API client generated from endpoint catalog."""',
        f'',
        f'    def __init__(self, base_url: str = "", headers: dict[str, str] | None = None):',
        f'        self.base_url = base_url',
        f'        self.headers = headers or {{}}',
        f'',
        f'    def _call(self, method: str, path: str, **kwargs: Any) -> dict:',
        f'        """Override in subclass to implement actual HTTP calls."""',
        f'        raise NotImplementedError("Subclass must implement _call()")',
    ] + method_lines
    return "\n".join(lines) + "\n"
