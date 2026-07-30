"""Tests for the IGNITE Accuracy Metric — planned vs actual comparison."""

from __future__ import annotations

import sys
from pathlib import Path

import pytest

sys.path.insert(0, str(Path(__file__).parent.parent / "src"))

from ignite_parser.executor import ActionResult, ActionStatus, ExecutionResult
from ignite_parser.planner import ActionKind, ActionPriority, ExecutionPlan, PlanAction
from ignite_parser.accuracy import measure_accuracy, AccuracyReport, _score_action


# --- Unit tests ---


class TestScoreAction:
    def test_success_scores_1(self):
        ar = ActionResult(
            action_id="a1",
            kind=ActionKind.GENERATE_MODEL,
            status=ActionStatus.SUCCESS,
            title="test",
            metrics={"syntax_valid": True},
        )
        assert _score_action(ar) == 1.0

    def test_success_with_bad_syntax_penalized(self):
        ar = ActionResult(
            action_id="a1",
            kind=ActionKind.GENERATE_MODEL,
            status=ActionStatus.SUCCESS,
            title="test",
            metrics={"syntax_valid": False},
        )
        assert _score_action(ar) == 0.7

    def test_partial_scores_half(self):
        ar = ActionResult(
            action_id="a1",
            kind=ActionKind.GENERATE_MODEL,
            status=ActionStatus.PARTIAL,
            title="test",
        )
        assert _score_action(ar) == 0.5

    def test_failed_scores_zero(self):
        ar = ActionResult(
            action_id="a1",
            kind=ActionKind.GENERATE_MODEL,
            status=ActionStatus.FAILED,
            title="test",
        )
        assert _score_action(ar) == 0.0

    def test_skipped_scores_zero(self):
        ar = ActionResult(
            action_id="a1",
            kind=ActionKind.GENERATE_MODEL,
            status=ActionStatus.SKIPPED,
            title="test",
        )
        assert _score_action(ar) == 0.0


# --- Integration tests ---


class TestMeasureAccuracy:
    def test_perfect_execution(self):
        ep = ExecutionPlan(actions=[
            PlanAction(action_id="a1", kind=ActionKind.GENERATE_MODEL,
                       priority=ActionPriority.HIGH, description="", title="Gen model"),
            PlanAction(action_id="a2", kind=ActionKind.EXPLORE,
                       priority=ActionPriority.MEDIUM, description="", title="Explore"),
        ])
        execution = ExecutionResult(
            results=[
                ActionResult(action_id="a1", kind=ActionKind.GENERATE_MODEL,
                             status=ActionStatus.SUCCESS, title="Gen model",
                             metrics={"syntax_valid": True}),
                ActionResult(action_id="a2", kind=ActionKind.EXPLORE,
                             status=ActionStatus.SUCCESS, title="Explore"),
            ],
            total=2, succeeded=2,
        )

        report = measure_accuracy(ep, execution)

        assert report.overall_score == 1.0
        assert report.success_rate == 1.0
        assert report.total_actions == 2

    def test_mixed_execution(self):
        ep = ExecutionPlan(actions=[
            PlanAction(action_id="a1", kind=ActionKind.GENERATE_MODEL,
                       priority=ActionPriority.HIGH, description="", title="Model"),
            PlanAction(action_id="a2", kind=ActionKind.EXPLORE,
                       priority=ActionPriority.MEDIUM, description="", title="Explore"),
        ])
        execution = ExecutionResult(
            results=[
                ActionResult(action_id="a1", kind=ActionKind.GENERATE_MODEL,
                             status=ActionStatus.SUCCESS, title="Model",
                             metrics={"syntax_valid": True}),
                ActionResult(action_id="a2", kind=ActionKind.EXPLORE,
                             status=ActionStatus.FAILED, title="Explore",
                             error="No data"),
            ],
            total=2, succeeded=1, failed=1,
        )

        report = measure_accuracy(ep, execution)

        assert report.overall_score == 0.5
        assert report.success_rate == 0.5
        assert ActionKind.GENERATE_MODEL.value in report.by_kind
        assert report.by_kind[ActionKind.GENERATE_MODEL.value] == 1.0
        assert report.by_kind[ActionKind.EXPLORE.value] == 0.0

    def test_empty_plan(self):
        ep = ExecutionPlan()
        execution = ExecutionResult()

        report = measure_accuracy(ep, execution)

        assert report.overall_score == 0.0
        assert report.total_actions == 0

    def test_missing_results(self):
        ep = ExecutionPlan(actions=[
            PlanAction(action_id="a1", kind=ActionKind.GENERATE_MODEL,
                       priority=ActionPriority.HIGH, description="", title="Model"),
        ])
        execution = ExecutionResult(results=[], total=0)

        report = measure_accuracy(ep, execution)

        assert report.total_actions == 1
        assert report.overall_score == 0.0
        assert report.action_accuracies[0].notes == ["No execution result found"]

    def test_summary_format(self):
        ep = ExecutionPlan(actions=[
            PlanAction(action_id="a1", kind=ActionKind.GENERATE_MODEL,
                       priority=ActionPriority.HIGH, description="", title="Model"),
        ])
        execution = ExecutionResult(
            results=[
                ActionResult(action_id="a1", kind=ActionKind.GENERATE_MODEL,
                             status=ActionStatus.SUCCESS, title="Model"),
            ],
            total=1, succeeded=1,
        )

        report = measure_accuracy(ep, execution)
        s = report.summary()

        assert "overall_score" in s
        assert "success_rate" in s
        assert "total_actions" in s
        assert "by_kind" in s

    def test_syntax_validity_rate(self):
        ep = ExecutionPlan(actions=[
            PlanAction(action_id="a1", kind=ActionKind.GENERATE_MODEL,
                       priority=ActionPriority.HIGH, description="", title="M1"),
            PlanAction(action_id="a2", kind=ActionKind.GENERATE_MODEL,
                       priority=ActionPriority.HIGH, description="", title="M2"),
        ])
        execution = ExecutionResult(
            results=[
                ActionResult(action_id="a1", kind=ActionKind.GENERATE_MODEL,
                             status=ActionStatus.SUCCESS, title="M1",
                             metrics={"syntax_valid": True}),
                ActionResult(action_id="a2", kind=ActionKind.GENERATE_MODEL,
                             status=ActionStatus.SUCCESS, title="M2",
                             metrics={"syntax_valid": False}),
            ],
            total=2, succeeded=2,
        )

        report = measure_accuracy(ep, execution)
        assert report.syntax_validity_rate == 0.5
