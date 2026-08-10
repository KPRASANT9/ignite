"""IGNITE Accuracy Metric — Compares planned outcomes vs actual results.

Measures how well the Planner's predicted actions match execution reality.
This is the calibration signal for the closed-loop pipeline.

Metrics:
- Action success rate: what fraction of planned actions succeeded
- Syntax validity: did generated code parse without errors
- Coverage delta: did exploration actions increase span kind coverage
- Confidence delta: did validation actions change finding confidence
"""

from __future__ import annotations

import ast
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any

from ignite_parser.executor import ActionResult, ActionStatus, ExecutionResult
from ignite_parser.planner import ActionKind, ExecutionPlan


@dataclass
class ActionAccuracy:
    """Accuracy assessment for a single action."""
    action_id: str
    kind: ActionKind
    planned_title: str
    status: ActionStatus
    syntax_valid: bool | None = None
    file_exists: bool | None = None
    score: float = 0.0  # 0.0 = complete failure, 1.0 = perfect execution
    notes: list[str] = field(default_factory=list)


@dataclass
class AccuracyReport:
    """Aggregate accuracy metrics for a plan execution."""
    action_accuracies: list[ActionAccuracy] = field(default_factory=list)
    overall_score: float = 0.0
    success_rate: float = 0.0
    syntax_validity_rate: float = 0.0
    by_kind: dict[str, float] = field(default_factory=dict)

    @property
    def total_actions(self) -> int:
        return len(self.action_accuracies)

    def summary(self) -> dict[str, Any]:
        return {
            "overall_score": round(self.overall_score, 3),
            "success_rate": round(self.success_rate, 3),
            "syntax_validity_rate": round(self.syntax_validity_rate, 3),
            "total_actions": self.total_actions,
            "by_kind": {k: round(v, 3) for k, v in self.by_kind.items()},
        }


def measure_accuracy(
    plan: ExecutionPlan,
    execution: ExecutionResult,
) -> AccuracyReport:
    """Compare planned actions vs execution results and compute accuracy.

    Each action is scored based on:
    - Did it execute? (0.0 for skipped/failed, 0.5 for partial, 1.0 for success)
    - For code generation: is the output syntactically valid?
    - For exploration: was a directive produced?
    """
    report = AccuracyReport()

    # Build result lookup
    result_map = {ar.action_id: ar for ar in execution.results}

    kind_scores: dict[str, list[float]] = {}
    syntax_checks = 0
    syntax_valid = 0

    for action in plan.actions:
        ar = result_map.get(action.action_id)
        if ar is None:
            aa = ActionAccuracy(
                action_id=action.action_id,
                kind=action.kind,
                planned_title=action.title,
                status=ActionStatus.SKIPPED,
                score=0.0,
                notes=["No execution result found"],
            )
            report.action_accuracies.append(aa)
            kind_scores.setdefault(action.kind.value, []).append(0.0)
            continue

        score = _score_action(ar)
        syn_valid = ar.metrics.get("syntax_valid")

        if syn_valid is not None:
            syntax_checks += 1
            if syn_valid:
                syntax_valid += 1

        # Check if generated files exist
        file_exists = None
        if ar.generated_files:
            file_exists = all(Path(f).exists() for f in ar.generated_files)
            if not file_exists:
                score = max(score - 0.2, 0.0)

        aa = ActionAccuracy(
            action_id=action.action_id,
            kind=action.kind,
            planned_title=action.title,
            status=ar.status,
            syntax_valid=syn_valid,
            file_exists=file_exists,
            score=score,
            notes=_build_notes(ar),
        )
        report.action_accuracies.append(aa)
        kind_scores.setdefault(action.kind.value, []).append(score)

    # Compute aggregates
    all_scores = [aa.score for aa in report.action_accuracies]
    report.overall_score = sum(all_scores) / len(all_scores) if all_scores else 0.0
    report.success_rate = execution.success_rate
    report.syntax_validity_rate = syntax_valid / syntax_checks if syntax_checks > 0 else 1.0

    for kind_name, scores in kind_scores.items():
        report.by_kind[kind_name] = sum(scores) / len(scores) if scores else 0.0

    return report


def _score_action(ar: ActionResult) -> float:
    """Score a single action result."""
    if ar.status == ActionStatus.SUCCESS:
        base = 1.0
        # Bonus/penalty based on syntax validity
        if ar.metrics.get("syntax_valid") is False:
            base -= 0.3
        return base
    elif ar.status == ActionStatus.PARTIAL:
        return 0.5
    elif ar.status == ActionStatus.SKIPPED:
        return 0.0
    else:
        return 0.0


def _build_notes(ar: ActionResult) -> list[str]:
    """Build human-readable notes about the action result."""
    notes = []
    if ar.status == ActionStatus.SUCCESS:
        if ar.generated_files:
            notes.append(f"Generated {len(ar.generated_files)} file(s)")
        if ar.metrics.get("syntax_valid"):
            notes.append("Syntax validated")
    elif ar.status == ActionStatus.FAILED:
        notes.append(f"Failed: {ar.error}")
    elif ar.status == ActionStatus.SKIPPED:
        notes.append(f"Skipped: {ar.error}")
    return notes
