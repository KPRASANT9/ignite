"""Tests for the IGNITE Ops Agent Module — run_ops, OpsReport, escalation detection."""

from __future__ import annotations

import json
import sys
from pathlib import Path
from unittest.mock import MagicMock, patch

import pytest

sys.path.insert(0, str(Path(__file__).parent.parent / "src"))

from ignite_parser.executor import ActionResult, ActionStatus, ExecutionResult
from ignite_parser.planner import ActionKind, ActionPriority, ExecutionPlan, PlanAction
from ignite_parser.accuracy import AccuracyReport, ActionAccuracy
from ignite_parser.orchestrator import LoopIteration, LoopResult, PipelineResult
from ignite_parser.ops import (
    ESCALATION_THRESHOLDS,
    Escalation,
    OpsReport,
    _detect_escalations,
    _render_markdown,
    _to_dict,
    _from_dict,
    run_ops,
)


# --- Helpers ---


def _make_action_result(
    action_id: str = "a1",
    kind: ActionKind = ActionKind.GENERATE_MODEL,
    status: ActionStatus = ActionStatus.SUCCESS,
    title: str = "Test action",
    error: str | None = None,
    metrics: dict | None = None,
) -> ActionResult:
    return ActionResult(
        action_id=action_id,
        kind=kind,
        status=status,
        title=title,
        error=error,
        metrics=metrics or {"syntax_valid": True},
    )


def _make_plan(actions: list[PlanAction] | None = None) -> ExecutionPlan:
    if actions is None:
        actions = [
            PlanAction(
                action_id="a1",
                kind=ActionKind.GENERATE_MODEL,
                priority=ActionPriority.HIGH,
                title="Generate TransactionSync",
                description="Generate FSM model",
            ),
            PlanAction(
                action_id="a2",
                kind=ActionKind.GENERATE_HANDLER,
                priority=ActionPriority.MEDIUM,
                title="Generate error handler",
                description="Generate error handler",
            ),
        ]
    return ExecutionPlan(actions=actions)


def _make_execution(results: list[ActionResult] | None = None) -> ExecutionResult:
    if results is None:
        results = [
            _make_action_result("a1", title="Generate TransactionSync"),
            _make_action_result("a2", kind=ActionKind.GENERATE_HANDLER, title="Generate error handler"),
        ]
    return ExecutionResult(
        results=results,
        total=len(results),
        succeeded=sum(1 for r in results if r.status == ActionStatus.SUCCESS),
        failed=sum(1 for r in results if r.status == ActionStatus.FAILED),
        skipped=sum(1 for r in results if r.status == ActionStatus.SKIPPED),
    )


def _make_loop_result(
    converged: bool = True,
    iterations: int = 2,
    execution: ExecutionResult | None = None,
    plan: ExecutionPlan | None = None,
) -> LoopResult:
    if execution is None:
        execution = _make_execution()
    if plan is None:
        plan = _make_plan()

    loop_iterations = []
    for i in range(1, iterations + 1):
        pipeline = PipelineResult(traces_parsed=4, plan=plan if i == iterations else _make_plan())
        loop_iterations.append(LoopIteration(
            iteration=i,
            pipeline=pipeline,
            execution=execution if i == iterations else None,
            new_action_count=2 if i == 1 else 0,
        ))

    return LoopResult(
        iterations=loop_iterations,
        converged=converged,
        total_actions_executed=execution.succeeded,
        total_files_generated=0,
    )


# --- OpsReport dataclass ---


class TestOpsReport:
    def test_default_values(self):
        report = OpsReport()
        assert report.run_id == ""
        assert report.iterations == 0
        assert report.converged is False
        assert report.escalations == []
        assert report.accuracy_score == 0.0

    def test_exit_code_clean(self):
        report = OpsReport(converged=True)
        assert report.exit_code == 0

    def test_exit_code_warning(self):
        report = OpsReport(
            converged=True,
            escalations=[Escalation(kind="accuracy_drop", severity="warning", message="low")],
        )
        assert report.exit_code == 1

    def test_exit_code_not_converged(self):
        report = OpsReport(converged=False)
        assert report.exit_code == 2

    def test_exit_code_critical(self):
        report = OpsReport(
            converged=True,
            escalations=[Escalation(kind="coverage_regression", severity="critical", message="bad")],
        )
        assert report.exit_code == 3

    def test_exit_code_critical_trumps_not_converged(self):
        report = OpsReport(
            converged=False,
            escalations=[Escalation(kind="coverage_regression", severity="critical", message="bad")],
        )
        assert report.exit_code == 3


# --- Escalation detection ---


class TestEscalationDetection:
    def test_no_escalations_on_healthy_run(self):
        loop = _make_loop_result(converged=True)
        accuracy = AccuracyReport(overall_score=0.9, success_rate=0.9)
        result = _detect_escalations(loop, accuracy, 0.5, 0.625, None)
        assert result == []

    def test_unhandled_action_kind(self):
        ar = _make_action_result(
            status=ActionStatus.FAILED,
            error="No handler for action kind: new_kind",
        )
        execution = _make_execution([ar])
        loop = _make_loop_result(execution=execution)
        result = _detect_escalations(loop, None, 0.5, 0.5, None)
        assert len(result) == 1
        assert result[0].kind == "unhandled_action"
        assert result[0].severity == "critical"

    def test_accuracy_critical(self):
        accuracy = AccuracyReport(overall_score=0.4)
        loop = _make_loop_result()
        result = _detect_escalations(loop, accuracy, 0.5, 0.5, None)
        assert any(e.kind == "accuracy_drop" and e.severity == "critical" for e in result)

    def test_accuracy_warning(self):
        accuracy = AccuracyReport(overall_score=0.7)
        loop = _make_loop_result()
        result = _detect_escalations(loop, accuracy, 0.5, 0.5, None)
        assert any(e.kind == "accuracy_drop" and e.severity == "warning" for e in result)

    def test_accuracy_drop_from_previous(self):
        accuracy = AccuracyReport(overall_score=0.75)
        loop = _make_loop_result()
        result = _detect_escalations(loop, accuracy, 0.5, 0.5, previous_accuracy=0.95)
        drop_escalations = [e for e in result if "dropped" in e.message]
        assert len(drop_escalations) == 1

    def test_no_accuracy_drop_within_threshold(self):
        accuracy = AccuracyReport(overall_score=0.88)
        loop = _make_loop_result()
        result = _detect_escalations(loop, accuracy, 0.5, 0.5, previous_accuracy=0.90)
        drop_escalations = [e for e in result if "dropped" in e.message]
        assert len(drop_escalations) == 0

    def test_validation_failure(self):
        ar = _make_action_result(
            status=ActionStatus.PARTIAL,
            error="Syntax error",
            metrics={"syntax_valid": False},
        )
        execution = _make_execution([ar])
        loop = _make_loop_result(execution=execution)
        result = _detect_escalations(loop, None, 0.5, 0.5, None)
        assert any(e.kind == "validation_failure" for e in result)

    def test_coverage_regression(self):
        loop = _make_loop_result()
        result = _detect_escalations(loop, None, 0.625, 0.5, None)
        assert any(e.kind == "coverage_regression" and e.severity == "critical" for e in result)

    def test_no_coverage_regression_on_increase(self):
        loop = _make_loop_result()
        result = _detect_escalations(loop, None, 0.5, 0.625, None)
        assert not any(e.kind == "coverage_regression" for e in result)


# --- Markdown rendering ---


class TestRenderMarkdown:
    def test_contains_header(self):
        report = OpsReport(
            timestamp="2026-07-28T08:30:00Z",
            converged=True,
            iterations=2,
        )
        md = report.to_markdown()
        assert "IGNITE Loop Report" in md
        assert "2026-07-28T08:30:00Z" in md

    def test_contains_loop_status_converged(self):
        report = OpsReport(converged=True, iterations=2)
        md = report.to_markdown()
        assert "Converged in 2 iteration(s)" in md

    def test_contains_loop_status_not_converged(self):
        report = OpsReport(converged=False, iterations=5)
        md = report.to_markdown()
        assert "Did not converge" in md

    def test_contains_action_table(self):
        report = OpsReport(
            action_details=[
                _make_action_result("a1", title="Generate Model"),
            ],
            actions_planned=1,
            actions_executed=1,
            actions_succeeded=1,
        )
        md = report.to_markdown()
        assert "Actions Completed" in md
        assert "Generate Model" in md
        assert "OK" in md

    def test_contains_escalation(self):
        report = OpsReport(
            escalations=[Escalation(kind="accuracy_drop", severity="critical", message="Too low")],
        )
        md = report.to_markdown()
        assert "Escalations" in md
        assert "CRITICAL" in md
        assert "Too low" in md

    def test_no_escalations_section(self):
        report = OpsReport()
        md = report.to_markdown()
        assert "No Escalations" in md

    def test_coverage_section(self):
        report = OpsReport(
            span_kinds_covered=["api_call", "auth_flow"],
            span_kinds_missing=["doc_read"],
        )
        md = report.to_markdown()
        assert "Coverage" in md
        assert "api_call" in md
        assert "doc_read" in md

    def test_accuracy_display(self):
        report = OpsReport(
            accuracy_score=0.85,
            actions_succeeded=8,
            actions_executed=10,
            actions_planned=10,
        )
        md = report.to_markdown()
        assert "85%" in md


# --- JSON serialization ---


class TestJsonSerialization:
    def test_roundtrip(self):
        original = OpsReport(
            run_id="test-123",
            timestamp="2026-07-28T08:30:00Z",
            traces_dir="/tmp/traces",
            iterations=3,
            converged=True,
            actions_planned=5,
            actions_executed=4,
            actions_succeeded=3,
            actions_failed=1,
            accuracy_score=0.85,
            accuracy_by_kind={"generate_model": 0.9},
            manifest_coverage_before=0.5,
            manifest_coverage_after=0.625,
            coverage_delta=0.125,
            span_kinds_covered=["api_call", "auth_flow"],
            span_kinds_missing=["doc_read"],
            action_details=[
                _make_action_result("a1", title="Gen Model"),
            ],
            escalations=[
                Escalation(kind="accuracy_drop", severity="warning", message="Below target"),
            ],
        )

        json_str = original.to_json()
        restored = OpsReport.from_json(json_str)

        assert restored.run_id == "test-123"
        assert restored.converged is True
        assert restored.iterations == 3
        assert restored.accuracy_score == 0.85
        assert restored.manifest_coverage_before == 0.5
        assert restored.coverage_delta == 0.125
        assert len(restored.action_details) == 1
        assert restored.action_details[0].title == "Gen Model"
        assert len(restored.escalations) == 1
        assert restored.escalations[0].kind == "accuracy_drop"

    def test_to_json_contains_exit_code(self):
        report = OpsReport(converged=True)
        data = json.loads(report.to_json())
        assert data["exit_code"] == 0

    def test_from_json_handles_empty(self):
        report = OpsReport.from_json("{}")
        assert report.run_id == ""
        assert report.iterations == 0


# --- run_ops integration (mocked orchestrator) ---


class TestRunOps:
    @patch("ignite_parser.ops._get_coverage")
    @patch("ignite_parser.ops.run_loop")
    def test_healthy_run(self, mock_run_loop, mock_get_coverage):
        """A clean run should produce a converged report with no escalations."""
        mock_get_coverage.return_value = {"span_kinds_seen": {"api_call", "auth_flow"}}

        loop = _make_loop_result(converged=True, iterations=2)
        mock_run_loop.return_value = loop

        report = run_ops(traces_dir="/tmp/traces", max_iterations=5)

        assert report.converged is True
        assert report.iterations == 2
        assert report.exit_code == 0
        assert report.run_id != ""
        assert report.timestamp != ""

    @patch("ignite_parser.ops._get_coverage")
    @patch("ignite_parser.ops.run_loop")
    def test_failed_actions_escalate(self, mock_run_loop, mock_get_coverage):
        """A run with unhandled action kinds should produce critical escalation."""
        mock_get_coverage.return_value = {"span_kinds_seen": {"api_call"}}

        ar = _make_action_result(
            status=ActionStatus.FAILED,
            error="No handler for action kind: custom_kind",
        )
        execution = _make_execution([ar])
        loop = _make_loop_result(converged=True, execution=execution)
        mock_run_loop.return_value = loop

        report = run_ops(traces_dir="/tmp/traces")

        assert any(e.kind == "unhandled_action" for e in report.escalations)
        assert report.exit_code == 3

    @patch("ignite_parser.ops._get_coverage")
    @patch("ignite_parser.ops.run_loop")
    def test_not_converged_exit_code(self, mock_run_loop, mock_get_coverage):
        mock_get_coverage.return_value = {"span_kinds_seen": set()}

        loop = _make_loop_result(converged=False, iterations=5)
        mock_run_loop.return_value = loop

        report = run_ops(traces_dir="/tmp/traces")

        assert report.converged is False
        assert report.exit_code == 2

    @patch("ignite_parser.ops._get_coverage")
    @patch("ignite_parser.ops.run_loop")
    def test_coverage_delta_computed(self, mock_run_loop, mock_get_coverage):
        """Coverage before/after should be computed from span kinds."""
        # First call = before, second call = after
        mock_get_coverage.side_effect = [
            {"span_kinds_seen": {"api_call"}},
            {"span_kinds_seen": {"api_call", "auth_flow", "state_transition"}},
        ]

        loop = _make_loop_result(converged=True)
        mock_run_loop.return_value = loop

        report = run_ops(traces_dir="/tmp/traces")

        assert report.manifest_coverage_after > report.manifest_coverage_before
        assert report.coverage_delta > 0
        assert "auth_flow" in report.span_kinds_covered
        assert "api_call" in report.span_kinds_covered

    @patch("ignite_parser.ops._get_coverage")
    @patch("ignite_parser.ops.run_loop")
    def test_previous_accuracy_drop_detection(self, mock_run_loop, mock_get_coverage):
        mock_get_coverage.return_value = {"span_kinds_seen": set()}

        # Set up a low-accuracy result
        ar = _make_action_result(status=ActionStatus.FAILED, metrics={})
        execution = _make_execution([ar])
        plan = _make_plan([PlanAction(
            action_id="a1", kind=ActionKind.GENERATE_MODEL,
            priority=ActionPriority.HIGH, title="Test", description="Test",
        )])
        loop = _make_loop_result(converged=True, execution=execution, plan=plan)
        mock_run_loop.return_value = loop

        report = run_ops(traces_dir="/tmp/traces", previous_accuracy=0.95)

        # Should detect the drop from 0.95
        assert report.accuracy_score < 0.95


# --- Escalation dataclass ---


class TestEscalation:
    def test_basic_creation(self):
        esc = Escalation(
            kind="unhandled_action",
            severity="critical",
            message="No handler for GENERATE_WIDGET",
        )
        assert esc.kind == "unhandled_action"
        assert esc.severity == "critical"
        assert esc.context == {}

    def test_with_context(self):
        esc = Escalation(
            kind="accuracy_drop",
            severity="warning",
            message="Score dropped",
            context={"previous": 0.9, "current": 0.7},
        )
        assert esc.context["previous"] == 0.9


# --- Threshold config ---


class TestThresholds:
    def test_thresholds_are_tunable(self):
        """Thresholds should be in a module-level dict, not hardcoded."""
        assert "accuracy_warning" in ESCALATION_THRESHOLDS
        assert "accuracy_critical" in ESCALATION_THRESHOLDS
        assert "accuracy_drop_warning" in ESCALATION_THRESHOLDS
        assert ESCALATION_THRESHOLDS["accuracy_warning"] == 0.8
        assert ESCALATION_THRESHOLDS["accuracy_critical"] == 0.5
