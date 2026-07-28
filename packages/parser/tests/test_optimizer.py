"""Tests for the IGNITE L2 Optimizer."""

from __future__ import annotations

import json
from datetime import datetime, timezone
from pathlib import Path

import pytest

from ignite_parser.analyzer import (
    AnalysisResult,
    CoverageReport,
    DependencyEdge,
    DependencyGraph,
    EndpointRecord,
    FindingCluster,
    analyze,
)
from ignite_parser.models import (
    AgentRole,
    Confidence,
    Finding,
    FindingCategory,
    FindingConfidence,
    Interaction,
    Observation,
    Relationships,
    Request,
    Response,
    Span,
    SpanKind,
    Trace,
    TraceStatus,
)
from ignite_parser.optimizer import (
    MergedFinding,
    ExtractedFSM,
    InstitutionCluster,
    OptimizedResult,
    optimize,
    TITLE_SIMILARITY_THRESHOLD,
)
from ignite_parser.parser import parse_trace


FIXTURE_DIR = Path(__file__).parent / "fixtures"


# --- Helpers ---

def _make_span(
    span_id: str,
    kind: SpanKind = SpanKind.API_CALL,
    target: str = "",
    method: str | None = None,
    what_learned: str = "learned",
    confidence: Confidence = Confidence.HIGH,
    questions: list[str] | None = None,
    tags: list[str] | None = None,
    sequence: int = 1,
) -> Span:
    return Span(
        span_id=span_id,
        trace_id="trace-1",
        sequence=sequence,
        kind=kind,
        started_at=datetime(2026, 7, 27, 14, 0, tzinfo=timezone.utc),
        interaction=Interaction(
            target=target,
            method=method,
            request=Request(),
            response=Response(),
        ),
        observation=Observation(
            what_happened="tested",
            what_learned=what_learned,
            confidence=confidence,
            questions_raised=questions or [],
        ),
        tags=tags or [],
    )


def _make_finding(
    finding_id: str,
    trace_id: str = "trace-1",
    category: FindingCategory = FindingCategory.PROTOCOL,
    title: str = "Test finding",
    description: str = "A test finding",
    confidence: FindingConfidence = FindingConfidence.CONFIRMED,
    tags: list[str] | None = None,
    source_spans: list[str] | None = None,
) -> Finding:
    return Finding(
        finding_id=finding_id,
        trace_id=trace_id,
        source_spans=source_spans or [],
        category=category,
        title=title,
        description=description,
        confidence=confidence,
        tags=tags or [],
    )


def _make_trace(
    trace_id: str = "trace-1",
    system: str = "plaid",
    spans: list[Span] | None = None,
    findings: list[Finding] | None = None,
) -> Trace:
    return Trace(
        schema_version="0.1",
        trace_id=trace_id,
        agent_id="TestAgent",
        agent_role=AgentRole.EXPLORER,
        system=system,
        started_at=datetime(2026, 7, 27, 14, 0, tzinfo=timezone.utc),
        status=TraceStatus.COMPLETED,
        objective="Test objective",
        spans=spans or [],
        findings=findings or [],
    )


def _analyze_trace(trace: Trace) -> AnalysisResult:
    return analyze([trace])


# --- Empty input ---

def test_optimize_empty():
    result = optimize([])
    assert result.finding_count == 0
    assert result.fsm_count == 0
    assert result.institution_count == 0
    assert result.source_analysis_count == 0


def test_optimize_single_analysis():
    finding = _make_finding("f1", title="Token exchange uses 3 stages")
    trace = _make_trace(findings=[finding])
    analysis = _analyze_trace(trace)
    result = optimize([analysis])

    assert result.finding_count == 1
    assert result.merged_findings[0].canonical_title == "Token exchange uses 3 stages"
    assert result.merged_findings[0].evidence_count == 1
    assert result.source_analysis_count == 1


# --- Cross-trace deduplication ---

def test_duplicate_findings_merged():
    f1 = _make_finding("f1", trace_id="t1", title="OAuth consent expires in 3-18 months")
    f2 = _make_finding("f2", trace_id="t2", title="OAuth consent expires in 3-18 months")
    t1 = _make_trace(trace_id="t1", findings=[f1])
    t2 = _make_trace(trace_id="t2", findings=[f2])
    a1, a2 = _analyze_trace(t1), _analyze_trace(t2)
    result = optimize([a1, a2])

    assert result.finding_count == 1
    mf = result.merged_findings[0]
    assert mf.evidence_count == 2
    assert set(mf.source_trace_ids) == {"t1", "t2"}


def test_similar_titles_merged():
    f1 = _make_finding("f1", trace_id="t1", title="MFA uses 4 typed challenge variants")
    f2 = _make_finding("f2", trace_id="t2", title="MFA uses 4 typed challenge variants with structured responses")
    t1 = _make_trace(trace_id="t1", findings=[f1])
    t2 = _make_trace(trace_id="t2", findings=[f2])
    a1, a2 = _analyze_trace(t1), _analyze_trace(t2)
    result = optimize([a1, a2])

    # Should merge due to high similarity
    assert result.finding_count == 1
    assert result.merged_findings[0].evidence_count == 2


def test_different_findings_not_merged():
    f1 = _make_finding("f1", trace_id="t1", category=FindingCategory.AUTH, title="Token exchange is 3-stage")
    f2 = _make_finding("f2", trace_id="t2", category=FindingCategory.PROTOCOL, title="Rate limit is 100/min")
    t1 = _make_trace(trace_id="t1", findings=[f1])
    t2 = _make_trace(trace_id="t2", findings=[f2])
    a1, a2 = _analyze_trace(t1), _analyze_trace(t2)
    result = optimize([a1, a2])

    assert result.finding_count == 2


def test_different_category_prevents_merge():
    f1 = _make_finding("f1", trace_id="t1", category=FindingCategory.AUTH, title="Same title here")
    f2 = _make_finding("f2", trace_id="t2", category=FindingCategory.PROTOCOL, title="Same title here")
    t1 = _make_trace(trace_id="t1", findings=[f1])
    t2 = _make_trace(trace_id="t2", findings=[f2])
    a1, a2 = _analyze_trace(t1), _analyze_trace(t2)
    result = optimize([a1, a2])

    assert result.finding_count == 2


# --- Confidence promotion ---

def test_confidence_promoted_by_evidence():
    """Findings seen in 3+ traces get promoted to confirmed."""
    findings = [
        _make_finding(f"f{i}", trace_id=f"t{i}", title="Same finding",
                       confidence=FindingConfidence.HYPOTHESIS)
        for i in range(3)
    ]
    traces = [_make_trace(trace_id=f"t{i}", findings=[findings[i]]) for i in range(3)]
    analyses = [_analyze_trace(t) for t in traces]
    result = optimize(analyses)

    assert result.finding_count == 1
    assert result.merged_findings[0].confidence == FindingConfidence.CONFIRMED


def test_confidence_promoted_to_probable():
    """Findings seen in 2 traces get promoted from hypothesis to probable."""
    f1 = _make_finding("f1", trace_id="t1", title="Test", confidence=FindingConfidence.HYPOTHESIS)
    f2 = _make_finding("f2", trace_id="t2", title="Test", confidence=FindingConfidence.HYPOTHESIS)
    t1 = _make_trace(trace_id="t1", findings=[f1])
    t2 = _make_trace(trace_id="t2", findings=[f2])
    result = optimize([_analyze_trace(t1), _analyze_trace(t2)])

    assert result.merged_findings[0].confidence == FindingConfidence.PROBABLE


def test_existing_confirmed_not_demoted():
    f1 = _make_finding("f1", trace_id="t1", title="Test", confidence=FindingConfidence.CONFIRMED)
    t1 = _make_trace(trace_id="t1", findings=[f1])
    result = optimize([_analyze_trace(t1)])

    assert result.merged_findings[0].confidence == FindingConfidence.CONFIRMED


# --- Institution clustering ---

def test_institution_extracted_from_description():
    f1 = _make_finding("f1", title="Chase invalidates Items on new OAuth",
                        description="Chase invalidates old Items when new OAuth creates different account sets")
    t1 = _make_trace(findings=[f1])
    result = optimize([_analyze_trace(t1)])

    institutions = {c.institution for c in result.institution_clusters}
    assert "chase" in institutions


def test_multiple_institutions_in_one_finding():
    f1 = _make_finding("f1", title="Consent expiration varies",
                        description="3 months at Brex, 18 months at USAA, 12 months at Chase")
    t1 = _make_trace(findings=[f1])
    result = optimize([_analyze_trace(t1)])

    institutions = {c.institution for c in result.institution_clusters}
    assert "brex" in institutions
    assert "usaa" in institutions
    assert "chase" in institutions


def test_generic_findings_get_generic_cluster():
    f1 = _make_finding("f1", title="Token has 4hr TTL",
                        description="All link tokens expire after 4 hours")
    t1 = _make_trace(findings=[f1])
    result = optimize([_analyze_trace(t1)])

    institutions = {c.institution for c in result.institution_clusters}
    assert "_generic" in institutions


# --- FSM extraction ---

def test_fsm_extracted_from_state_machine_finding():
    f1 = _make_finding(
        "f1",
        category=FindingCategory.STATE_MACHINE,
        title="Item lifecycle FSM",
        description="Items transition from HEALTHY to error states (ITEM_LOGIN_REQUIRED, "
                    "OAUTH_INVALID_TOKEN, OAUTH_CONSENT_EXPIRED). "
                    "HEALTHY → ITEM_LOGIN_REQUIRED via credential failure. "
                    "All errors converge on Link Update Mode for recovery.",
    )
    t1 = _make_trace(findings=[f1])
    result = optimize([_analyze_trace(t1)])

    assert result.fsm_count == 1
    fsm = result.fsms[0]
    assert fsm.name == "Item lifecycle FSM"
    # Should find states from ALL_CAPS patterns
    state_names = {s.name for s in fsm.states}
    assert "HEALTHY" in state_names
    assert "ITEM_LOGIN_REQUIRED" in state_names
    assert "OAUTH_INVALID_TOKEN" in state_names


def test_fsm_classifies_error_states():
    f1 = _make_finding(
        "f1",
        category=FindingCategory.STATE_MACHINE,
        title="Auth FSM",
        description="States: ACTIVE, EXPIRED, REVOKED, FAILED. ACTIVE → EXPIRED after timeout.",
    )
    t1 = _make_trace(findings=[f1])
    result = optimize([_analyze_trace(t1)])

    fsm = result.fsms[0]
    state_map = {s.name: s for s in fsm.states}
    assert state_map["EXPIRED"].is_error
    assert state_map["FAILED"].is_error
    assert state_map["ACTIVE"].is_initial


def test_fsm_extracts_transitions():
    f1 = _make_finding(
        "f1",
        category=FindingCategory.STATE_MACHINE,
        title="Token FSM",
        description="PENDING → ACTIVE on verification. ACTIVE → EXPIRED on timeout.",
    )
    t1 = _make_trace(findings=[f1])
    result = optimize([_analyze_trace(t1)])

    fsm = result.fsms[0]
    assert fsm.transition_count >= 2
    from_to = [(t.from_state, t.to_state) for t in fsm.transitions]
    assert ("PENDING", "ACTIVE") in from_to
    assert ("ACTIVE", "EXPIRED") in from_to


def test_no_fsm_for_non_state_machine_findings():
    f1 = _make_finding("f1", category=FindingCategory.AUTH, title="Auth uses OAuth")
    t1 = _make_trace(findings=[f1])
    result = optimize([_analyze_trace(t1)])

    assert result.fsm_count == 0


# --- Endpoint merging ---

def test_endpoints_merged_across_analyses():
    span1 = _make_span("s1", target="/test", method="GET", tags=["v1"])
    span2 = _make_span("s2", target="/test", method="GET", tags=["v2"])
    t1 = _make_trace(trace_id="t1", spans=[span1])
    t2 = _make_trace(trace_id="t2", spans=[span2])
    result = optimize([_analyze_trace(t1), _analyze_trace(t2)])

    assert len(result.merged_endpoints) == 1
    ep = list(result.merged_endpoints.values())[0]
    assert ep.hit_count == 2
    assert "v1" in ep.tags
    assert "v2" in ep.tags


def test_endpoint_questions_deduped():
    span1 = _make_span("s1", target="/test", method="GET", questions=["Rate limit?"])
    span2 = _make_span("s2", target="/test", method="GET", questions=["Rate limit?", "Auth needed?"])
    t1 = _make_trace(trace_id="t1", spans=[span1])
    t2 = _make_trace(trace_id="t2", spans=[span2])
    result = optimize([_analyze_trace(t1), _analyze_trace(t2)])

    ep = list(result.merged_endpoints.values())[0]
    assert ep.questions.count("Rate limit?") == 1
    assert "Auth needed?" in ep.questions


# --- Coverage merging ---

def test_coverage_aggregated():
    span1 = _make_span("s1", kind=SpanKind.API_CALL, target="/a")
    span2 = _make_span("s2", kind=SpanKind.DOC_READ, target="/b")
    t1 = _make_trace(trace_id="t1", system="plaid", spans=[span1])
    t2 = _make_trace(trace_id="t2", system="databricks", spans=[span2])
    result = optimize([_analyze_trace(t1), _analyze_trace(t2)])

    assert result.coverage.total_traces == 2
    assert result.coverage.total_spans == 2
    assert result.coverage.systems_explored == {"plaid", "databricks"}
    assert SpanKind.API_CALL in result.coverage.span_kinds_seen
    assert SpanKind.DOC_READ in result.coverage.span_kinds_seen


def test_coverage_questions_deduped():
    span1 = _make_span("s1", target="/a", questions=["Q1"])
    span2 = _make_span("s2", target="/b", questions=["Q1", "Q2"])
    t1 = _make_trace(trace_id="t1", spans=[span1])
    t2 = _make_trace(trace_id="t2", spans=[span2])
    result = optimize([_analyze_trace(t1), _analyze_trace(t2)])

    assert result.coverage.open_questions.count("Q1") == 1
    assert "Q2" in result.coverage.open_questions


# --- Graph merging ---

def test_graphs_merged():
    span1 = _make_span("s1", target="/a")
    span2 = _make_span("s2", target="/b")
    f1 = _make_finding("f1", trace_id="t1", source_spans=["s1"])
    f2 = _make_finding("f2", trace_id="t2", title="Other", source_spans=["s2"])
    t1 = _make_trace(trace_id="t1", spans=[span1], findings=[f1])
    t2 = _make_trace(trace_id="t2", spans=[span2], findings=[f2])
    result = optimize([_analyze_trace(t1), _analyze_trace(t2)])

    assert "s1" in result.merged_graph.nodes
    assert "s2" in result.merged_graph.nodes
    assert "f1" in result.merged_graph.nodes
    assert "f2" in result.merged_graph.nodes


# --- Integration: real fixture ---

def test_optimize_from_fixture():
    """Full pipeline: fixture → Parser → Analyzer → Optimizer."""
    fixture = FIXTURE_DIR / "valid_trace.json"
    with open(fixture) as f:
        data = json.load(f)

    parse_result = parse_trace(data)
    assert parse_result.ok

    analysis = analyze(parse_result.traces)
    result = optimize([analysis])

    assert result.finding_count >= 1
    assert result.source_analysis_count == 1
    assert result.coverage.total_traces == 1
    assert len(result.merged_endpoints) > 0


def test_optimize_multiple_copies_of_fixture():
    """Same trace analyzed twice — findings should deduplicate."""
    fixture = FIXTURE_DIR / "valid_trace.json"
    with open(fixture) as f:
        data = json.load(f)

    parse_result = parse_trace(data)
    analysis = analyze(parse_result.traces)

    # Optimize two copies of the same analysis
    result = optimize([analysis, analysis])

    # Findings should merge (same title, same category)
    # Evidence count should be 2 for each merged finding
    for mf in result.merged_findings:
        assert mf.evidence_count >= 2
