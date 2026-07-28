"""IGNITE L2 Optimizer — Fourth stage of the Catalyst-style modeling pipeline.

Consumes multiple AnalysisResult objects (one per trace) and produces a
compressed, deduplicated OptimizedResult. This is where cross-trace
intelligence emerges: duplicate findings merge, patterns get scored by
frequency, state machines are extracted from related spans, and findings
cluster by institution.

Pipeline: Parser → Analyzer → **Optimizer** → Planner
"""

from __future__ import annotations

from collections import defaultdict
from dataclasses import dataclass, field
from difflib import SequenceMatcher
from typing import Any

from ignite_parser.analyzer import (
    AnalysisResult,
    DependencyEdge,
    DependencyGraph,
    EndpointRecord,
    FindingCluster,
    CoverageReport,
)
from ignite_parser.models import (
    Confidence,
    Finding,
    FindingCategory,
    FindingConfidence,
    Span,
    SpanKind,
    Trace,
)


# --- Optimizer output models ---


@dataclass
class MergedFinding:
    """A finding deduplicated across traces with aggregated evidence."""
    canonical_title: str
    category: FindingCategory
    description: str
    confidence: FindingConfidence
    evidence_count: int = 0
    source_trace_ids: list[str] = field(default_factory=list)
    source_finding_ids: list[str] = field(default_factory=list)
    tags: set[str] = field(default_factory=set)
    institution_tags: set[str] = field(default_factory=set)


@dataclass
class FSMState:
    """A state in an extracted finite state machine."""
    name: str
    is_initial: bool = False
    is_terminal: bool = False
    is_error: bool = False


@dataclass
class FSMTransition:
    """A transition between states in an extracted FSM."""
    from_state: str
    to_state: str
    trigger: str = ""
    conditions: list[str] = field(default_factory=list)


@dataclass
class ExtractedFSM:
    """A finite state machine extracted from state_transition spans."""
    name: str
    system: str
    states: list[FSMState] = field(default_factory=list)
    transitions: list[FSMTransition] = field(default_factory=list)
    source_spans: list[str] = field(default_factory=list)

    @property
    def state_count(self) -> int:
        return len(self.states)

    @property
    def transition_count(self) -> int:
        return len(self.transitions)


@dataclass
class InstitutionCluster:
    """Findings grouped by institution/provider."""
    institution: str
    findings: list[MergedFinding] = field(default_factory=list)

    @property
    def count(self) -> int:
        return len(self.findings)


@dataclass
class OptimizedResult:
    """Complete output of the Optimizer stage."""
    merged_findings: list[MergedFinding] = field(default_factory=list)
    institution_clusters: list[InstitutionCluster] = field(default_factory=list)
    fsms: list[ExtractedFSM] = field(default_factory=list)
    merged_endpoints: dict[str, EndpointRecord] = field(default_factory=dict)
    merged_graph: DependencyGraph = field(default_factory=DependencyGraph)
    coverage: CoverageReport = field(default_factory=CoverageReport)
    source_analysis_count: int = 0

    @property
    def finding_count(self) -> int:
        return len(self.merged_findings)

    @property
    def fsm_count(self) -> int:
        return len(self.fsms)

    @property
    def institution_count(self) -> int:
        return len(self.institution_clusters)


# --- Similarity threshold for finding deduplication ---

TITLE_SIMILARITY_THRESHOLD = 0.70


# --- Optimizer ---


def optimize(analyses: list[AnalysisResult]) -> OptimizedResult:
    """Compress and deduplicate multiple AnalysisResults into an OptimizedResult.

    This is the main entry point. Each AnalysisResult typically comes from
    one trace's Parser→Analyzer pass. The Optimizer merges them into a
    unified, deduplicated view.
    """
    result = OptimizedResult(source_analysis_count=len(analyses))

    _merge_findings(analyses, result)
    _cluster_by_institution(result)
    _merge_endpoints(analyses, result)
    _merge_graphs(analyses, result)
    _merge_coverage(analyses, result)
    _extract_fsms(analyses, result)

    return result


def _merge_findings(analyses: list[AnalysisResult], result: OptimizedResult) -> None:
    """Deduplicate findings across traces by title similarity within category."""
    merged: list[MergedFinding] = []

    confidence_rank = {
        FindingConfidence.HYPOTHESIS: 0,
        FindingConfidence.PROBABLE: 1,
        FindingConfidence.CONFIRMED: 2,
    }

    for analysis in analyses:
        for cluster in analysis.finding_clusters:
            for finding in cluster.findings:
                # Try to merge with an existing merged finding
                match = _find_similar_merged(finding, merged)
                if match is not None:
                    mf = merged[match]
                    mf.evidence_count += 1
                    if finding.trace_id not in mf.source_trace_ids:
                        mf.source_trace_ids.append(finding.trace_id)
                    mf.source_finding_ids.append(finding.finding_id)
                    mf.tags.update(finding.tags)
                    # Promote confidence
                    if confidence_rank.get(finding.confidence, 0) > confidence_rank.get(mf.confidence, 0):
                        mf.confidence = finding.confidence
                    # Use longer description
                    if len(finding.description) > len(mf.description):
                        mf.description = finding.description
                else:
                    # New merged finding
                    mf = MergedFinding(
                        canonical_title=finding.title,
                        category=finding.category,
                        description=finding.description,
                        confidence=finding.confidence,
                        evidence_count=1,
                        source_trace_ids=[finding.trace_id],
                        source_finding_ids=[finding.finding_id],
                        tags=set(finding.tags),
                    )
                    merged.append(mf)

    # Score by evidence: more traces = higher confidence
    for mf in merged:
        trace_count = len(set(mf.source_trace_ids))
        if trace_count >= 3 and mf.confidence != FindingConfidence.CONFIRMED:
            mf.confidence = FindingConfidence.CONFIRMED
        elif trace_count >= 2 and mf.confidence == FindingConfidence.HYPOTHESIS:
            mf.confidence = FindingConfidence.PROBABLE

    result.merged_findings = merged


def _find_similar_merged(finding: Finding, merged: list[MergedFinding]) -> int | None:
    """Find the index of a merged finding similar to this one, or None."""
    for i, mf in enumerate(merged):
        if mf.category != finding.category:
            continue
        similarity = SequenceMatcher(None, mf.canonical_title.lower(), finding.title.lower()).ratio()
        if similarity >= TITLE_SIMILARITY_THRESHOLD:
            return i
    return None


def _cluster_by_institution(result: OptimizedResult) -> None:
    """Group merged findings by institution tags."""
    clusters: dict[str, InstitutionCluster] = {}

    # Known institution keywords to extract from tags and descriptions
    for mf in result.merged_findings:
        institutions = _extract_institutions(mf)
        if not institutions:
            institutions = {"_generic"}

        mf.institution_tags = institutions

        for inst in institutions:
            if inst not in clusters:
                clusters[inst] = InstitutionCluster(institution=inst)
            clusters[inst].findings.append(mf)

    result.institution_clusters = [c for c in clusters.values() if c.institution != "_generic"]
    # Add generic cluster at the end if it has findings
    generic = clusters.get("_generic")
    if generic:
        result.institution_clusters.append(generic)


# Common financial institution names for extraction
_KNOWN_INSTITUTIONS = {
    "chase", "wells fargo", "bank of america", "bofa", "citi", "citibank",
    "usaa", "brex", "charles schwab", "schwab", "us bank",
    "hdfc", "icici", "sbi", "axis", "kotak",
    "finvu", "anumati", "setu",
}


def _extract_institutions(mf: MergedFinding) -> set[str]:
    """Extract institution names from finding tags and description."""
    found: set[str] = set()
    search_text = (mf.description + " " + " ".join(mf.tags)).lower()
    for inst in _KNOWN_INSTITUTIONS:
        if inst in search_text:
            found.add(inst)
    return found


def _merge_endpoints(analyses: list[AnalysisResult], result: OptimizedResult) -> None:
    """Merge endpoint catalogs across analyses."""
    for analysis in analyses:
        for key, ep in analysis.endpoints.items():
            if key not in result.merged_endpoints:
                result.merged_endpoints[key] = EndpointRecord(
                    target=ep.target,
                    method=ep.method,
                    url=ep.url,
                )

            merged_ep = result.merged_endpoints[key]
            merged_ep.hit_count += ep.hit_count
            merged_ep.span_kinds.update(ep.span_kinds)
            merged_ep.tags.update(ep.tags)

            if not merged_ep.url and ep.url:
                merged_ep.url = ep.url

            for sc in ep.status_codes:
                if sc not in merged_ep.status_codes:
                    merged_ep.status_codes.append(sc)

            for err in ep.error_patterns:
                if err not in merged_ep.error_patterns:
                    merged_ep.error_patterns.append(err)

            merged_ep.observations.extend(ep.observations)
            merged_ep.source_spans.extend(ep.source_spans)

            # Deduplicate questions
            seen_q = set(merged_ep.questions)
            for q in ep.questions:
                if q not in seen_q:
                    merged_ep.questions.append(q)
                    seen_q.add(q)

            # Schema: last write wins (later analyses refine)
            if ep.request_schema:
                merged_ep.request_schema = ep.request_schema
            if ep.response_schema:
                merged_ep.response_schema = ep.response_schema


def _merge_graphs(analyses: list[AnalysisResult], result: OptimizedResult) -> None:
    """Merge dependency graphs across analyses."""
    graph = result.merged_graph
    for analysis in analyses:
        graph.nodes.update(analysis.graph.nodes)
        graph.edges.extend(analysis.graph.edges)


def _merge_coverage(analyses: list[AnalysisResult], result: OptimizedResult) -> None:
    """Merge coverage reports across analyses."""
    cov = result.coverage
    cov.total_traces = sum(a.coverage.total_traces for a in analyses)
    cov.total_spans = sum(a.coverage.total_spans for a in analyses)
    cov.total_findings = sum(a.coverage.total_findings for a in analyses)

    seen_questions: set[str] = set()

    for analysis in analyses:
        cov.span_kinds_seen.update(analysis.coverage.span_kinds_seen)
        cov.finding_categories_seen.update(analysis.coverage.finding_categories_seen)
        cov.systems_explored.update(analysis.coverage.systems_explored)
        cov.agent_roles_active.update(analysis.coverage.agent_roles_active)
        cov.low_confidence_areas.extend(analysis.coverage.low_confidence_areas)

        for q in analysis.coverage.open_questions:
            if q not in seen_questions:
                seen_questions.add(q)
                cov.open_questions.append(q)


def _extract_fsms(analyses: list[AnalysisResult], result: OptimizedResult) -> None:
    """Extract finite state machines from state_machine findings.

    Looks for findings with category=state_machine and extracts state/transition
    information from their descriptions and evidence.
    """
    for mf in result.merged_findings:
        if mf.category != FindingCategory.STATE_MACHINE:
            continue

        fsm = _parse_fsm_from_finding(mf)
        if fsm and fsm.state_count > 0:
            result.fsms.append(fsm)


def _parse_fsm_from_finding(mf: MergedFinding) -> ExtractedFSM | None:
    """Parse state and transition info from a state_machine finding's description."""
    fsm = ExtractedFSM(
        name=mf.canonical_title,
        system="",
        source_spans=mf.source_finding_ids,
    )

    desc_lower = mf.description.lower()

    # Extract states from common patterns in descriptions
    # Look for words following "states:" or "error states" or state names in ALL_CAPS or SNAKE_CASE
    import re

    # Pattern: ITEM_LOGIN_REQUIRED, OAUTH_INVALID_TOKEN, etc.
    state_pattern = re.compile(r'\b([A-Z][A-Z_]{3,})\b')
    states_found = set(state_pattern.findall(mf.description))

    # Also look for states mentioned as inline code or quoted
    code_pattern = re.compile(r'`([A-Za-z_]+)`')
    states_found.update(code_pattern.findall(mf.description))

    # Common state keywords for classification
    error_keywords = {"error", "failed", "invalid", "expired", "revoked", "required", "disconnect"}
    terminal_keywords = {"complete", "closed", "removed", "deleted", "terminated"}
    initial_keywords = {"healthy", "active", "initial", "pending", "created", "new"}

    for state_name in states_found:
        if len(state_name) < 3:
            continue
        state_lower = state_name.lower()
        fsm.states.append(FSMState(
            name=state_name,
            is_initial=any(k in state_lower for k in initial_keywords),
            is_terminal=any(k in state_lower for k in terminal_keywords),
            is_error=any(k in state_lower for k in error_keywords),
        ))

    # Extract transitions from "→" or "->" patterns
    # Match WORD_CHARS → WORD_CHARS, stopping at whitespace/punctuation after the target
    transition_pattern = re.compile(r'([A-Z][A-Z_]+)\s*(?:→|->)\s*([A-Z][A-Z_]+)')
    for match in transition_pattern.finditer(mf.description):
        from_s = match.group(1).strip()
        to_s = match.group(2).strip()
        if from_s and to_s:
            fsm.transitions.append(FSMTransition(from_state=from_s, to_state=to_s))

    return fsm if fsm.states else None
