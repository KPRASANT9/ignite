"""Tests for the IGNITE L2 Analyzer."""

from __future__ import annotations

import json
from datetime import datetime, timezone
from pathlib import Path

import pytest

from ignite_parser.analyzer import (
    AnalysisResult,
    DependencyGraph,
    EndpointRecord,
    FindingCluster,
    analyze,
)
from ignite_parser.models import (
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
    AgentRole,
)
from ignite_parser.parser import parse_trace


FIXTURE_DIR = Path(__file__).parent / "fixtures"


# --- Helpers ---

def _make_span(
    span_id: str,
    kind: SpanKind = SpanKind.API_CALL,
    target: str = "",
    method: str | None = None,
    url: str | None = None,
    status_code: int | None = None,
    body_schema: dict | None = None,
    request_body: dict | None = None,
    error: str | None = None,
    what_happened: str = "tested",
    what_learned: str = "learned something",
    confidence: Confidence = Confidence.HIGH,
    questions: list[str] | None = None,
    depends_on: list[str] | None = None,
    enables: list[str] | None = None,
    contradicts: list[str] | None = None,
    refines: list[str] | None = None,
    tags: list[str] | None = None,
    sequence: int = 1,
) -> Span:
    return Span(
        span_id=span_id,
        trace_id="trace-1",
        sequence=sequence,
        kind=kind,
        started_at=datetime(2026, 7, 23, 14, 0, tzinfo=timezone.utc),
        interaction=Interaction(
            target=target,
            method=method,
            request=Request(url=url, body=request_body),
            response=Response(
                status_code=status_code,
                body_schema=body_schema,
                error=error,
            ),
        ),
        observation=Observation(
            what_happened=what_happened,
            what_learned=what_learned,
            confidence=confidence,
            questions_raised=questions or [],
        ),
        relationships=Relationships(
            depends_on=depends_on or [],
            enables=enables or [],
            contradicts=contradicts or [],
            refines=refines or [],
        ),
        tags=tags or [],
    )


def _make_finding(
    finding_id: str,
    category: FindingCategory = FindingCategory.PROTOCOL,
    title: str = "Test finding",
    description: str = "A test finding",
    confidence: FindingConfidence = FindingConfidence.CONFIRMED,
    source_spans: list[str] | None = None,
    related_findings: list[str] | None = None,
) -> Finding:
    return Finding(
        finding_id=finding_id,
        trace_id="trace-1",
        source_spans=source_spans or [],
        category=category,
        title=title,
        description=description,
        confidence=confidence,
        related_findings=related_findings or [],
    )


def _make_trace(
    trace_id: str = "trace-1",
    system: str = "plaid",
    spans: list[Span] | None = None,
    findings: list[Finding] | None = None,
    agent_role: AgentRole = AgentRole.EXPLORER,
) -> Trace:
    return Trace(
        schema_version="0.1",
        trace_id=trace_id,
        agent_id="TestAgent",
        agent_role=agent_role,
        system=system,
        started_at=datetime(2026, 7, 23, 14, 0, tzinfo=timezone.utc),
        status=TraceStatus.COMPLETED,
        objective="Test objective",
        spans=spans or [],
        findings=findings or [],
    )


# --- Empty input ---

def test_analyze_empty_traces():
    result = analyze([])
    assert result.endpoint_count == 0
    assert result.cluster_count == 0
    assert result.graph.edge_count == 0
    assert result.coverage.total_traces == 0


# --- Endpoint catalog ---

def test_endpoint_catalog_single_span():
    span = _make_span(
        "s1", target="POST /link/token/create", method="POST",
        url="https://sandbox.plaid.com/link/token/create",
        status_code=200, tags=["link"],
    )
    trace = _make_trace(spans=[span])
    result = analyze([trace])

    assert result.endpoint_count == 1
    ep = result.endpoints["POST POST /link/token/create"]
    assert ep.hit_count == 1
    assert ep.method == "POST"
    assert ep.url == "https://sandbox.plaid.com/link/token/create"
    assert 200 in ep.status_codes
    assert "link" in ep.tags


def test_endpoint_catalog_merges_across_traces():
    span1 = _make_span(
        "s1", target="/accounts/get", method="POST",
        status_code=200, what_learned="Returns account list",
    )
    span2 = _make_span(
        "s2", target="/accounts/get", method="POST",
        status_code=400, error="INVALID_ACCESS_TOKEN",
        what_learned="Requires valid access_token",
    )
    t1 = _make_trace(trace_id="t1", spans=[span1])
    t2 = _make_trace(trace_id="t2", spans=[span2])
    result = analyze([t1, t2])

    assert result.endpoint_count == 1
    ep = result.endpoints["POST /accounts/get"]
    assert ep.hit_count == 2
    assert 200 in ep.status_codes
    assert 400 in ep.status_codes
    assert "INVALID_ACCESS_TOKEN" in ep.error_patterns
    assert len(ep.observations) == 2


def test_endpoint_no_duplicate_status_codes():
    span1 = _make_span("s1", target="/test", method="GET", status_code=200)
    span2 = _make_span("s2", target="/test", method="GET", status_code=200, sequence=2)
    trace = _make_trace(spans=[span1, span2])
    result = analyze([trace])

    ep = result.endpoints["GET /test"]
    assert ep.status_codes == [200]


def test_endpoint_without_method():
    span = _make_span("s1", kind=SpanKind.DOC_READ, target="Plaid API docs — /link/token/create")
    trace = _make_trace(spans=[span])
    result = analyze([trace])

    assert result.endpoint_count == 1
    key = "Plaid API docs — /link/token/create"
    assert key in result.endpoints
    assert result.endpoints[key].method is None


def test_endpoint_collects_questions():
    span = _make_span(
        "s1", target="/test", method="GET",
        questions=["What is the rate limit?", "Does it support pagination?"],
    )
    trace = _make_trace(spans=[span])
    result = analyze([trace])

    ep = result.endpoints["GET /test"]
    assert "What is the rate limit?" in ep.questions


def test_endpoint_merges_schemas():
    schema_v1 = {"type": "object", "properties": {"id": {"type": "string"}}}
    schema_v2 = {"type": "object", "properties": {"id": {"type": "string"}, "name": {"type": "string"}}}

    span1 = _make_span("s1", target="/users", method="GET", body_schema=schema_v1)
    span2 = _make_span("s2", target="/users", method="GET", body_schema=schema_v2, sequence=2)
    trace = _make_trace(spans=[span1, span2])
    result = analyze([trace])

    ep = result.endpoints["GET /users"]
    # Last write wins
    assert ep.response_schema == schema_v2


def test_endpoint_skips_empty_target():
    span = _make_span("s1", target="")
    trace = _make_trace(spans=[span])
    result = analyze([trace])
    assert result.endpoint_count == 0


# --- Dependency graph ---

def test_graph_depends_on():
    span1 = _make_span("s1", target="/a", enables=["s2"])
    span2 = _make_span("s2", target="/b", depends_on=["s1"], sequence=2)
    trace = _make_trace(spans=[span1, span2])
    result = analyze([trace])

    g = result.graph
    assert "s1" in g.nodes
    assert "s2" in g.nodes
    assert g.neighbors("s1", "enables") == ["s2"]
    assert g.predecessors("s2", "depends_on") == []  # depends_on edge is from s2
    assert g.neighbors("s2", "depends_on") == ["s1"]


def test_graph_roots_and_leaves():
    span1 = _make_span("s1", target="/a", enables=["s2"])
    span2 = _make_span("s2", target="/b", sequence=2)
    trace = _make_trace(spans=[span1, span2])
    result = analyze([trace])

    g = result.graph
    # s1 enables s2, so s1 has outgoing, s2 is the target
    # roots = nodes with no incoming edges
    roots = g.roots()
    assert "s1" in roots


def test_graph_contradicts_and_refines():
    span1 = _make_span("s1", target="/a")
    span2 = _make_span("s2", target="/a", contradicts=["s1"], sequence=2)
    span3 = _make_span("s3", target="/a", refines=["s1"], sequence=3)
    trace = _make_trace(spans=[span1, span2, span3])
    result = analyze([trace])

    g = result.graph
    assert g.neighbors("s2", "contradicts") == ["s1"]
    assert g.neighbors("s3", "refines") == ["s1"]


def test_graph_finding_derived_from():
    span = _make_span("s1", target="/a")
    finding = _make_finding("f1", source_spans=["s1"])
    trace = _make_trace(spans=[span], findings=[finding])
    result = analyze([trace])

    g = result.graph
    assert "f1" in g.nodes
    assert g.neighbors("f1", "derived_from") == ["s1"]


def test_graph_finding_related_to():
    finding1 = _make_finding("f1", related_findings=["f2"])
    finding2 = _make_finding("f2", title="Other finding")
    trace = _make_trace(findings=[finding1, finding2])
    result = analyze([trace])

    g = result.graph
    assert g.neighbors("f1", "related_to") == ["f2"]


def test_graph_empty():
    trace = _make_trace()
    result = analyze([trace])
    assert result.graph.edge_count == 0
    assert len(result.graph.nodes) == 0


# --- Finding index ---

def test_finding_clusters_by_category_and_title():
    f1 = _make_finding("f1", category=FindingCategory.AUTH, title="OAuth requires PKCE")
    f2 = _make_finding("f2", category=FindingCategory.AUTH, title="OAuth requires PKCE",
                        confidence=FindingConfidence.CONFIRMED)
    f3 = _make_finding("f3", category=FindingCategory.PROTOCOL, title="Rate limit is 100/min")

    t1 = _make_trace(trace_id="t1", findings=[f1])
    t2 = _make_trace(trace_id="t2", findings=[f2])
    t3 = _make_trace(trace_id="t3", findings=[f3])
    result = analyze([t1, t2, t3])

    assert result.cluster_count == 2
    auth_clusters = [c for c in result.finding_clusters if c.category == FindingCategory.AUTH]
    assert len(auth_clusters) == 1
    assert auth_clusters[0].count == 2
    assert auth_clusters[0].highest_confidence == FindingConfidence.CONFIRMED


def test_finding_cluster_confidence_promotion():
    f1 = _make_finding("f1", title="Test", confidence=FindingConfidence.HYPOTHESIS)
    f2 = _make_finding("f2", title="Test", confidence=FindingConfidence.PROBABLE)
    trace = _make_trace(findings=[f1, f2])
    result = analyze([trace])

    cluster = result.finding_clusters[0]
    assert cluster.highest_confidence == FindingConfidence.PROBABLE


def test_finding_no_findings():
    trace = _make_trace()
    result = analyze([trace])
    assert result.cluster_count == 0


# --- Coverage report ---

def test_coverage_basic():
    span = _make_span("s1", kind=SpanKind.API_CALL, target="/test", method="GET")
    finding = _make_finding("f1", category=FindingCategory.PROTOCOL)
    trace = _make_trace(system="plaid", spans=[span], findings=[finding])
    result = analyze([trace])

    cov = result.coverage
    assert cov.total_traces == 1
    assert cov.total_spans == 1
    assert cov.total_findings == 1
    assert "plaid" in cov.systems_explored
    assert SpanKind.API_CALL in cov.span_kinds_seen
    assert FindingCategory.PROTOCOL in cov.finding_categories_seen
    assert "explorer" in cov.agent_roles_active


def test_coverage_multiple_systems():
    t1 = _make_trace(trace_id="t1", system="plaid")
    t2 = _make_trace(trace_id="t2", system="databricks")
    result = analyze([t1, t2])

    assert result.coverage.systems_explored == {"plaid", "databricks"}


def test_coverage_open_questions_deduped():
    span1 = _make_span("s1", target="/a", questions=["What is the rate limit?"])
    span2 = _make_span("s2", target="/b", questions=["What is the rate limit?", "New question?"], sequence=2)
    trace = _make_trace(spans=[span1, span2])
    result = analyze([trace])

    assert result.coverage.open_questions.count("What is the rate limit?") == 1
    assert "New question?" in result.coverage.open_questions


def test_coverage_low_confidence_flagged():
    span = _make_span(
        "s1", target="/maybe", method="GET",
        confidence=Confidence.LOW,
        what_learned="Might support webhooks",
    )
    trace = _make_trace(spans=[span])
    result = analyze([trace])

    assert len(result.coverage.low_confidence_areas) == 1
    assert "Might support webhooks" in result.coverage.low_confidence_areas[0]


def test_coverage_multiple_agent_roles():
    t1 = _make_trace(trace_id="t1", agent_role=AgentRole.EXPLORER)
    t2 = _make_trace(trace_id="t2", agent_role=AgentRole.AUTH_NAVIGATOR)
    result = analyze([t1, t2])

    assert result.coverage.agent_roles_active == {"explorer", "auth_navigator"}


# --- Integration: from fixture file ---

def test_analyze_from_fixture():
    """Parse the sample fixture through Parser → Analyzer end-to-end."""
    fixture = FIXTURE_DIR / "valid_trace.json"
    with open(fixture) as f:
        data = json.load(f)

    parse_result = parse_trace(data)
    assert parse_result.ok
    assert len(parse_result.traces) == 1

    result = analyze(parse_result.traces)

    # Endpoints
    assert result.endpoint_count == 2  # doc_read + api_call
    assert any("POST" in k for k in result.endpoints)

    # Graph
    assert "span-001" in result.graph.nodes
    assert "span-002" in result.graph.nodes
    assert "finding-001" in result.graph.nodes
    assert result.graph.neighbors("span-001", "enables") == ["span-002"]
    assert result.graph.neighbors("finding-001", "derived_from") == ["span-001", "span-002"]

    # Findings
    assert result.cluster_count == 1
    assert result.finding_clusters[0].category == FindingCategory.PROTOCOL

    # Coverage
    assert result.coverage.total_traces == 1
    assert result.coverage.total_spans == 2
    assert result.coverage.total_findings == 1
    assert "plaid" in result.coverage.systems_explored
    assert len(result.coverage.open_questions) >= 2  # questions from both spans
