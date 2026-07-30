"""IGNITE Ops Agent Module — Invokes the pipeline loop and produces structured reports.

This is the "cursor controller" — a thin orchestrator that:
1. Runs the closed-loop pipeline via orchestrator.run_loop()
2. Measures accuracy of the execution
3. Computes coverage deltas
4. Detects escalation conditions
5. Produces a structured OpsReport with to_markdown() and to_json() rendering

Pipeline: Ops Agent -> Orchestrator -> (Parser -> Analyzer -> Optimizer -> Planner -> Executor -> Feedback)*

The Buzz agent invokes `ignite ops` CLI, gets the report, and posts it.
"""

from __future__ import annotations

import json
import uuid
from dataclasses import dataclass, field
from datetime import datetime, timezone
from pathlib import Path
from typing import Any

from ignite_parser.accuracy import AccuracyReport, measure_accuracy
from ignite_parser.executor import ActionResult, ActionStatus, ExecutionResult
from ignite_parser.models import SpanKind
from ignite_parser.orchestrator import LoopResult, run_loop
from ignite_parser.planner import ActionKind, ExecutionPlan


# --- Escalation thresholds (tunable) ---

ESCALATION_THRESHOLDS = {
    "accuracy_warning": 0.8,       # Overall score below this -> warning
    "accuracy_critical": 0.5,      # Overall score below this -> critical
    "accuracy_drop_warning": 0.10, # Score drop >10% from previous -> warning
}


# --- Data types ---


@dataclass
class Escalation:
    """A condition requiring attention."""
    kind: str       # 'unhandled_action' | 'accuracy_drop' | 'validation_failure' | 'coverage_regression'
    severity: str   # 'warning' | 'critical'
    message: str    # Human-readable description
    context: dict[str, Any] = field(default_factory=dict)


@dataclass
class OpsReport:
    """Complete report from an ops run — the contract between pipeline and Buzz agent."""

    # Identity
    run_id: str = ""
    timestamp: str = ""
    traces_dir: str = ""

    # Loop outcome
    iterations: int = 0
    converged: bool = False

    # Action summary
    actions_planned: int = 0
    actions_executed: int = 0
    actions_succeeded: int = 0
    actions_failed: int = 0
    action_details: list[ActionResult] = field(default_factory=list)

    # Accuracy
    accuracy_score: float = 0.0
    accuracy_by_kind: dict[str, float] = field(default_factory=dict)

    # Coverage
    manifest_coverage_before: float = 0.0
    manifest_coverage_after: float = 0.0
    coverage_delta: float = 0.0
    span_kinds_covered: list[str] = field(default_factory=list)
    span_kinds_missing: list[str] = field(default_factory=list)

    # Escalations
    escalations: list[Escalation] = field(default_factory=list)

    def to_markdown(self) -> str:
        """Render as Buzz-ready markdown report."""
        return _render_markdown(self)

    def to_json(self) -> str:
        """Render as machine-readable JSON."""
        return json.dumps(_to_dict(self), indent=2)

    @classmethod
    def from_json(cls, text: str) -> OpsReport:
        """Deserialize from JSON."""
        data = json.loads(text)
        return _from_dict(data)

    @property
    def exit_code(self) -> int:
        """Compute CLI exit code per EngineeringModeller spec.

        0: converged, no escalations
        1: converged, warning-level escalations only
        2: did not converge within max iterations
        3: critical escalation
        """
        has_critical = any(e.severity == "critical" for e in self.escalations)
        has_warning = any(e.severity == "warning" for e in self.escalations)

        if has_critical:
            return 3
        if not self.converged:
            return 2
        if has_warning:
            return 1
        return 0


# --- Core function ---


def run_ops(
    traces_dir: str | Path,
    output_dir: str | Path = ".",
    max_iterations: int = 5,
    system: str = "ignite",
    previous_accuracy: float | None = None,
) -> OpsReport:
    """Run the full ops loop and produce a structured report.

    This is the main entry point. It:
    1. Snapshots coverage before the run
    2. Runs the orchestrator loop
    3. Measures accuracy on the final iteration
    4. Computes coverage delta
    5. Detects escalation conditions
    6. Returns an OpsReport ready for rendering
    """
    traces_path = Path(traces_dir)
    report = OpsReport(
        run_id=str(uuid.uuid4()),
        timestamp=datetime.now(timezone.utc).isoformat(),
        traces_dir=str(traces_path),
    )

    # Snapshot coverage before run
    coverage_before = _get_coverage(traces_path)

    # Run the loop
    loop_result = run_loop(
        traces_dir=traces_path,
        output_dir=output_dir,
        max_iterations=max_iterations,
        system=system,
    )

    # Populate loop outcome
    report.iterations = loop_result.iteration_count
    report.converged = loop_result.converged

    # Aggregate action results across all iterations
    all_action_results: list[ActionResult] = []
    final_plan: ExecutionPlan | None = None
    final_execution: ExecutionResult | None = None

    for iteration in loop_result.iterations:
        if iteration.execution:
            all_action_results.extend(iteration.execution.results)
            final_execution = iteration.execution
        if iteration.pipeline.plan:
            final_plan = iteration.pipeline.plan

    report.action_details = all_action_results
    report.actions_executed = len(all_action_results)
    report.actions_succeeded = sum(
        1 for ar in all_action_results if ar.status == ActionStatus.SUCCESS
    )
    report.actions_failed = sum(
        1 for ar in all_action_results if ar.status == ActionStatus.FAILED
    )
    if final_plan:
        report.actions_planned = final_plan.action_count

    # Measure accuracy on final iteration
    accuracy_report: AccuracyReport | None = None
    if final_plan and final_execution:
        accuracy_report = measure_accuracy(final_plan, final_execution)
        report.accuracy_score = accuracy_report.overall_score
        report.accuracy_by_kind = dict(accuracy_report.by_kind)

    # Snapshot coverage after run
    coverage_after = _get_coverage(traces_path)

    all_span_kinds = {sk.value for sk in SpanKind}
    covered_before = coverage_before.get("span_kinds_seen", set())
    covered_after = coverage_after.get("span_kinds_seen", set())

    report.span_kinds_covered = sorted(covered_after)
    report.span_kinds_missing = sorted(all_span_kinds - covered_after)

    # Coverage as fraction of span kinds
    total_kinds = len(all_span_kinds)
    report.manifest_coverage_before = len(covered_before) / total_kinds if total_kinds else 0.0
    report.manifest_coverage_after = len(covered_after) / total_kinds if total_kinds else 0.0
    report.coverage_delta = report.manifest_coverage_after - report.manifest_coverage_before

    # Detect escalations
    report.escalations = _detect_escalations(
        loop_result=loop_result,
        accuracy_report=accuracy_report,
        coverage_before=report.manifest_coverage_before,
        coverage_after=report.manifest_coverage_after,
        previous_accuracy=previous_accuracy,
    )

    return report


# --- Coverage helper ---


def _get_coverage(traces_dir: Path) -> dict[str, Any]:
    """Get current coverage snapshot by running a lightweight pipeline pass."""
    from ignite_parser.orchestrator import run_pipeline

    pipeline = run_pipeline(traces_dir)
    result: dict[str, Any] = {"span_kinds_seen": set()}

    if pipeline.optimized and pipeline.optimized.coverage:
        result["span_kinds_seen"] = {
            sk.value for sk in pipeline.optimized.coverage.span_kinds_seen
        }

    return result


# --- Escalation detection ---


def _detect_escalations(
    loop_result: LoopResult,
    accuracy_report: AccuracyReport | None,
    coverage_before: float,
    coverage_after: float,
    previous_accuracy: float | None,
) -> list[Escalation]:
    """Detect the four escalation conditions from the spec."""
    escalations: list[Escalation] = []

    # 1. Unhandled action kind — actions that failed because no handler exists
    for iteration in loop_result.iterations:
        if not iteration.execution:
            continue
        for ar in iteration.execution.results:
            if ar.status == ActionStatus.FAILED and ar.error and "No handler" in ar.error:
                escalations.append(Escalation(
                    kind="unhandled_action",
                    severity="critical",
                    message=f"No handler for action: {ar.title}",
                    context={"action_id": ar.action_id, "kind": ar.kind.value, "error": ar.error},
                ))

    # 2. Accuracy drop
    if accuracy_report:
        score = accuracy_report.overall_score

        if score < ESCALATION_THRESHOLDS["accuracy_critical"]:
            escalations.append(Escalation(
                kind="accuracy_drop",
                severity="critical",
                message=f"Accuracy critically low: {score:.1%} (threshold: {ESCALATION_THRESHOLDS['accuracy_critical']:.0%})",
                context={"score": score, "threshold": ESCALATION_THRESHOLDS["accuracy_critical"]},
            ))
        elif score < ESCALATION_THRESHOLDS["accuracy_warning"]:
            escalations.append(Escalation(
                kind="accuracy_drop",
                severity="warning",
                message=f"Accuracy below target: {score:.1%} (threshold: {ESCALATION_THRESHOLDS['accuracy_warning']:.0%})",
                context={"score": score, "threshold": ESCALATION_THRESHOLDS["accuracy_warning"]},
            ))

        if previous_accuracy is not None:
            drop = previous_accuracy - score
            if drop > ESCALATION_THRESHOLDS["accuracy_drop_warning"]:
                escalations.append(Escalation(
                    kind="accuracy_drop",
                    severity="warning",
                    message=f"Accuracy dropped {drop:.1%} from previous run ({previous_accuracy:.1%} -> {score:.1%})",
                    context={"previous": previous_accuracy, "current": score, "drop": drop},
                ))

    # 3. Validation failure — generated code that doesn't parse
    for iteration in loop_result.iterations:
        if not iteration.execution:
            continue
        for ar in iteration.execution.results:
            if ar.metrics.get("syntax_valid") is False:
                escalations.append(Escalation(
                    kind="validation_failure",
                    severity="warning",
                    message=f"Generated code failed syntax validation: {ar.title}",
                    context={"action_id": ar.action_id, "error": ar.error},
                ))

    # 4. Coverage regression
    if coverage_after < coverage_before:
        escalations.append(Escalation(
            kind="coverage_regression",
            severity="critical",
            message=f"Coverage regressed: {coverage_before:.1%} -> {coverage_after:.1%}",
            context={"before": coverage_before, "after": coverage_after},
        ))

    return escalations


# --- Rendering ---


def _status_emoji(report: OpsReport) -> str:
    """Return status indicator."""
    if any(e.severity == "critical" for e in report.escalations):
        return "RED"
    if any(e.severity == "warning" for e in report.escalations):
        return "YELLOW"
    return "GREEN"


def _accuracy_emoji(score: float) -> str:
    if score >= 0.8:
        return "GREEN"
    if score >= 0.6:
        return "YELLOW"
    return "RED"


def _render_markdown(report: OpsReport) -> str:
    """Render OpsReport as the ProductModeller's dashboard format."""
    lines: list[str] = []

    # Section 1: Loop Health
    status_label = _status_emoji(report)
    if report.converged:
        loop_status = f"Converged in {report.iterations} iteration(s)"
    else:
        loop_status = f"Did not converge ({report.iterations} iterations)"

    lines.append(f"## IGNITE Loop Report — {report.timestamp}")
    lines.append("")
    lines.append("| Metric | Value | Status |")
    lines.append("|--------|-------|--------|")
    lines.append(f"| **Loop Status** | {loop_status} | {status_label} |")
    lines.append(f"| **Actions Executed** | {report.actions_executed} of {report.actions_planned} planned | — |")
    lines.append(f"| **Accuracy** | {report.accuracy_score:.0%} ({report.actions_succeeded}/{report.actions_executed} succeeded) | {_accuracy_emoji(report.accuracy_score)} |")

    coverage_before_pct = f"{report.manifest_coverage_before:.1%}"
    coverage_after_pct = f"{report.manifest_coverage_after:.1%}"
    delta_sign = "+" if report.coverage_delta >= 0 else ""
    lines.append(f"| **Manifest Coverage** | {coverage_before_pct} -> {coverage_after_pct} ({delta_sign}{report.coverage_delta:.1%}) | — |")
    lines.append("")

    # Section 2: Actions Completed
    if report.action_details:
        lines.append("### Actions Completed")
        lines.append("")
        lines.append("| # | Action | Kind | Result |")
        lines.append("|---|--------|------|--------|")
        for i, ar in enumerate(report.action_details, 1):
            status_mark = {
                ActionStatus.SUCCESS: "OK",
                ActionStatus.PARTIAL: "PARTIAL",
                ActionStatus.FAILED: "FAIL",
                ActionStatus.SKIPPED: "SKIP",
            }.get(ar.status, str(ar.status))

            syntax_note = ""
            if ar.metrics.get("syntax_valid") is True:
                syntax_note = " (valid)"
            elif ar.metrics.get("syntax_valid") is False:
                syntax_note = " (syntax error)"

            lines.append(f"| {i} | {ar.title} | {ar.kind.value} | {status_mark}{syntax_note} |")
        lines.append("")

    # Section 3: Coverage Detail
    if report.span_kinds_covered or report.span_kinds_missing:
        lines.append("### Coverage")
        lines.append("")
        lines.append(f"**Covered:** {', '.join(report.span_kinds_covered) or 'none'}")
        lines.append(f"**Missing:** {', '.join(report.span_kinds_missing) or 'none'}")
        lines.append("")

    # Section 4: Escalations
    if report.escalations:
        lines.append("### Escalations")
        lines.append("")
        for i, esc in enumerate(report.escalations, 1):
            severity_tag = "CRITICAL" if esc.severity == "critical" else "WARNING"
            lines.append(f"{i}. **[{severity_tag}]** {esc.message}")
        lines.append("")
    else:
        lines.append("### No Escalations")
        lines.append("")

    return "\n".join(lines)


# --- Serialization ---


def _to_dict(report: OpsReport) -> dict[str, Any]:
    """Convert OpsReport to a JSON-serializable dict."""
    return {
        "run_id": report.run_id,
        "timestamp": report.timestamp,
        "traces_dir": report.traces_dir,
        "iterations": report.iterations,
        "converged": report.converged,
        "actions_planned": report.actions_planned,
        "actions_executed": report.actions_executed,
        "actions_succeeded": report.actions_succeeded,
        "actions_failed": report.actions_failed,
        "action_details": [
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
            for ar in report.action_details
        ],
        "accuracy_score": report.accuracy_score,
        "accuracy_by_kind": report.accuracy_by_kind,
        "manifest_coverage_before": report.manifest_coverage_before,
        "manifest_coverage_after": report.manifest_coverage_after,
        "coverage_delta": report.coverage_delta,
        "span_kinds_covered": report.span_kinds_covered,
        "span_kinds_missing": report.span_kinds_missing,
        "escalations": [
            {
                "kind": e.kind,
                "severity": e.severity,
                "message": e.message,
                "context": e.context,
            }
            for e in report.escalations
        ],
        "exit_code": report.exit_code,
    }


def _from_dict(data: dict[str, Any]) -> OpsReport:
    """Deserialize OpsReport from a dict."""
    report = OpsReport(
        run_id=data.get("run_id", ""),
        timestamp=data.get("timestamp", ""),
        traces_dir=data.get("traces_dir", ""),
        iterations=data.get("iterations", 0),
        converged=data.get("converged", False),
        actions_planned=data.get("actions_planned", 0),
        actions_executed=data.get("actions_executed", 0),
        actions_succeeded=data.get("actions_succeeded", 0),
        actions_failed=data.get("actions_failed", 0),
        accuracy_score=data.get("accuracy_score", 0.0),
        accuracy_by_kind=data.get("accuracy_by_kind", {}),
        manifest_coverage_before=data.get("manifest_coverage_before", 0.0),
        manifest_coverage_after=data.get("manifest_coverage_after", 0.0),
        coverage_delta=data.get("coverage_delta", 0.0),
        span_kinds_covered=data.get("span_kinds_covered", []),
        span_kinds_missing=data.get("span_kinds_missing", []),
    )

    # Reconstruct action details
    for ad in data.get("action_details", []):
        report.action_details.append(ActionResult(
            action_id=ad["action_id"],
            kind=ActionKind(ad["kind"]),
            status=ActionStatus(ad["status"]),
            title=ad["title"],
            outputs=ad.get("outputs", []),
            generated_files=ad.get("generated_files", []),
            error=ad.get("error"),
            metrics=ad.get("metrics", {}),
        ))

    # Reconstruct escalations
    for ed in data.get("escalations", []):
        report.escalations.append(Escalation(
            kind=ed["kind"],
            severity=ed["severity"],
            message=ed["message"],
            context=ed.get("context", {}),
        ))

    return report
