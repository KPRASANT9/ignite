"""Tests for IGNITE Batch 2 — Feedback Divergence.

Covers:
- ExpectedOutcome / ActualOutcome dataclasses
- compute_divergence() — semantic distance between expected and actual
- ErrP correction strategy table — lookup_correction()
- SpanBuilder.expect() / .actual() / .divergence()
- Parser parsing of new fields
- Feedback writer setting expected/actual/divergence on action_result spans
"""

from __future__ import annotations

import json
import sys
from pathlib import Path

import pytest

sys.path.insert(0, str(Path(__file__).parent.parent / "src"))
sys.path.insert(0, str(Path(__file__).parent.parent.parent / "trace-sdk" / "src"))

from ignite_parser.models import (
    CorrectionAction,
    ExpectedOutcome,
    ActualOutcome,
    RequestIntent,
    ResponseOutcome,
    Span,
)
from ignite_parser.feedback import (
    ERRP_CORRECTION_TABLE,
    compute_divergence,
    lookup_correction,
    _describe_expected,
    _infer_intent,
    _infer_outcome,
)
from ignite_parser.executor import ActionResult, ActionStatus, ExecutionResult
from ignite_parser.planner import ActionKind, ActionPriority, ExecutionPlan, PlanAction
from ignite_parser.feedback import capture_feedback
from ignite_parser.parser import parse_trace
from ignite_trace.span import SpanBuilder


# --- ExpectedOutcome / ActualOutcome dataclasses ---


class TestExpectedOutcome:
    def test_default_values(self):
        eo = ExpectedOutcome()
        assert eo.description == ""
        assert eo.confidence == 0.0

    def test_with_values(self):
        eo = ExpectedOutcome(description="success with valid syntax", confidence=0.9)
        assert eo.description == "success with valid syntax"
        assert eo.confidence == 0.9


class TestActualOutcome:
    def test_default_values(self):
        ao = ActualOutcome()
        assert ao.description == ""

    def test_with_values(self):
        ao = ActualOutcome(description="generated model with 5 states")
        assert ao.description == "generated model with 5 states"


# --- Span with feedback divergence fields ---


class TestSpanDivergenceFields:
    def test_span_defaults_to_none(self):
        span = Span()
        assert span.expected_outcome is None
        assert span.actual_outcome is None
        assert span.divergence_score is None

    def test_span_with_outcomes(self):
        span = Span(
            expected_outcome=ExpectedOutcome("success", 0.8),
            actual_outcome=ActualOutcome("failed with syntax error"),
            divergence_score=0.75,
        )
        assert span.expected_outcome.description == "success"
        assert span.expected_outcome.confidence == 0.8
        assert span.actual_outcome.description == "failed with syntax error"
        assert span.divergence_score == 0.75


# --- compute_divergence ---


class TestComputeDivergence:
    def test_exact_match_is_zero(self):
        assert compute_divergence("success", "success") == 0.0

    def test_case_insensitive_match(self):
        assert compute_divergence("SUCCESS", "success") == 0.0

    def test_both_empty_is_zero(self):
        assert compute_divergence("", "") == 0.0

    def test_no_expected_is_neutral(self):
        assert compute_divergence("", "something happened") == 0.5

    def test_no_actual_is_high(self):
        assert compute_divergence("expected success", "") == 0.8

    def test_total_mismatch_is_high(self):
        score = compute_divergence(
            "generate_model completed successfully with valid syntax",
            "catastrophic failure unknown error",
        )
        assert score > 0.5

    def test_partial_overlap_is_moderate(self):
        score = compute_divergence(
            "generate_model completed successfully",
            "generate_model completed with errors",
        )
        assert 0.0 < score < 0.8

    def test_high_confidence_amplifies_divergence(self):
        low_conf = compute_divergence("alpha beta", "gamma delta", expected_confidence=0.1)
        high_conf = compute_divergence("alpha beta", "gamma delta", expected_confidence=1.0)
        assert high_conf > low_conf

    def test_score_clamped_to_0_1(self):
        score = compute_divergence("a", "b", expected_confidence=1.0)
        assert 0.0 <= score <= 1.0

    def test_identical_long_descriptions(self):
        desc = "generate_model completed successfully with valid syntax producing 5 states and 8 transitions"
        assert compute_divergence(desc, desc) == 0.0


# --- ErrP Correction Strategy Table ---


class TestErrPCorrectionTable:
    def test_query_rate_limited(self):
        result = lookup_correction(RequestIntent.QUERY.value, ResponseOutcome.RATE_LIMITED.value)
        assert result == CorrectionAction.BACKOFF_RETRY

    def test_query_auth_failure(self):
        result = lookup_correction(RequestIntent.QUERY.value, ResponseOutcome.AUTH_FAILURE.value)
        assert result == CorrectionAction.REAUTH_RETRY

    def test_mutation_error_escalates(self):
        result = lookup_correction(RequestIntent.MUTATION.value, ResponseOutcome.ERROR.value)
        assert result == CorrectionAction.LOG_ESCALATE

    def test_mutation_partial_idempotency(self):
        result = lookup_correction(RequestIntent.MUTATION.value, ResponseOutcome.PARTIAL.value)
        assert result == CorrectionAction.IDEMPOTENCY_RETRY

    def test_state_transition_auth_restarts_flow(self):
        result = lookup_correction(RequestIntent.STATE_TRANSITION.value, ResponseOutcome.AUTH_FAILURE.value)
        assert result == CorrectionAction.RESTART_FLOW

    def test_config_change_error_escalates_human(self):
        result = lookup_correction(RequestIntent.CONFIG_CHANGE.value, ResponseOutcome.ERROR.value)
        assert result == CorrectionAction.ESCALATE_HUMAN

    def test_decision_error_escalates_human(self):
        result = lookup_correction(RequestIntent.DECISION.value, ResponseOutcome.ERROR.value)
        assert result == CorrectionAction.ESCALATE_HUMAN

    def test_success_returns_none(self):
        """Success outcomes need no correction."""
        result = lookup_correction(RequestIntent.QUERY.value, ResponseOutcome.SUCCESS.value)
        assert result is None

    def test_mutation_timeout_escalates(self):
        result = lookup_correction(RequestIntent.MUTATION.value, ResponseOutcome.TIMEOUT.value)
        assert result == CorrectionAction.LOG_ESCALATE

    def test_all_table_entries_have_valid_enums(self):
        """Every key in the table uses valid enum values."""
        for (intent, outcome), action in ERRP_CORRECTION_TABLE.items():
            assert intent in [ri.value for ri in RequestIntent]
            assert outcome in [ro.value for ro in ResponseOutcome]
            assert isinstance(action, CorrectionAction)


# --- Intent/Outcome inference helpers ---


class TestInferIntent:
    def test_generate_model_is_mutation(self):
        ar = ActionResult(action_id="a1", kind=ActionKind.GENERATE_MODEL, status=ActionStatus.SUCCESS, title="t")
        assert _infer_intent(ar) == RequestIntent.MUTATION.value

    def test_explore_is_query(self):
        ar = ActionResult(action_id="a1", kind=ActionKind.EXPLORE, status=ActionStatus.SUCCESS, title="t")
        assert _infer_intent(ar) == RequestIntent.QUERY.value

    def test_validate_is_query(self):
        ar = ActionResult(action_id="a1", kind=ActionKind.VALIDATE, status=ActionStatus.SUCCESS, title="t")
        assert _infer_intent(ar) == RequestIntent.QUERY.value


class TestInferOutcome:
    def test_success(self):
        ar = ActionResult(action_id="a1", kind=ActionKind.GENERATE_MODEL, status=ActionStatus.SUCCESS, title="t")
        assert _infer_outcome(ar) == ResponseOutcome.SUCCESS.value

    def test_failed(self):
        ar = ActionResult(action_id="a1", kind=ActionKind.GENERATE_MODEL, status=ActionStatus.FAILED, title="t", error="boom")
        assert _infer_outcome(ar) == ResponseOutcome.ERROR.value

    def test_partial(self):
        ar = ActionResult(action_id="a1", kind=ActionKind.GENERATE_MODEL, status=ActionStatus.PARTIAL, title="t")
        assert _infer_outcome(ar) == ResponseOutcome.PARTIAL.value

    def test_auth_error_detected(self):
        ar = ActionResult(action_id="a1", kind=ActionKind.GENERATE_MODEL, status=ActionStatus.FAILED, title="t", error="auth token expired")
        assert _infer_outcome(ar) == ResponseOutcome.AUTH_FAILURE.value

    def test_rate_limit_detected(self):
        ar = ActionResult(action_id="a1", kind=ActionKind.GENERATE_MODEL, status=ActionStatus.FAILED, title="t", error="rate limit exceeded")
        assert _infer_outcome(ar) == ResponseOutcome.RATE_LIMITED.value


# --- SpanBuilder integration ---


class TestSpanBuilderDivergence:
    def test_expect_sets_expected_outcome(self):
        sb = SpanBuilder("api_call", "test", 1, "t1")
        sb.expect("should succeed", confidence=0.9)
        d = sb.to_dict()
        assert d["expected_outcome"] == {"description": "should succeed", "confidence": 0.9}

    def test_actual_sets_actual_outcome(self):
        sb = SpanBuilder("api_call", "test", 1, "t1")
        sb.actual("it succeeded")
        d = sb.to_dict()
        assert d["actual_outcome"] == {"description": "it succeeded"}

    def test_divergence_sets_score(self):
        sb = SpanBuilder("api_call", "test", 1, "t1")
        sb.divergence(0.42)
        d = sb.to_dict()
        assert d["divergence_score"] == 0.42

    def test_divergence_clamped(self):
        sb = SpanBuilder("api_call", "test", 1, "t1")
        sb.divergence(1.5)
        d = sb.to_dict()
        assert d["divergence_score"] == 1.0

        sb2 = SpanBuilder("api_call", "test", 1, "t1")
        sb2.divergence(-0.5)
        d2 = sb2.to_dict()
        assert d2["divergence_score"] == 0.0

    def test_no_divergence_fields_when_not_set(self):
        sb = SpanBuilder("api_call", "test", 1, "t1")
        d = sb.to_dict()
        assert "expected_outcome" not in d
        assert "actual_outcome" not in d
        assert "divergence_score" not in d

    def test_full_chain(self):
        sb = SpanBuilder("action_result", "Generate FSM", 1, "t1")
        sb.expect("generate_model completed successfully", confidence=0.8)
        sb.actual("generate_model failed: syntax error")
        sb.divergence(0.75)
        sb.classify(request_intent="mutation", response_outcome="error")
        d = sb.to_dict()
        assert d["expected_outcome"]["confidence"] == 0.8
        assert d["actual_outcome"]["description"] == "generate_model failed: syntax error"
        assert d["divergence_score"] == 0.75
        assert d["request_intent"] == "mutation"
        assert d["response_outcome"] == "error"


# --- Parser round-trip ---


class TestParserDivergenceFields:
    def _make_trace_data(self, span_overrides: dict | None = None):
        """Build a minimal valid trace with v0.2 divergence fields."""
        span = {
            "span_id": "span-001",
            "kind": "action_result",
            "started_at": "2026-07-28T08:00:00Z",
            "duration_ms": 100,
            "interaction": {"target": "Generate Model"},
            "observation": {
                "what_happened": "Generated FSM model",
                "what_learned": "Pipeline can produce models",
                "confidence": "high",
            },
        }
        if span_overrides:
            span.update(span_overrides)
        return {
            "schema_version": "0.2",
            "trace_id": "trace-001",
            "agent_id": "bumble",
            "agent_role": "explorer",
            "system": "ignite",
            "status": "completed",
            "started_at": "2026-07-28T08:00:00Z",
            "objective": "Test divergence parsing",
            "spans": [span],
            "findings": [],
        }

    def test_parse_with_divergence_fields(self):
        data = self._make_trace_data({
            "expected_outcome": {"description": "success with valid syntax", "confidence": 0.9},
            "actual_outcome": {"description": "success with valid syntax"},
            "divergence_score": 0.0,
        })
        result = parse_trace(data)
        assert result.ok
        span = result.traces[0].spans[0]
        assert span.expected_outcome is not None
        assert span.expected_outcome.description == "success with valid syntax"
        assert span.expected_outcome.confidence == 0.9
        assert span.actual_outcome is not None
        assert span.actual_outcome.description == "success with valid syntax"
        assert span.divergence_score == 0.0

    def test_parse_without_divergence_fields_defaults_none(self):
        data = self._make_trace_data()
        result = parse_trace(data)
        assert result.ok
        span = result.traces[0].spans[0]
        assert span.expected_outcome is None
        assert span.actual_outcome is None
        assert span.divergence_score is None

    def test_v01_trace_backward_compat(self):
        """v0.1 traces have no divergence fields and should still parse."""
        data = self._make_trace_data()
        data["schema_version"] = "0.1"
        result = parse_trace(data)
        assert result.ok
        span = result.traces[0].spans[0]
        assert span.expected_outcome is None
        assert span.divergence_score is None

    def test_invalid_divergence_score_warns(self):
        data = self._make_trace_data({"divergence_score": "not_a_float"})
        result = parse_trace(data)
        assert result.ok
        span = result.traces[0].spans[0]
        assert span.divergence_score is None
        assert any("divergence_score" in w.path for w in result.warnings)


# --- Feedback writer integration ---


class TestFeedbackWithDivergence:
    def test_feedback_sets_divergence_fields(self, tmp_path):
        """capture_feedback should set expected/actual/divergence on each span."""
        plan = ExecutionPlan(actions=[
            PlanAction(
                action_id="a1",
                kind=ActionKind.GENERATE_MODEL,
                priority=ActionPriority.HIGH,
                title="Generate TransactionSync",
                description="Generate FSM",
            ),
        ])
        execution = ExecutionResult(
            results=[
                ActionResult(
                    action_id="a1",
                    kind=ActionKind.GENERATE_MODEL,
                    status=ActionStatus.SUCCESS,
                    title="Generate TransactionSync",
                    outputs=["Generated FSM"],
                    metrics={"syntax_valid": True},
                ),
            ],
            total=1, succeeded=1, failed=0, skipped=0,
        )

        trace_dir = tmp_path / "traces"
        trace_dir.mkdir()
        result_path = capture_feedback(plan, execution, output_dir=str(trace_dir))
        assert result_path is not None

        # Parse the feedback trace and check divergence fields
        trace_data = json.loads(result_path.read_text())
        result = parse_trace(trace_data)
        assert result.ok

        # Find the action_result span (not the summary)
        action_spans = [s for s in result.traces[0].spans if s.kind.value == "action_result"]
        assert len(action_spans) >= 1

        span = action_spans[0]
        assert span.expected_outcome is not None
        assert "generate_model" in span.expected_outcome.description.lower()
        assert span.actual_outcome is not None
        assert span.divergence_score is not None
        assert 0.0 <= span.divergence_score <= 1.0

    def test_failed_action_has_correction_metadata(self, tmp_path):
        """Failed mutations should have correction_action in metadata."""
        plan = ExecutionPlan(actions=[
            PlanAction(
                action_id="a1",
                kind=ActionKind.GENERATE_MODEL,
                priority=ActionPriority.HIGH,
                title="Generate Model",
                description="Generate FSM",
            ),
        ])
        execution = ExecutionResult(
            results=[
                ActionResult(
                    action_id="a1",
                    kind=ActionKind.GENERATE_MODEL,
                    status=ActionStatus.FAILED,
                    title="Generate Model",
                    error="FSM not found",
                ),
            ],
            total=1, succeeded=0, failed=1, skipped=0,
        )

        trace_dir = tmp_path / "traces"
        trace_dir.mkdir()
        result_path = capture_feedback(plan, execution, output_dir=str(trace_dir))
        assert result_path is not None

        trace_data = json.loads(result_path.read_text())
        result = parse_trace(trace_data)
        assert result.ok

        action_spans = [s for s in result.traces[0].spans if s.kind.value == "action_result"]
        span = action_spans[0]

        # mutation + error → log_escalate
        assert span.metadata.get("correction_action") == CorrectionAction.LOG_ESCALATE.value


# --- CorrectionAction enum ---


class TestCorrectionActionEnum:
    def test_all_values(self):
        assert len(CorrectionAction) == 6
        values = {c.value for c in CorrectionAction}
        assert "backoff_retry" in values
        assert "reauth_retry" in values
        assert "log_escalate" in values
        assert "idempotency_retry" in values
        assert "restart_flow" in values
        assert "escalate_human" in values


# --- _describe_expected ---


class TestDescribeExpected:
    def test_generate_model(self):
        ar = ActionResult(action_id="a1", kind=ActionKind.GENERATE_MODEL, status=ActionStatus.SUCCESS, title="t")
        desc = _describe_expected(ar)
        assert "generate_model" in desc
        assert "successfully" in desc

    def test_explore(self):
        ar = ActionResult(action_id="a1", kind=ActionKind.EXPLORE, status=ActionStatus.SUCCESS, title="t")
        desc = _describe_expected(ar)
        assert "explore" in desc
