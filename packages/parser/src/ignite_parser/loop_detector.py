"""IGNITE Cross-Modality Loop Detection — detects emergent BCI loops.

Walks contains_contexts relationships across spans to detect when a complete
intelligence loop has formed across modalities:

    Capture → Classify → Decode → Plan → Operate → Feedback → (back to Capture)

This is the highest-signal event in the system — it means the pipeline
learned something and changed itself. The detector is relationship-driven:
it doesn't hardcode the BCI stages, it finds cycles in the modality
transition graph built from contains_contexts.

Pipeline: Parser → Analyzer → **Loop Detector** → (L3 Operator)
"""

from __future__ import annotations

from collections import defaultdict
from dataclasses import dataclass, field
from typing import Any

from ignite_parser.models import ContainsContext, Span, Trace


# BCI stage labels for well-known relationships
_RELATIONSHIP_TO_STAGE = {
    "captures": "capture",
    "processes": "classify",
    "reads_from": "decode",
    "analyzes": "decode",
    "writes_to": "plan",
    "triggers": "operate",
    "learns_from": "feedback",
    "wraps": "operate",
    "embeds": "capture",
}


@dataclass
class LoopStep:
    """One step in a detected cross-modality loop."""
    span_id: str
    modality: str
    relationship: str
    target_modality: str
    stage: str  # BCI stage label


@dataclass
class DetectedLoop:
    """A complete cross-modality loop detected across traces."""
    loop_id: str
    steps: list[LoopStep] = field(default_factory=list)
    modalities_involved: set[str] = field(default_factory=set)
    is_complete: bool = False  # True if the loop cycles back
    cycle_length: int = 0

    @property
    def stages(self) -> list[str]:
        return [s.stage for s in self.steps]

    def summary(self) -> dict[str, Any]:
        return {
            "loop_id": self.loop_id,
            "steps": len(self.steps),
            "modalities": sorted(self.modalities_involved),
            "is_complete": self.is_complete,
            "cycle_length": self.cycle_length,
            "stages": self.stages,
        }


@dataclass
class LoopDetectionResult:
    """Result of loop detection across a set of traces."""
    loops: list[DetectedLoop] = field(default_factory=list)
    total_cross_modality_edges: int = 0
    modalities_seen: set[str] = field(default_factory=set)

    @property
    def complete_loops(self) -> list[DetectedLoop]:
        return [l for l in self.loops if l.is_complete]

    @property
    def has_complete_loop(self) -> bool:
        return any(l.is_complete for l in self.loops)

    def summary(self) -> dict[str, Any]:
        return {
            "total_loops": len(self.loops),
            "complete_loops": len(self.complete_loops),
            "total_edges": self.total_cross_modality_edges,
            "modalities": sorted(self.modalities_seen),
            "loops": [l.summary() for l in self.loops],
        }


def detect_loops(traces: list[Trace]) -> LoopDetectionResult:
    """Detect cross-modality loops across a set of traces.

    Builds a directed graph from contains_contexts relationships, then
    finds cycles using DFS. Each cycle represents a complete BCI loop
    where intelligence flows across modalities and returns.
    """
    result = LoopDetectionResult()

    # Build the cross-modality edge list from all spans
    edges: list[tuple[str, str, str, str, str]] = []  # (span_id, from_mod, to_mod, relationship, stage)

    for trace in traces:
        for span in trace.spans:
            from_mod = span.modality.value
            result.modalities_seen.add(from_mod)

            for ctx in span.contains_contexts:
                if not ctx.active:
                    continue
                to_mod = ctx.modality
                if to_mod == from_mod:
                    continue  # Skip same-modality references

                result.modalities_seen.add(to_mod)
                stage = _RELATIONSHIP_TO_STAGE.get(ctx.relationship, ctx.relationship)
                edges.append((span.span_id, from_mod, to_mod, ctx.relationship, stage))

    result.total_cross_modality_edges = len(edges)

    if not edges:
        return result

    # Build adjacency list: modality → [(target_modality, span_id, relationship, stage)]
    adj: dict[str, list[tuple[str, str, str, str]]] = defaultdict(list)
    for span_id, from_mod, to_mod, rel, stage in edges:
        adj[from_mod].append((to_mod, span_id, rel, stage))

    # Find all cycles using DFS from each modality
    all_modalities = list(result.modalities_seen)
    found_cycles: list[list[LoopStep]] = []

    for start_mod in all_modalities:
        if start_mod not in adj:
            continue
        _find_cycles_dfs(start_mod, adj, edges, found_cycles)

    # Deduplicate cycles (same set of modalities in same order = same cycle)
    seen_signatures: set[str] = set()
    loop_count = 0

    for cycle_steps in found_cycles:
        sig = "→".join(s.modality for s in cycle_steps)
        if sig in seen_signatures:
            continue
        seen_signatures.add(sig)

        loop_count += 1
        mods = set()
        for s in cycle_steps:
            mods.add(s.modality)
            mods.add(s.target_modality)
        loop = DetectedLoop(
            loop_id=f"loop-{loop_count:03d}",
            steps=cycle_steps,
            modalities_involved=mods,
            is_complete=True,
            cycle_length=len(cycle_steps),
        )
        result.loops.append(loop)

    # Also detect partial chains (non-cyclic but multi-modality)
    if not result.loops:
        chains = _find_longest_chains(adj, edges)
        for i, chain_steps in enumerate(chains):
            if len(chain_steps) >= 2:  # At least 2 modality hops
                loop = DetectedLoop(
                    loop_id=f"chain-{i+1:03d}",
                    steps=chain_steps,
                    modalities_involved={s.modality for s in chain_steps},
                    is_complete=False,
                    cycle_length=len(chain_steps),
                )
                result.loops.append(loop)

    return result


def _find_cycles_dfs(
    start: str,
    adj: dict[str, list[tuple[str, str, str, str]]],
    edges: list[tuple[str, str, str, str, str]],
    found_cycles: list[list[LoopStep]],
    max_depth: int = 10,
) -> None:
    """DFS to find cycles starting and ending at `start`."""

    def _dfs(current: str, path: list[LoopStep], visited: set[str]) -> None:
        if len(path) > max_depth:
            return

        for to_mod, span_id, rel, stage in adj.get(current, []):
            step = LoopStep(
                span_id=span_id,
                modality=current,
                relationship=rel,
                target_modality=to_mod,
                stage=stage,
            )

            if to_mod == start and len(path) >= 1:
                # Found a cycle back to start — include the closing step
                found_cycles.append(list(path) + [step])
                return

            if to_mod in visited:
                continue

            path.append(step)
            visited.add(to_mod)
            _dfs(to_mod, path, visited)
            path.pop()
            visited.discard(to_mod)

    _dfs(start, [], {start})


def _find_longest_chains(
    adj: dict[str, list[tuple[str, str, str, str]]],
    edges: list[tuple[str, str, str, str, str]],
) -> list[list[LoopStep]]:
    """Find the longest non-cyclic chains across modalities."""
    best_chains: list[list[LoopStep]] = []

    for start_mod in adj:
        chain = _longest_chain_from(start_mod, adj, set())
        if chain:
            best_chains.append(chain)

    # Sort by length, return top 3
    best_chains.sort(key=lambda c: len(c), reverse=True)
    return best_chains[:3]


def _longest_chain_from(
    current: str,
    adj: dict[str, list[tuple[str, str, str, str]]],
    visited: set[str],
) -> list[LoopStep]:
    """Find the longest chain from a starting modality."""
    visited = visited | {current}
    best: list[LoopStep] = []

    for to_mod, span_id, rel, stage in adj.get(current, []):
        if to_mod in visited:
            continue

        step = LoopStep(
            span_id=span_id,
            modality=current,
            relationship=rel,
            target_modality=to_mod,
            stage=stage,
        )
        sub_chain = _longest_chain_from(to_mod, adj, visited)
        candidate = [step] + sub_chain
        if len(candidate) > len(best):
            best = candidate

    return best
