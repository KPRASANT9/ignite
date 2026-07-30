"""Tests for the IGNITE Feedback Writer — action_result trace capture."""

from __future__ import annotations

import json
import sys
from pathlib import Path

import pytest

sys.path.insert(0, str(Path(__file__).parent.parent / "src"))
# Also add the trace-sdk so TraceSession is available for feedback tests
sys.path.insert(0, str(Path(__file__).parent.parent.parent / "trace-sdk" / "src"))

from ignite_parser.executor import ActionResult, ActionStatus, ExecutionResult
from ignite_parser.planner import ActionKind, ActionPriority, ExecutionPlan, PlanAction
from ignite_parser.feedback import capture_feedback, _describe_outcome, _describe_learning


# --- Unit tests ---


class TestDescribeOutcome:
    def test_success(self):
        ar = ActionResult(
            action_id="a1",
            kind=ActionKind.GENERATE_MODEL,
            status=ActionStatus.SUCCESS,
            title="test",
            outputs=["Generated FSM model"],
        )
        desc = _describe_outcome(ar)
        assert "completed successfully" in desc
        assert "Generated FSM model" in desc

    def test_failed(self):
        ar = ActionResult(
            action_id="a2",
            kind=ActionKind.EXPLORE,
            status=ActionStatus.FAILED,
            title="test",
            error="No data",
        )
        desc = _describe_outcome(ar)
        assert "failed" in desc
        assert "No data" in desc

    def test_skipped(self):
        ar = ActionResult(
            action_id="a3",
            kind=ActionKind.VALIDATE,
            status=ActionStatus.SKIPPED,
            title="test",
        )
        desc = _describe_outcome(ar)
        assert "skipped" in desc


class TestDescribeLearning:
    def test_success_learning(self):
        ar = ActionResult(
            action_id="a1",
            kind=ActionKind.GENERATE_MODEL,
            status=ActionStatus.SUCCESS,
            title="test",
            generated_files=["model.py"],
        )
        desc = _describe_learning(ar)
        assert "Successfully" in desc

    def test_failed_learning(self):
        ar = ActionResult(
            action_id="a2",
            kind=ActionKind.EXPLORE,
            status=ActionStatus.FAILED,
            title="test",
            error="Missing span kind",
        )
        desc = _describe_learning(ar)
        assert "failed" in desc


# --- Integration tests ---


class TestCaptureFeedback:
    def test_produces_trace_file(self, tmp_path):
        ep = ExecutionPlan(actions=[
            PlanAction(
                action_id="a1",
                kind=ActionKind.GENERATE_MODEL,
                priority=ActionPriority.HIGH,
                title="Generate FSM",
                description="Generate FSM model",
            ),
        ])
        execution = ExecutionResult(
            results=[
                ActionResult(
                    action_id="a1",
                    kind=ActionKind.GENERATE_MODEL,
                    status=ActionStatus.SUCCESS,
                    title="Generate FSM",
                    outputs=["Generated model"],
                    generated_files=["model.py"],
                    metrics={"syntax_valid": True},
                ),
            ],
            total=1,
            succeeded=1,
        )

        path = capture_feedback(ep, execution, output_dir=str(tmp_path))

        assert path is not None
        assert path.exists()

        # The trace should be valid JSON
        data = json.loads(path.read_text())
        assert data["schema_version"] in ("0.1", "0.2")
        assert data["agent_id"] == "ignite-executor"
        assert len(data["spans"]) == 1
        assert data["spans"][0]["kind"] == "action_result"

    def test_captures_failures_as_findings(self, tmp_path):
        ep = ExecutionPlan(actions=[
            PlanAction(
                action_id="a1",
                kind=ActionKind.EXPLORE,
                priority=ActionPriority.MEDIUM,
                title="Explore auth_flow",
                description="Explore auth_flow span kind",
            ),
        ])
        execution = ExecutionResult(
            results=[
                ActionResult(
                    action_id="a1",
                    kind=ActionKind.EXPLORE,
                    status=ActionStatus.FAILED,
                    title="Explore auth_flow",
                    error="No sandbox credentials",
                ),
            ],
            total=1,
            failed=1,
        )

        path = capture_feedback(ep, execution, output_dir=str(tmp_path))

        assert path is not None
        data = json.loads(path.read_text())
        # Failed actions should produce error_pattern findings
        error_findings = [f for f in data["findings"] if f["category"] == "error_pattern"]
        assert len(error_findings) > 0

    def test_empty_results_returns_none(self, tmp_path):
        ep = ExecutionPlan()
        execution = ExecutionResult()

        path = capture_feedback(ep, execution, output_dir=str(tmp_path))
        assert path is None

    def test_feedback_trace_round_trips_through_parser(self, tmp_path):
        """Feedback trace must be parseable by the Parser."""
        ep = ExecutionPlan(actions=[
            PlanAction(
                action_id="a1",
                kind=ActionKind.GENERATE_MODEL,
                priority=ActionPriority.HIGH,
                title="Generate model",
                description="Generate model",
            ),
        ])
        execution = ExecutionResult(
            results=[
                ActionResult(
                    action_id="a1",
                    kind=ActionKind.GENERATE_MODEL,
                    status=ActionStatus.SUCCESS,
                    title="Generate model",
                    outputs=["model.py"],
                ),
            ],
            total=1,
            succeeded=1,
        )

        path = capture_feedback(ep, execution, output_dir=str(tmp_path))
        assert path is not None

        from ignite_parser.parser import parse_trace
        data = json.loads(path.read_text())
        result = parse_trace(data, source=str(path))
        assert result.ok, f"Feedback trace failed parsing: {result.errors}"
        assert result.valid_count == 1
