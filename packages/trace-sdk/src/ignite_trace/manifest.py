"""Exploration Manifest — coverage-driven targeting for trace campaigns.

Declares the (system, domain, span_kind) targets and tracks which have been
explored. Agents call manifest.next() to get the highest-priority unexplored
target instead of choosing randomly.
"""

from __future__ import annotations

from dataclasses import dataclass, field
from pathlib import Path
from typing import Any

import yaml


@dataclass
class ManifestTarget:
    """A single (domain, span_kind) cell in the exploration matrix."""
    id: str
    domain: str
    span_kind: str
    priority: str  # "P1", "P2", "P3"
    tier: int  # 1, 2, 3
    description: str = ""
    expected_findings: list[str] = field(default_factory=list)
    endpoints: list[str] = field(default_factory=list)
    modality: str = "api"  # "api", "db", "cli", "web", "message", "custom"
    status: str = "unexplored"  # "unexplored", "in_progress", "completed"
    trace_id: str | None = None  # links to the trace that covered this target

    @property
    def explored(self) -> bool:
        return self.status == "completed"


class ExplorationManifest:
    """Loads and queries an exploration manifest for a system.

    Usage:
        manifest = ExplorationManifest.from_yaml("manifests/plaid.yaml")
        next_target = manifest.next()           # highest-priority unexplored
        next_target = manifest.next(priority="P1")  # filter by priority
        coverage = manifest.coverage()          # % explored
    """

    def __init__(self, system: str, targets: list[ManifestTarget]):
        self.system = system
        self.targets = targets

    @classmethod
    def from_yaml(cls, path: str | Path) -> ExplorationManifest:
        """Load manifest from a YAML file."""
        data = yaml.safe_load(Path(path).read_text(encoding="utf-8"))
        system = data.get("system", "unknown")
        targets = []
        for t in data.get("targets", []):
            targets.append(ManifestTarget(
                id=t["id"],
                domain=t["domain"],
                span_kind=t["span_kind"],
                priority=t.get("priority", "P3"),
                tier=t.get("tier", 3),
                description=t.get("description", ""),
                expected_findings=t.get("expected_findings", []),
                endpoints=t.get("endpoints", []),
                modality=t.get("modality", "api"),
                status=t.get("status", "unexplored"),
                trace_id=t.get("trace_id"),
            ))
        return cls(system=system, targets=targets)

    @classmethod
    def from_dict(cls, data: dict[str, Any]) -> ExplorationManifest:
        """Load manifest from a dictionary."""
        system = data.get("system", "unknown")
        targets = []
        for t in data.get("targets", []):
            targets.append(ManifestTarget(
                id=t["id"],
                domain=t["domain"],
                span_kind=t["span_kind"],
                priority=t.get("priority", "P3"),
                tier=t.get("tier", 3),
                description=t.get("description", ""),
                expected_findings=t.get("expected_findings", []),
                endpoints=t.get("endpoints", []),
                modality=t.get("modality", "api"),
                status=t.get("status", "unexplored"),
                trace_id=t.get("trace_id"),
            ))
        return cls(system=system, targets=targets)

    def next(self, priority: str | None = None, tier: int | None = None) -> ManifestTarget | None:
        """Return the highest-priority unexplored target.

        Priority ordering: P1 > P2 > P3. Within same priority, lower tier first.
        """
        priority_order = {"P1": 0, "P2": 1, "P3": 2}
        candidates = [t for t in self.targets if not t.explored]

        if priority:
            candidates = [t for t in candidates if t.priority == priority]
        if tier is not None:
            candidates = [t for t in candidates if t.tier == tier]

        if not candidates:
            return None

        candidates.sort(key=lambda t: (priority_order.get(t.priority, 99), t.tier))
        return candidates[0]

    def mark_completed(self, target_id: str, trace_id: str) -> None:
        """Mark a target as completed and link it to its trace."""
        for t in self.targets:
            if t.id == target_id:
                t.status = "completed"
                t.trace_id = trace_id
                return

    def mark_in_progress(self, target_id: str) -> None:
        """Mark a target as currently being explored."""
        for t in self.targets:
            if t.id == target_id:
                t.status = "in_progress"
                return

    def coverage(self) -> dict[str, Any]:
        """Return coverage statistics."""
        total = len(self.targets)
        completed = sum(1 for t in self.targets if t.explored)
        by_tier: dict[int, dict[str, int]] = {}
        by_span_kind: dict[str, dict[str, int]] = {}
        by_domain: dict[str, dict[str, int]] = {}

        for t in self.targets:
            # By tier
            if t.tier not in by_tier:
                by_tier[t.tier] = {"total": 0, "completed": 0}
            by_tier[t.tier]["total"] += 1
            if t.explored:
                by_tier[t.tier]["completed"] += 1

            # By span kind
            if t.span_kind not in by_span_kind:
                by_span_kind[t.span_kind] = {"total": 0, "completed": 0}
            by_span_kind[t.span_kind]["total"] += 1
            if t.explored:
                by_span_kind[t.span_kind]["completed"] += 1

            # By domain
            if t.domain not in by_domain:
                by_domain[t.domain] = {"total": 0, "completed": 0}
            by_domain[t.domain]["total"] += 1
            if t.explored:
                by_domain[t.domain]["completed"] += 1

        return {
            "system": self.system,
            "total": total,
            "completed": completed,
            "percentage": round(completed / total * 100, 1) if total > 0 else 0,
            "by_tier": by_tier,
            "by_span_kind": by_span_kind,
            "by_domain": by_domain,
        }

    def matrix(self) -> dict[str, dict[str, str]]:
        """Return the domain × span_kind coverage matrix."""
        domains: dict[str, dict[str, str]] = {}
        for t in self.targets:
            if t.domain not in domains:
                domains[t.domain] = {}
            domains[t.domain][t.span_kind] = "✅" if t.explored else ("🔄" if t.status == "in_progress" else "◻")
        return domains
