"""IGNITE L2 Analyzer — Second stage of the Catalyst-style modeling pipeline.

Consumes validated Trace objects from the Parser and produces structured
analysis: endpoint catalog, dependency graph, finding index, and coverage
report. This is where L1 traces become L2 knowledge.
"""

from __future__ import annotations

from collections import defaultdict
from dataclasses import dataclass, field
from typing import Any

from ignite_parser.models import (
    Confidence,
    Finding,
    FindingCategory,
    FindingConfidence,
    Modality,
    Span,
    SpanKind,
    Trace,
)


# --- Analysis output models ---


@dataclass
class SchemaField:
    """A single field in an inferred API schema."""
    name: str
    field_type: str  # "string", "integer", "object", "array", etc.
    required: bool = False
    example: str | None = None


@dataclass
class EndpointRecord:
    """Aggregated knowledge about one API endpoint across all traces."""
    target: str  # e.g. "POST /link/token/create"
    method: str | None = None
    url: str | None = None
    hit_count: int = 0
    span_kinds: set[SpanKind] = field(default_factory=set)
    status_codes: list[int] = field(default_factory=list)
    request_schema: dict[str, Any] | None = None
    response_schema: dict[str, Any] | None = None
    error_patterns: list[str] = field(default_factory=list)
    observations: list[str] = field(default_factory=list)
    questions: list[str] = field(default_factory=list)
    source_spans: list[str] = field(default_factory=list)
    tags: set[str] = field(default_factory=set)


@dataclass
class DependencyEdge:
    """A directed edge in the dependency graph."""
    from_id: str
    to_id: str
    relation: str  # "depends_on", "enables", "contradicts", "refines"


@dataclass
class DependencyGraph:
    """Directed graph of span/finding relationships across traces."""
    nodes: set[str] = field(default_factory=set)
    edges: list[DependencyEdge] = field(default_factory=list)

    @property
    def edge_count(self) -> int:
        return len(self.edges)

    def neighbors(self, node_id: str, relation: str | None = None) -> list[str]:
        """Get outgoing neighbors, optionally filtered by relation type."""
        return [
            e.to_id for e in self.edges
            if e.from_id == node_id and (relation is None or e.relation == relation)
        ]

    def predecessors(self, node_id: str, relation: str | None = None) -> list[str]:
        """Get incoming neighbors, optionally filtered by relation type."""
        return [
            e.from_id for e in self.edges
            if e.to_id == node_id and (relation is None or e.relation == relation)
        ]

    def roots(self) -> set[str]:
        """Nodes with no incoming edges — entry points."""
        targets = {e.to_id for e in self.edges}
        return self.nodes - targets

    def leaves(self) -> set[str]:
        """Nodes with no outgoing edges — terminal points."""
        sources = {e.from_id for e in self.edges}
        return self.nodes - sources


@dataclass
class FindingCluster:
    """Group of related findings across traces, deduplicated by similarity."""
    category: FindingCategory
    title: str
    findings: list[Finding] = field(default_factory=list)
    highest_confidence: FindingConfidence = FindingConfidence.HYPOTHESIS

    @property
    def count(self) -> int:
        return len(self.findings)


@dataclass
class ModalityProfile:
    """Cross-modality feature profile for a single modality."""
    modality: str = ""
    span_count: int = 0
    avg_duration_ms: float = 0.0
    total_duration_ms: int = 0
    success_rate: float = 0.0
    error_rate: float = 0.0
    span_kinds: set[str] = field(default_factory=set)
    unique_targets: set[str] = field(default_factory=set)


@dataclass
class CoverageReport:
    """What has been explored vs. what remains unknown."""
    total_traces: int = 0
    total_spans: int = 0
    total_findings: int = 0
    span_kinds_seen: set[SpanKind] = field(default_factory=set)
    finding_categories_seen: set[FindingCategory] = field(default_factory=set)
    systems_explored: set[str] = field(default_factory=set)
    agent_roles_active: set[str] = field(default_factory=set)
    open_questions: list[str] = field(default_factory=list)
    low_confidence_areas: list[str] = field(default_factory=list)
    modalities_seen: set[str] = field(default_factory=set)


@dataclass
class AnalysisResult:
    """Complete output of the Analyzer stage."""
    endpoints: dict[str, EndpointRecord] = field(default_factory=dict)
    graph: DependencyGraph = field(default_factory=DependencyGraph)
    finding_clusters: list[FindingCluster] = field(default_factory=list)
    coverage: CoverageReport = field(default_factory=CoverageReport)
    modality_profiles: dict[str, ModalityProfile] = field(default_factory=dict)

    @property
    def endpoint_count(self) -> int:
        return len(self.endpoints)

    @property
    def cluster_count(self) -> int:
        return len(self.finding_clusters)


# --- Analyzer ---


def analyze(traces: list[Trace]) -> AnalysisResult:
    """Run all analysis stages on a list of validated traces.

    This is the main entry point. Traces must have passed Parser
    validation — the Analyzer trusts the data is well-formed.
    """
    result = AnalysisResult()

    # Build all views in one pass over the data
    _build_endpoint_catalog(traces, result)
    _build_dependency_graph(traces, result)
    _build_finding_index(traces, result)
    _build_coverage_report(traces, result)
    _build_modality_profiles(traces, result)

    return result


def _build_endpoint_catalog(traces: list[Trace], result: AnalysisResult) -> None:
    """Extract and merge endpoint knowledge from all spans."""
    for trace in traces:
        for span in trace.spans:
            target = span.interaction.target
            if not target:
                continue

            # Normalize key: method + target when method is present
            key = f"{span.interaction.method} {target}" if span.interaction.method else target

            if key not in result.endpoints:
                result.endpoints[key] = EndpointRecord(
                    target=target,
                    method=span.interaction.method,
                    url=span.interaction.request.url,
                )

            ep = result.endpoints[key]
            ep.hit_count += 1
            ep.span_kinds.add(span.kind)
            ep.source_spans.append(span.span_id)
            ep.tags.update(span.tags)

            # Merge URL (first non-None wins)
            if not ep.url and span.interaction.request.url:
                ep.url = span.interaction.request.url

            # Collect status codes
            sc = span.interaction.response.status_code
            if sc is not None and sc not in ep.status_codes:
                ep.status_codes.append(sc)

            # Merge response schema (last write wins — later traces refine)
            if span.interaction.response.body_schema:
                ep.response_schema = span.interaction.response.body_schema

            # Merge request body as schema proxy
            if span.interaction.request.body:
                ep.request_schema = span.interaction.request.body

            # Collect errors
            if span.interaction.response.error:
                err = span.interaction.response.error
                if err not in ep.error_patterns:
                    ep.error_patterns.append(err)

            # Collect observations
            if span.observation.what_learned:
                ep.observations.append(span.observation.what_learned)

            # Collect open questions
            ep.questions.extend(span.observation.questions_raised)


def _build_dependency_graph(traces: list[Trace], result: AnalysisResult) -> None:
    """Build a directed graph from span and finding relationships."""
    graph = result.graph

    for trace in traces:
        for span in trace.spans:
            graph.nodes.add(span.span_id)

            for dep in span.relationships.depends_on:
                graph.edges.append(DependencyEdge(span.span_id, dep, "depends_on"))
                graph.nodes.add(dep)

            for ena in span.relationships.enables:
                graph.edges.append(DependencyEdge(span.span_id, ena, "enables"))
                graph.nodes.add(ena)

            for con in span.relationships.contradicts:
                graph.edges.append(DependencyEdge(span.span_id, con, "contradicts"))
                graph.nodes.add(con)

            for ref in span.relationships.refines:
                graph.edges.append(DependencyEdge(span.span_id, ref, "refines"))
                graph.nodes.add(ref)

        for finding in trace.findings:
            graph.nodes.add(finding.finding_id)
            for sid in finding.source_spans:
                graph.edges.append(DependencyEdge(finding.finding_id, sid, "derived_from"))
                graph.nodes.add(sid)
            for rid in finding.related_findings:
                graph.edges.append(DependencyEdge(finding.finding_id, rid, "related_to"))
                graph.nodes.add(rid)


def _build_finding_index(traces: list[Trace], result: AnalysisResult) -> None:
    """Cluster findings by category, dedup by title similarity."""
    clusters_by_key: dict[tuple[FindingCategory, str], FindingCluster] = {}

    confidence_rank = {
        FindingConfidence.HYPOTHESIS: 0,
        FindingConfidence.PROBABLE: 1,
        FindingConfidence.CONFIRMED: 2,
    }

    for trace in traces:
        for finding in trace.findings:
            key = (finding.category, finding.title)

            if key not in clusters_by_key:
                clusters_by_key[key] = FindingCluster(
                    category=finding.category,
                    title=finding.title,
                    highest_confidence=finding.confidence,
                )

            cluster = clusters_by_key[key]
            cluster.findings.append(finding)

            # Promote confidence to highest seen
            if confidence_rank.get(finding.confidence, 0) > confidence_rank.get(cluster.highest_confidence, 0):
                cluster.highest_confidence = finding.confidence

    result.finding_clusters = list(clusters_by_key.values())


def _build_coverage_report(traces: list[Trace], result: AnalysisResult) -> None:
    """Compute exploration coverage and identify gaps."""
    cov = result.coverage
    cov.total_traces = len(traces)

    seen_questions: set[str] = set()

    for trace in traces:
        cov.systems_explored.add(trace.system)
        cov.agent_roles_active.add(trace.agent_role.value)
        cov.total_spans += len(trace.spans)
        cov.total_findings += len(trace.findings)

        for span in trace.spans:
            cov.span_kinds_seen.add(span.kind)
            cov.modalities_seen.add(span.modality.value)

            # Collect unique open questions
            for q in span.observation.questions_raised:
                if q not in seen_questions:
                    seen_questions.add(q)
                    cov.open_questions.append(q)

            # Flag low-confidence observations
            if span.observation.confidence == Confidence.LOW:
                cov.low_confidence_areas.append(
                    f"{span.kind.value}:{span.interaction.target} — {span.observation.what_learned}"
                )

        for finding in trace.findings:
            cov.finding_categories_seen.add(finding.category)


def _build_modality_profiles(traces: list[Trace], result: AnalysisResult) -> None:
    """Extract cross-modality feature profiles: frequency, timing, success/error rates.

    Groups spans by modality and computes per-modality statistics that the
    Optimizer can use to identify cross-modality patterns and anomalies.
    """
    from ignite_parser.models import ResponseOutcome

    modality_spans: dict[str, list[Span]] = {}

    for trace in traces:
        for span in trace.spans:
            mod = span.modality.value
            if mod not in modality_spans:
                modality_spans[mod] = []
            modality_spans[mod].append(span)

    for mod, spans in modality_spans.items():
        count = len(spans)
        total_dur = sum(s.duration_ms for s in spans)
        avg_dur = total_dur / count if count > 0 else 0.0

        success_count = sum(
            1 for s in spans if s.response_outcome == ResponseOutcome.SUCCESS
        )
        error_count = sum(
            1 for s in spans
            if s.response_outcome in (ResponseOutcome.ERROR, ResponseOutcome.AUTH_FAILURE)
        )

        profile = ModalityProfile(
            modality=mod,
            span_count=count,
            avg_duration_ms=round(avg_dur, 1),
            total_duration_ms=total_dur,
            success_rate=round(success_count / count, 3) if count > 0 else 0.0,
            error_rate=round(error_count / count, 3) if count > 0 else 0.0,
            span_kinds={s.kind.value for s in spans},
            unique_targets={s.interaction.target for s in spans if s.interaction.target},
        )
        result.modality_profiles[mod] = profile
