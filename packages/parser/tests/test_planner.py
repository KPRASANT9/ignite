"""Tests for the IGNITE L2 Planner — finding-to-action decoder.

Tests verify:
1. Each finding category maps to the correct action kind
2. FSM extraction produces GENERATE_MODEL actions
3. Error patterns produce GENERATE_HANDLER actions
4. Protocol findings produce GENERATE_CLIENT actions
5. Coverage gaps produce EXPLORE actions
6. Low-confidence findings produce VALIDATE actions
7. Topological ordering respects dependencies
8. Integration: V1 traces → Parser → Analyzer → Optimizer → Planner
"""

from __future__ import annotations

import json
import sys
from pathlib import Path
from typing import Optional

import pytest

sys.path.insert(0, str(Path(__file__).parent.parent / "src"))

from ignite_parser.models import (
    FindingCategory,
    FindingConfidence,
    SpanKind,
)
from ignite_parser.analyzer import (
    AnalysisResult,
    CoverageReport,
    EndpointRecord,
    FindingCluster,
    DependencyGraph,
)
from ignite_parser.optimizer import (
    ExtractedFSM,
    FSMState,
    FSMTransition,
    InstitutionCluster,
    MergedFinding,
    OptimizedResult,
    optimize,
)
from ignite_parser.planner import (
    ActionKind,
    ActionPriority,
    ExecutionPlan,
    PlanAction,
    plan,
    _fsm_class_name,
    _reset_counter,
)


# --- Fixtures ---


def _make_finding(
    title: str,
    category: FindingCategory = FindingCategory.PROTOCOL,
    confidence: FindingConfidence = FindingConfidence.CONFIRMED,
    evidence_count: int = 1,
    source_trace_ids: list[str] | None = None,
    tags: set[str] | None = None,
) -> MergedFinding:
    return MergedFinding(
        canonical_title=title,
        category=category,
        description=f"Description of {title}",
        confidence=confidence,
        evidence_count=evidence_count,
        source_trace_ids=source_trace_ids or ["trace-001"],
        source_finding_ids=[f"finding-{title[:8]}"],
        tags=tags or set(),
    )


def _make_fsm(
    name: str,
    states: list[str],
    transitions: list[tuple[str, str]] | None = None,
    initial: list[str] | None = None,
    terminal: list[str] | None = None,
    error: list[str] | None = None,
) -> ExtractedFSM:
    initial = initial or []
    terminal = terminal or []
    error = error or []
    return ExtractedFSM(
        name=name,
        system="plaid",
        states=[
            FSMState(
                name=s,
                is_initial=s in initial,
                is_terminal=s in terminal,
                is_error=s in error,
            )
            for s in states
        ],
        transitions=[
            FSMTransition(from_state=f, to_state=t) for f, t in (transitions or [])
        ],
    )


def _make_optimized(
    findings: list[MergedFinding] | None = None,
    fsms: list[ExtractedFSM] | None = None,
    endpoints: dict[str, EndpointRecord] | None = None,
    span_kinds_seen: set[SpanKind] | None = None,
    open_questions: list[str] | None = None,
) -> OptimizedResult:
    cov = CoverageReport(
        span_kinds_seen=span_kinds_seen or set(),
        open_questions=open_questions or [],
    )
    return OptimizedResult(
        merged_findings=findings or [],
        fsms=fsms or [],
        merged_endpoints=endpoints or {},
        coverage=cov,
        source_analysis_count=1,
    )


# --- Unit tests: FSM → GENERATE_MODEL ---


class TestFSMToModel:
    def test_single_fsm_produces_action(self):
        fsm = _make_fsm(
            "Transaction sync FSM",
            states=["UNSYNCED", "PARTIAL", "FULLY_SYNCED"],
            transitions=[("UNSYNCED", "PARTIAL"), ("PARTIAL", "FULLY_SYNCED")],
            initial=["UNSYNCED"],
        )
        opt = _make_optimized(fsms=[fsm])
        result = plan(opt)

        models = result.actions_by_kind(ActionKind.GENERATE_MODEL)
        assert len(models) == 1
        assert "Transaction sync FSM" in models[0].title
        assert models[0].priority == ActionPriority.HIGH
        assert models[0].source_fsm_name == "Transaction sync FSM"
        assert models[0].metadata["state_count"] == 3
        assert models[0].metadata["transition_count"] == 2

    def test_multiple_fsms_produce_multiple_actions(self):
        fsms = [
            _make_fsm("Sync FSM", ["A", "B"]),
            _make_fsm("Item FSM", ["GOOD", "ERROR", "REMOVED"]),
        ]
        opt = _make_optimized(fsms=fsms)
        result = plan(opt)

        models = result.actions_by_kind(ActionKind.GENERATE_MODEL)
        assert len(models) == 2

    def test_empty_fsm_skipped(self):
        fsm = ExtractedFSM(name="Empty", system="test")
        opt = _make_optimized(fsms=[fsm])
        result = plan(opt)

        models = result.actions_by_kind(ActionKind.GENERATE_MODEL)
        assert len(models) == 0

    def test_fsm_error_states_flagged(self):
        fsm = _make_fsm(
            "Item lifecycle",
            states=["GOOD", "LOGIN_REQUIRED", "REMOVED"],
            error=["LOGIN_REQUIRED"],
            terminal=["REMOVED"],
        )
        opt = _make_optimized(fsms=[fsm])
        result = plan(opt)

        action = result.actions_by_kind(ActionKind.GENERATE_MODEL)[0]
        assert action.metadata["has_error_states"] is True

    def test_fsm_outputs_include_class_names(self):
        fsm = _make_fsm("Transaction sync", ["A", "B"])
        opt = _make_optimized(fsms=[fsm])
        result = plan(opt)

        action = result.actions_by_kind(ActionKind.GENERATE_MODEL)[0]
        assert any("TransactionSync" in o for o in action.outputs)


# --- Unit tests: error_pattern → GENERATE_HANDLER ---


class TestErrorToHandler:
    def test_error_findings_produce_handler(self):
        findings = [
            _make_finding("HTTP 400 for auth failures", FindingCategory.ERROR_PATTERN),
            _make_finding("Error taxonomy has 7 types", FindingCategory.ERROR_PATTERN),
        ]
        opt = _make_optimized(findings=findings)
        result = plan(opt)

        handlers = result.actions_by_kind(ActionKind.GENERATE_HANDLER)
        assert len(handlers) == 1
        assert "2 error patterns" in handlers[0].description
        assert handlers[0].priority == ActionPriority.HIGH

    def test_no_error_findings_no_handler(self):
        findings = [_make_finding("Some protocol thing", FindingCategory.PROTOCOL)]
        opt = _make_optimized(findings=findings)
        result = plan(opt)

        handlers = result.actions_by_kind(ActionKind.GENERATE_HANDLER)
        assert len(handlers) == 0

    def test_single_error_finding_still_produces_handler(self):
        findings = [_make_finding("400 errors", FindingCategory.ERROR_PATTERN)]
        opt = _make_optimized(findings=findings)
        result = plan(opt)

        handlers = result.actions_by_kind(ActionKind.GENERATE_HANDLER)
        assert len(handlers) == 1


# --- Unit tests: protocol → GENERATE_CLIENT ---


class TestProtocolToClient:
    def test_protocol_with_schemas_produces_client(self):
        findings = [_make_finding("Cursor pagination", FindingCategory.PROTOCOL)]
        endpoints = {
            "POST /transactions/sync": EndpointRecord(
                target="POST /transactions/sync",
                method="POST",
                url="https://sandbox.plaid.com/transactions/sync",
                response_schema={"type": "object"},
            ),
        }
        opt = _make_optimized(findings=findings, endpoints=endpoints)
        result = plan(opt)

        clients = result.actions_by_kind(ActionKind.GENERATE_CLIENT)
        assert len(clients) == 1
        assert clients[0].priority == ActionPriority.MEDIUM

    def test_protocol_without_schemas_no_client(self):
        findings = [_make_finding("Cursor pagination", FindingCategory.PROTOCOL)]
        endpoints = {
            "POST /some/endpoint": EndpointRecord(
                target="POST /some/endpoint",
            ),
        }
        opt = _make_optimized(findings=findings, endpoints=endpoints)
        result = plan(opt)

        clients = result.actions_by_kind(ActionKind.GENERATE_CLIENT)
        assert len(clients) == 0

    def test_no_protocol_findings_no_client(self):
        findings = [_make_finding("Some error", FindingCategory.ERROR_PATTERN)]
        opt = _make_optimized(findings=findings)
        result = plan(opt)

        clients = result.actions_by_kind(ActionKind.GENERATE_CLIENT)
        assert len(clients) == 0


# --- Unit tests: coverage gaps → EXPLORE ---


class TestCoverageToExplore:
    def test_missing_span_kinds_produce_explore(self):
        seen = {SpanKind.API_CALL, SpanKind.DOC_READ}
        opt = _make_optimized(span_kinds_seen=seen)
        result = plan(opt)

        explores = result.actions_by_kind(ActionKind.EXPLORE)
        missing_kinds = {a.metadata.get("missing_span_kind") for a in explores if "missing_span_kind" in a.metadata}
        assert "auth_flow" in missing_kinds
        assert "error_probe" in missing_kinds
        assert "state_transition" in missing_kinds
        # api_call and doc_read should NOT be in missing
        assert "api_call" not in missing_kinds
        assert "doc_read" not in missing_kinds

    def test_all_span_kinds_seen_no_explore(self):
        seen = set(SpanKind)
        opt = _make_optimized(span_kinds_seen=seen)
        result = plan(opt)

        explores = [a for a in result.actions_by_kind(ActionKind.EXPLORE) if "missing_span_kind" in a.metadata]
        assert len(explores) == 0

    def test_open_questions_produce_explore(self):
        opt = _make_optimized(
            span_kinds_seen=set(SpanKind),  # No span kind gaps
            open_questions=["Does cursor expire?", "Rate limit for balance/get?"],
        )
        result = plan(opt)

        explores = result.actions_by_kind(ActionKind.EXPLORE)
        assert len(explores) == 1
        assert "2 open questions" in explores[0].title
        assert explores[0].priority == ActionPriority.LOW


# --- Unit tests: low-confidence → VALIDATE ---


class TestLowConfToValidate:
    def test_hypothesis_single_trace_produces_validate(self):
        findings = [
            _make_finding(
                "Maybe cursor expires",
                confidence=FindingConfidence.HYPOTHESIS,
                source_trace_ids=["trace-001"],
            ),
        ]
        opt = _make_optimized(findings=findings)
        result = plan(opt)

        validates = result.actions_by_kind(ActionKind.VALIDATE)
        assert len(validates) == 1
        assert "Maybe cursor expires" in validates[0].title
        assert validates[0].priority == ActionPriority.LOW

    def test_confirmed_not_validated(self):
        findings = [
            _make_finding("Confirmed thing", confidence=FindingConfidence.CONFIRMED),
        ]
        opt = _make_optimized(findings=findings)
        result = plan(opt)

        validates = result.actions_by_kind(ActionKind.VALIDATE)
        assert len(validates) == 0

    def test_hypothesis_multi_trace_not_validated(self):
        findings = [
            _make_finding(
                "Probable thing",
                confidence=FindingConfidence.HYPOTHESIS,
                source_trace_ids=["trace-001", "trace-002"],
            ),
        ]
        opt = _make_optimized(findings=findings)
        result = plan(opt)

        validates = result.actions_by_kind(ActionKind.VALIDATE)
        assert len(validates) == 0


# --- Topological ordering ---


class TestTopologicalOrder:
    def test_respects_dependencies(self):
        ep = ExecutionPlan(actions=[
            PlanAction(action_id="a3", kind=ActionKind.EXPLORE, priority=ActionPriority.LOW,
                       title="C", description="", depends_on=["a2"]),
            PlanAction(action_id="a1", kind=ActionKind.GENERATE_MODEL, priority=ActionPriority.HIGH,
                       title="A", description=""),
            PlanAction(action_id="a2", kind=ActionKind.GENERATE_HANDLER, priority=ActionPriority.HIGH,
                       title="B", description="", depends_on=["a1"]),
        ])
        ordered = ep.topological_order()
        ids = [a.action_id for a in ordered]
        assert ids.index("a1") < ids.index("a2")
        assert ids.index("a2") < ids.index("a3")

    def test_no_dependencies_preserves_order(self):
        ep = ExecutionPlan(actions=[
            PlanAction(action_id="x", kind=ActionKind.EXPLORE, priority=ActionPriority.LOW,
                       title="X", description=""),
            PlanAction(action_id="y", kind=ActionKind.EXPLORE, priority=ActionPriority.LOW,
                       title="Y", description=""),
        ])
        ordered = ep.topological_order()
        assert [a.action_id for a in ordered] == ["x", "y"]


# --- FSM class name utility ---


class TestFSMClassName:
    def test_simple_name(self):
        assert _fsm_class_name("Transaction sync") == "TransactionSync"

    def test_name_with_noise(self):
        name = "Transaction sync has a 3-state FSM: UNSYNCED → PARTIAL → FULLY_SYNCED"
        assert _fsm_class_name(name) == "TransactionSync"

    def test_item_lifecycle(self):
        assert _fsm_class_name("Item lifecycle FSM") == "ItemLifecycle"

    def test_empty_name(self):
        assert _fsm_class_name("") == "UnnamedFSM"

    def test_all_noise(self):
        assert _fsm_class_name("a the is") == "UnnamedFSM"


# --- Plan properties ---


class TestPlanProperties:
    def test_action_count(self):
        ep = ExecutionPlan(actions=[
            PlanAction(action_id="1", kind=ActionKind.EXPLORE, priority=ActionPriority.LOW,
                       title="A", description=""),
            PlanAction(action_id="2", kind=ActionKind.EXPLORE, priority=ActionPriority.LOW,
                       title="B", description=""),
        ])
        assert ep.action_count == 2

    def test_filter_by_kind(self):
        ep = ExecutionPlan(actions=[
            PlanAction(action_id="1", kind=ActionKind.EXPLORE, priority=ActionPriority.LOW,
                       title="A", description=""),
            PlanAction(action_id="2", kind=ActionKind.GENERATE_MODEL, priority=ActionPriority.HIGH,
                       title="B", description=""),
        ])
        assert len(ep.actions_by_kind(ActionKind.EXPLORE)) == 1
        assert len(ep.actions_by_kind(ActionKind.GENERATE_MODEL)) == 1

    def test_filter_by_priority(self):
        ep = ExecutionPlan(actions=[
            PlanAction(action_id="1", kind=ActionKind.EXPLORE, priority=ActionPriority.LOW,
                       title="A", description=""),
            PlanAction(action_id="2", kind=ActionKind.GENERATE_MODEL, priority=ActionPriority.HIGH,
                       title="B", description=""),
        ])
        assert len(ep.actions_by_priority(ActionPriority.HIGH)) == 1
        assert len(ep.actions_by_priority(ActionPriority.LOW)) == 1


# --- Integration: V1 traces → full pipeline → Planner ---


class TestV1Integration:
    """Test the Planner against real V1 trace data."""

    @pytest.fixture
    def v1_optimized(self):
        """Load all V1 traces, run through Parser→Analyzer→Optimizer."""
        from ignite_parser.parser import parse_trace
        from ignite_parser.analyzer import analyze

        trace_dir = Path(__file__).parent.parent.parent.parent / "traces"
        sdk_trace_dir = trace_dir / "explorer"

        all_parsed = []

        # M0: sample trace
        sample = trace_dir / "sample_trace.jsonl"
        if sample.exists():
            with open(sample) as f:
                data = json.loads(f.readline())
            r = parse_trace(data)
            all_parsed.extend(r.traces)

        # M1-M4: SDK-produced traces
        if sdk_trace_dir.exists():
            for trace_file in sorted(sdk_trace_dir.glob("trace-*.json")):
                with open(trace_file) as f:
                    data = json.load(f)
                r = parse_trace(data)
                all_parsed.extend(r.traces)

        if not all_parsed:
            pytest.skip("No V1 trace files found")

        analysis = analyze(all_parsed)
        return optimize([analysis])

    def test_v1_produces_nonempty_plan(self, v1_optimized):
        result = plan(v1_optimized)
        assert result.action_count >= 3

    def test_v1_has_generate_model_actions(self, v1_optimized):
        result = plan(v1_optimized)
        models = result.actions_by_kind(ActionKind.GENERATE_MODEL)
        assert len(models) >= 2, f"Expected ≥2 FSM model actions, got {len(models)}"

    def test_v1_has_generate_handler_action(self, v1_optimized):
        result = plan(v1_optimized)
        handlers = result.actions_by_kind(ActionKind.GENERATE_HANDLER)
        assert len(handlers) >= 1

    def test_v1_has_explore_actions(self, v1_optimized):
        result = plan(v1_optimized)
        explores = result.actions_by_kind(ActionKind.EXPLORE)
        assert len(explores) >= 1  # At least some span kinds missing

    def test_v1_source_counts(self, v1_optimized):
        result = plan(v1_optimized)
        assert result.source_finding_count >= 10
        assert result.source_fsm_count >= 2

    def test_v1_topological_order_valid(self, v1_optimized):
        result = plan(v1_optimized)
        ordered = result.topological_order()
        assert len(ordered) == result.action_count

    def test_v1_no_circular_dependencies(self, v1_optimized):
        result = plan(v1_optimized)
        # In a valid plan, topological_order length == action_count
        ordered = result.topological_order()
        assert len(ordered) == result.action_count

    def test_v1_coverage_gaps_populated(self, v1_optimized):
        result = plan(v1_optimized)
        # V1 has 4/8 span kinds — should have gaps
        assert len(result.coverage_gaps) >= 1

    def test_v1_all_action_ids_unique(self, v1_optimized):
        result = plan(v1_optimized)
        ids = [a.action_id for a in result.actions]
        assert len(ids) == len(set(ids))


# --- Empty input ---


class TestEmptyInput:
    def test_empty_optimized_produces_explore_only(self):
        opt = _make_optimized()
        result = plan(opt)
        # Should produce explore actions for all 8 missing span kinds
        explores = result.actions_by_kind(ActionKind.EXPLORE)
        assert len(explores) == len(SpanKind)
        # No other action types
        assert len(result.actions_by_kind(ActionKind.GENERATE_MODEL)) == 0
        assert len(result.actions_by_kind(ActionKind.GENERATE_HANDLER)) == 0
        assert len(result.actions_by_kind(ActionKind.GENERATE_CLIENT)) == 0
