"""Tests for the IGNITE L2 Executor — plan action dispatcher.

Tests verify:
1. Each action kind dispatches to the correct handler
2. GENERATE_MODEL produces valid Python with FSM state enum + transitions
3. GENERATE_HANDLER produces error handler with recovery routing
4. GENERATE_CLIENT produces typed client methods
5. EXPLORE and VALIDATE produce structured directives
6. Dependency ordering is respected (skips actions with unmet deps)
7. Integration: OptimizedResult -> plan -> execute -> valid outputs
"""

from __future__ import annotations

import ast
import json
import sys
from pathlib import Path

import pytest

sys.path.insert(0, str(Path(__file__).parent.parent / "src"))

from ignite_parser.models import FindingCategory, FindingConfidence, SpanKind
from ignite_parser.analyzer import CoverageReport, DependencyGraph, EndpointRecord
from ignite_parser.optimizer import (
    ExtractedFSM,
    FSMState,
    FSMTransition,
    MergedFinding,
    OptimizedResult,
)
from ignite_parser.planner import (
    ActionKind,
    ActionPriority,
    ExecutionPlan,
    PlanAction,
    plan,
    _reset_counter,
)
from ignite_parser.executor import (
    ActionStatus,
    ActionResult,
    ExecutionResult,
    execute,
    _generate_fsm_code,
    _generate_error_handler_code,
    _generate_client_code,
    _fsm_class_name,
    _to_snake_case,
    _sanitize_identifier,
)


# --- Fixtures ---


def _make_optimized_with_fsm() -> OptimizedResult:
    """Create an OptimizedResult with an FSM for testing."""
    return OptimizedResult(
        merged_findings=[
            MergedFinding(
                canonical_title="Plaid returns HTTP 400 for all client errors",
                category=FindingCategory.ERROR_PATTERN,
                description="All error types return 400",
                confidence=FindingConfidence.CONFIRMED,
                evidence_count=3,
                source_trace_ids=["t1", "t2"],
                source_finding_ids=["f1", "f2"],
            ),
            MergedFinding(
                canonical_title="Link token exchange uses client_id + secret",
                category=FindingCategory.PROTOCOL,
                description="Auth protocol",
                confidence=FindingConfidence.CONFIRMED,
                evidence_count=1,
                source_trace_ids=["t1"],
                source_finding_ids=["f3"],
            ),
        ],
        fsms=[
            ExtractedFSM(
                name="Transaction sync has a 3-state FSM",
                system="plaid",
                states=[
                    FSMState(name="UNSYNCED", is_initial=True),
                    FSMState(name="PARTIAL"),
                    FSMState(name="FULLY_SYNCED", is_terminal=True),
                ],
                transitions=[
                    FSMTransition(from_state="UNSYNCED", to_state="PARTIAL", trigger="first_sync"),
                    FSMTransition(from_state="PARTIAL", to_state="FULLY_SYNCED", trigger="complete"),
                    FSMTransition(from_state="FULLY_SYNCED", to_state="PARTIAL", trigger="new_data"),
                ],
            ),
        ],
        merged_endpoints={
            "POST /link/token/create": EndpointRecord(
                target="POST /link/token/create",
                method="POST",
                url="https://sandbox.plaid.com/link/token/create",
                hit_count=2,
                response_schema={"type": "object", "properties": {"link_token": {"type": "string"}}},
            ),
        },
        coverage=CoverageReport(
            total_traces=2,
            total_spans=10,
            span_kinds_seen={SpanKind.API_CALL, SpanKind.DOC_READ, SpanKind.ERROR_PROBE, SpanKind.STATE_TRANSITION},
        ),
    )


# --- Unit tests ---


class TestSanitizeIdentifier:
    def test_normal_name(self):
        assert _sanitize_identifier("ACTIVE") == "ACTIVE"

    def test_with_spaces(self):
        assert _sanitize_identifier("fully synced") == "FULLY_SYNCED"

    def test_with_special_chars(self):
        assert _sanitize_identifier("login-required") == "LOGIN_REQUIRED"

    def test_starts_with_digit(self):
        assert _sanitize_identifier("3state") == "STATE_3STATE"

    def test_empty(self):
        assert _sanitize_identifier("") == "UNKNOWN"


class TestToSnakeCase:
    def test_pascal(self):
        assert _to_snake_case("TransactionSync") == "transaction_sync"

    def test_single_word(self):
        assert _to_snake_case("Item") == "item"


class TestGenerateFSMCode:
    def test_produces_valid_python(self):
        fsm = ExtractedFSM(
            name="Test FSM",
            system="test",
            states=[
                FSMState(name="INITIAL", is_initial=True),
                FSMState(name="ACTIVE"),
                FSMState(name="DONE", is_terminal=True),
            ],
            transitions=[
                FSMTransition(from_state="INITIAL", to_state="ACTIVE", trigger="start"),
                FSMTransition(from_state="ACTIVE", to_state="DONE", trigger="finish"),
            ],
        )
        code = _generate_fsm_code(fsm, "TestFSM")
        # Must parse without syntax errors
        ast.parse(code)

    def test_includes_state_enum(self):
        fsm = ExtractedFSM(
            name="Simple",
            system="test",
            states=[FSMState(name="A", is_initial=True), FSMState(name="B", is_terminal=True)],
            transitions=[FSMTransition(from_state="A", to_state="B")],
        )
        code = _generate_fsm_code(fsm, "Simple")
        assert "class SimpleState" in code
        assert 'A = "a"' in code
        assert 'B = "b"' in code

    def test_includes_transition_validator(self):
        fsm = ExtractedFSM(
            name="Val",
            system="test",
            states=[FSMState(name="X", is_initial=True), FSMState(name="Y")],
            transitions=[FSMTransition(from_state="X", to_state="Y", trigger="go")],
        )
        code = _generate_fsm_code(fsm, "Val")
        assert "validate_transition" in code
        assert "def transition" in code


class TestGenerateErrorHandlerCode:
    def test_produces_valid_python(self):
        findings = [
            MergedFinding(
                canonical_title="HTTP 400 for all errors",
                category=FindingCategory.ERROR_PATTERN,
                description="desc",
                confidence=FindingConfidence.CONFIRMED,
            ),
        ]
        code = _generate_error_handler_code(findings)
        ast.parse(code)

    def test_includes_recovery_enum(self):
        findings = [
            MergedFinding(
                canonical_title="Rate limit exceeded",
                category=FindingCategory.ERROR_PATTERN,
                description="desc",
                confidence=FindingConfidence.CONFIRMED,
            ),
        ]
        code = _generate_error_handler_code(findings)
        assert "class RecoveryAction" in code
        assert "RETRY" in code
        assert "def handle_error" in code


class TestGenerateClientCode:
    def test_produces_valid_python(self):
        endpoints = {
            "POST /test": EndpointRecord(
                target="POST /test",
                method="POST",
                url="/test",
                hit_count=1,
                response_schema={"type": "object"},
            ),
        }
        code = _generate_client_code(endpoints)
        ast.parse(code)

    def test_includes_client_class(self):
        endpoints = {
            "GET /items": EndpointRecord(
                target="GET /items",
                method="GET",
                url="/items",
                hit_count=3,
                response_schema={"type": "array"},
            ),
        }
        code = _generate_client_code(endpoints)
        assert "class GeneratedClient" in code


# --- Integration tests ---


class TestExecute:
    def test_executes_all_action_kinds(self, tmp_path):
        optimized = _make_optimized_with_fsm()
        _reset_counter()
        ep = plan(optimized)

        result = execute(ep, optimized, output_dir=tmp_path)

        assert result.total == ep.action_count
        assert result.succeeded > 0
        assert result.failed == 0
        # Every action should have a result
        assert len(result.results) == ep.action_count

    def test_generate_model_creates_file(self, tmp_path):
        optimized = _make_optimized_with_fsm()
        _reset_counter()
        ep = plan(optimized)

        result = execute(ep, optimized, output_dir=tmp_path)

        model_actions = [r for r in result.results if r.kind == ActionKind.GENERATE_MODEL]
        assert len(model_actions) > 0
        for ma in model_actions:
            assert ma.status == ActionStatus.SUCCESS
            assert len(ma.generated_files) > 0
            for f in ma.generated_files:
                assert Path(f).exists()
                # Validate the generated Python
                code = Path(f).read_text()
                ast.parse(code)

    def test_generate_handler_creates_file(self, tmp_path):
        optimized = _make_optimized_with_fsm()
        _reset_counter()
        ep = plan(optimized)

        result = execute(ep, optimized, output_dir=tmp_path)

        handler_actions = [r for r in result.results if r.kind == ActionKind.GENERATE_HANDLER]
        assert len(handler_actions) > 0
        for ha in handler_actions:
            assert ha.status == ActionStatus.SUCCESS
            for f in ha.generated_files:
                assert Path(f).exists()

    def test_explore_creates_directive(self, tmp_path):
        optimized = _make_optimized_with_fsm()
        _reset_counter()
        ep = plan(optimized)

        result = execute(ep, optimized, output_dir=tmp_path)

        explore_actions = [r for r in result.results if r.kind == ActionKind.EXPLORE]
        assert len(explore_actions) > 0
        for ea in explore_actions:
            assert ea.status == ActionStatus.SUCCESS
            for f in ea.generated_files:
                assert Path(f).exists()
                data = json.loads(Path(f).read_text())
                assert data["directive"] == "explore"

    def test_skips_actions_with_unmet_deps(self, tmp_path):
        ep = ExecutionPlan(actions=[
            PlanAction(
                action_id="a1",
                kind=ActionKind.EXPLORE,
                priority=ActionPriority.HIGH,
                title="Dep action",
                description="Test dep",
                depends_on=["nonexistent"],
            ),
        ])
        optimized = OptimizedResult()

        result = execute(ep, optimized, output_dir=tmp_path)
        assert result.skipped == 1
        assert result.results[0].status == ActionStatus.SKIPPED

    def test_success_rate(self, tmp_path):
        optimized = _make_optimized_with_fsm()
        _reset_counter()
        ep = plan(optimized)

        result = execute(ep, optimized, output_dir=tmp_path)

        assert result.success_rate > 0.0
        assert result.success_rate <= 1.0

    def test_empty_plan(self, tmp_path):
        ep = ExecutionPlan()
        optimized = OptimizedResult()
        result = execute(ep, optimized, output_dir=tmp_path)
        assert result.total == 0
        assert result.succeeded == 0
        assert result.success_rate == 0.0
