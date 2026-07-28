"""Tests for ExplorationManifest — coverage tracking and targeting."""

from pathlib import Path

from ignite_trace.manifest import ExplorationManifest, ManifestTarget


SAMPLE_MANIFEST = {
    "system": "plaid",
    "targets": [
        {"id": "M0", "domain": "auth", "span_kind": "doc_read", "priority": "P1", "tier": 1, "status": "completed", "trace_id": "trace-001"},
        {"id": "M1", "domain": "transactions", "span_kind": "api_call", "priority": "P1", "tier": 1},
        {"id": "M2", "domain": "errors", "span_kind": "error_probe", "priority": "P1", "tier": 1},
        {"id": "M6", "domain": "transfer", "span_kind": "state_transition", "priority": "P2", "tier": 2},
        {"id": "M11", "domain": "balance_cross", "span_kind": "api_call", "priority": "P3", "tier": 3},
    ],
}


class TestExplorationManifest:
    def test_from_dict(self):
        m = ExplorationManifest.from_dict(SAMPLE_MANIFEST)
        assert m.system == "plaid"
        assert len(m.targets) == 5

    def test_next_returns_highest_priority_unexplored(self):
        m = ExplorationManifest.from_dict(SAMPLE_MANIFEST)
        target = m.next()
        assert target is not None
        assert target.priority == "P1"
        assert target.id == "M1"  # M0 is completed

    def test_next_with_priority_filter(self):
        m = ExplorationManifest.from_dict(SAMPLE_MANIFEST)
        target = m.next(priority="P2")
        assert target is not None
        assert target.id == "M6"

    def test_next_with_tier_filter(self):
        m = ExplorationManifest.from_dict(SAMPLE_MANIFEST)
        target = m.next(tier=3)
        assert target is not None
        assert target.id == "M11"

    def test_next_returns_none_when_all_explored(self):
        m = ExplorationManifest.from_dict({
            "system": "test",
            "targets": [
                {"id": "T1", "domain": "d", "span_kind": "api_call", "priority": "P1", "tier": 1, "status": "completed"},
            ],
        })
        assert m.next() is None

    def test_mark_completed(self):
        m = ExplorationManifest.from_dict(SAMPLE_MANIFEST)
        m.mark_completed("M1", "trace-txn-001")
        target = [t for t in m.targets if t.id == "M1"][0]
        assert target.explored
        assert target.trace_id == "trace-txn-001"

    def test_mark_in_progress(self):
        m = ExplorationManifest.from_dict(SAMPLE_MANIFEST)
        m.mark_in_progress("M1")
        target = [t for t in m.targets if t.id == "M1"][0]
        assert target.status == "in_progress"
        assert not target.explored  # in_progress is not explored

    def test_coverage_stats(self):
        m = ExplorationManifest.from_dict(SAMPLE_MANIFEST)
        cov = m.coverage()
        assert cov["total"] == 5
        assert cov["completed"] == 1  # M0
        assert cov["percentage"] == 20.0
        assert cov["by_tier"][1]["total"] == 3
        assert cov["by_tier"][1]["completed"] == 1

    def test_matrix(self):
        m = ExplorationManifest.from_dict(SAMPLE_MANIFEST)
        matrix = m.matrix()
        assert matrix["auth"]["doc_read"] == "✅"
        assert matrix["transactions"]["api_call"] == "◻"

    def test_from_yaml(self):
        manifest_path = Path(__file__).parent.parent / "manifests" / "plaid.yaml"
        if manifest_path.exists():
            m = ExplorationManifest.from_yaml(manifest_path)
            assert m.system == "plaid"
            assert len(m.targets) > 0
            # M0 should be completed
            m0 = [t for t in m.targets if t.id == "M0-auth-docread"]
            assert len(m0) == 1
            assert m0[0].explored

    def test_coverage_by_span_kind(self):
        m = ExplorationManifest.from_dict(SAMPLE_MANIFEST)
        cov = m.coverage()
        assert "doc_read" in cov["by_span_kind"]
        assert cov["by_span_kind"]["doc_read"]["completed"] == 1

    def test_next_prefers_lower_tier_within_same_priority(self):
        m = ExplorationManifest.from_dict({
            "system": "test",
            "targets": [
                {"id": "A", "domain": "d", "span_kind": "api_call", "priority": "P1", "tier": 2},
                {"id": "B", "domain": "d", "span_kind": "error_probe", "priority": "P1", "tier": 1},
            ],
        })
        target = m.next()
        assert target.id == "B"  # tier 1 before tier 2
