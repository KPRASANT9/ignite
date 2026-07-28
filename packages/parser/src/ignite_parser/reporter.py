"""IGNITE L2 Reporter — Third stage of the pipeline.

Consumes AnalysisResult from the Analyzer and produces human-readable
markdown reports. This is the first accessibility layer: structured
data becomes actionable intelligence.

Report sections:
  1. Executive Summary — key numbers at a glance
  2. Endpoint Catalog — what was found, per endpoint
  3. Findings — clustered, ranked by confidence
  4. Dependency Map — text representation of the graph
  5. Coverage & Gaps — what's explored vs. what remains
  6. Open Questions — prioritized research backlog
"""

from __future__ import annotations

from datetime import datetime, timezone
from typing import Optional

from ignite_parser.analyzer import (
    AnalysisResult,
    EndpointRecord,
    FindingCluster,
)
from ignite_parser.models import FindingConfidence, SpanKind


def generate_report(
    result: AnalysisResult,
    title: str = "IGNITE Trace Analysis Report",
    system: Optional[str] = None,
) -> str:
    """Generate a full markdown report from an AnalysisResult.

    Args:
        result: Output from the Analyzer stage.
        title: Report title.
        system: Optional system name filter for the header.

    Returns:
        Complete markdown report as a string.
    """
    sections = [
        _header(title, result, system),
        _executive_summary(result),
        _endpoint_catalog(result),
        _findings_section(result),
        _dependency_map(result),
        _coverage_section(result),
        _open_questions(result),
        _footer(),
    ]
    return "\n".join(s for s in sections if s)


def _header(title: str, result: AnalysisResult, system: Optional[str]) -> str:
    lines = [f"# {title}"]
    meta = []
    if system:
        meta.append(f"**System:** {system}")
    elif result.coverage.systems_explored:
        systems = ", ".join(sorted(result.coverage.systems_explored))
        meta.append(f"**Systems:** {systems}")
    meta.append(f"**Generated:** {datetime.now(timezone.utc).strftime('%Y-%m-%d %H:%M UTC')}")
    meta.append(f"**Pipeline:** Parser → Analyzer → Reporter v0.1")
    lines.append("\n".join(meta))
    return "\n\n".join(lines)


def _executive_summary(result: AnalysisResult) -> str:
    cov = result.coverage
    lines = [
        "## Executive Summary",
        "",
        f"| Metric | Value |",
        f"|--------|-------|",
        f"| Traces analyzed | {cov.total_traces} |",
        f"| Spans processed | {cov.total_spans} |",
        f"| Endpoints discovered | {result.endpoint_count} |",
        f"| Findings extracted | {cov.total_findings} |",
        f"| Finding clusters | {result.cluster_count} |",
        f"| Dependency edges | {result.graph.edge_count} |",
        f"| Open questions | {len(cov.open_questions)} |",
        f"| Low-confidence areas | {len(cov.low_confidence_areas)} |",
    ]
    if cov.agent_roles_active:
        roles = ", ".join(sorted(cov.agent_roles_active))
        lines.append(f"| Agent roles active | {roles} |")
    if cov.span_kinds_seen:
        kinds = ", ".join(sorted(k.value for k in cov.span_kinds_seen))
        lines.append(f"| Span kinds observed | {kinds} |")
    return "\n".join(lines)


def _endpoint_catalog(result: AnalysisResult) -> str:
    if not result.endpoints:
        return ""

    lines = ["", "## Endpoint Catalog", ""]

    # Sort endpoints: highest hit count first
    sorted_eps = sorted(
        result.endpoints.values(),
        key=lambda ep: ep.hit_count,
        reverse=True,
    )

    for ep in sorted_eps:
        # Header
        label = f"{ep.method} {ep.target}" if ep.method else ep.target
        lines.append(f"### `{label}`")
        lines.append("")

        # Metadata table
        lines.append(f"| Property | Value |")
        lines.append(f"|----------|-------|")
        lines.append(f"| Hits | {ep.hit_count} |")
        if ep.url:
            lines.append(f"| URL | `{ep.url}` |")
        if ep.status_codes:
            codes = ", ".join(str(c) for c in sorted(ep.status_codes))
            lines.append(f"| Status codes | {codes} |")
        if ep.span_kinds:
            kinds = ", ".join(k.value for k in sorted(ep.span_kinds, key=lambda k: k.value))
            lines.append(f"| Span kinds | {kinds} |")
        if ep.tags:
            tags = ", ".join(sorted(ep.tags))
            lines.append(f"| Tags | {tags} |")
        lines.append("")

        # Observations
        if ep.observations:
            lines.append("**Observations:**")
            for obs in ep.observations:
                lines.append(f"- {obs}")
            lines.append("")

        # Error patterns
        if ep.error_patterns:
            lines.append("**Error patterns:**")
            for err in ep.error_patterns:
                lines.append(f"- `{err}`")
            lines.append("")

        # Request schema
        if ep.request_schema:
            lines.append("**Request body (sample):**")
            lines.append("```json")
            import json
            lines.append(json.dumps(ep.request_schema, indent=2))
            lines.append("```")
            lines.append("")

        # Response schema
        if ep.response_schema:
            lines.append("**Response schema:**")
            lines.append("```json")
            import json
            lines.append(json.dumps(ep.response_schema, indent=2))
            lines.append("```")
            lines.append("")

        # Open questions for this endpoint
        if ep.questions:
            unique_q = list(dict.fromkeys(ep.questions))  # dedup preserving order
            lines.append("**Open questions:**")
            for q in unique_q:
                lines.append(f"- {q}")
            lines.append("")

    return "\n".join(lines)


_CONFIDENCE_EMOJI = {
    FindingConfidence.CONFIRMED: "✅",
    FindingConfidence.PROBABLE: "🔶",
    FindingConfidence.HYPOTHESIS: "❓",
}

_CONFIDENCE_SORT = {
    FindingConfidence.CONFIRMED: 0,
    FindingConfidence.PROBABLE: 1,
    FindingConfidence.HYPOTHESIS: 2,
}


def _findings_section(result: AnalysisResult) -> str:
    if not result.finding_clusters:
        return ""

    lines = ["", "## Findings", ""]

    # Sort: confirmed first, then probable, then hypothesis
    sorted_clusters = sorted(
        result.finding_clusters,
        key=lambda c: (_CONFIDENCE_SORT.get(c.highest_confidence, 3), c.title),
    )

    # Summary table
    lines.append("| Confidence | Category | Finding | Sources |")
    lines.append("|------------|----------|---------|---------|")
    for cluster in sorted_clusters:
        emoji = _CONFIDENCE_EMOJI.get(cluster.highest_confidence, "")
        sources = cluster.count
        lines.append(
            f"| {emoji} {cluster.highest_confidence.value} | {cluster.category.value} | {cluster.title} | {sources} trace(s) |"
        )
    lines.append("")

    # Detailed findings
    for cluster in sorted_clusters:
        emoji = _CONFIDENCE_EMOJI.get(cluster.highest_confidence, "")
        lines.append(f"### {emoji} {cluster.title}")
        lines.append("")
        lines.append(f"**Category:** {cluster.category.value} | **Confidence:** {cluster.highest_confidence.value} | **Sources:** {cluster.count} trace(s)")
        lines.append("")
        # Show the most detailed description from the cluster
        best = max(cluster.findings, key=lambda f: len(f.description))
        lines.append(best.description)
        lines.append("")
        if best.evidence:
            lines.append(f"**Evidence:** {', '.join(best.evidence)}")
            lines.append("")

    return "\n".join(lines)


def _dependency_map(result: AnalysisResult) -> str:
    if not result.graph.edges:
        return ""

    lines = ["", "## Dependency Map", ""]

    # Group edges by relation type
    by_relation: dict[str, list[tuple[str, str]]] = {}
    for edge in result.graph.edges:
        by_relation.setdefault(edge.relation, []).append((edge.from_id, edge.to_id))

    for relation, pairs in sorted(by_relation.items()):
        lines.append(f"### {relation.replace('_', ' ').title()}")
        lines.append("")
        lines.append("```")
        for from_id, to_id in pairs:
            lines.append(f"  {from_id} → {to_id}")
        lines.append("```")
        lines.append("")

    # Graph topology
    roots = result.graph.roots()
    leaves = result.graph.leaves()
    if roots or leaves:
        lines.append("**Topology:**")
        if roots:
            lines.append(f"- Entry points: {', '.join(sorted(roots))}")
        if leaves:
            lines.append(f"- Terminal nodes: {', '.join(sorted(leaves))}")
        lines.append("")

    return "\n".join(lines)


def _coverage_section(result: AnalysisResult) -> str:
    cov = result.coverage
    lines = ["", "## Coverage & Gaps", ""]

    # What's been explored
    all_span_kinds = set(SpanKind)
    seen = cov.span_kinds_seen
    missing = all_span_kinds - seen

    lines.append("### Exploration Coverage")
    lines.append("")
    lines.append("| Span Kind | Status |")
    lines.append("|-----------|--------|")
    for kind in sorted(all_span_kinds, key=lambda k: k.value):
        status = "✅ Observed" if kind in seen else "⬜ Not yet explored"
        lines.append(f"| {kind.value} | {status} |")
    lines.append("")

    if missing:
        lines.append(f"**{len(missing)} span kind(s) not yet explored** — these represent investigation angles the swarm hasn't tried yet.")
        lines.append("")

    # Low-confidence areas
    if cov.low_confidence_areas:
        lines.append("### Low-Confidence Areas")
        lines.append("")
        lines.append("These observations need deeper investigation:")
        lines.append("")
        for area in cov.low_confidence_areas:
            lines.append(f"- {area}")
        lines.append("")

    return "\n".join(lines)


def _open_questions(result: AnalysisResult) -> str:
    questions = result.coverage.open_questions
    if not questions:
        return ""

    lines = [
        "", "## Open Questions", "",
        "These questions were raised during exploration and remain unanswered:",
        "",
    ]
    for i, q in enumerate(questions, 1):
        lines.append(f"{i}. {q}")
    lines.append("")

    return "\n".join(lines)


def _footer() -> str:
    return "\n---\n*Generated by IGNITE L2 Pipeline: Parser → Analyzer → Reporter v0.1*\n"
