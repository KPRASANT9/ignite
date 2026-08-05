"""Tests for the IGNITE Pipeline Orchestrator — closed-loop runner."""

from __future__ import annotations

import json
import sys
from pathlib import Path

import pytest

sys.path.insert(0, str(Path(__file__).parent.parent / "src"))

from ignite_parser.orchestrator import (
    discover_traces,
    run_pipeline,
    run_loop,
    status,
    PipelineResult,
    LoopResult,
)


# --- Fixtures ---


def _write_sample_trace(traces_dir: Path, trace_id: str = "trace-test-001") -> Path:
    """Write a minimal valid trace for testing."""
    trace = {
        "schema_version": "0.1",
        "trace_id": trace_id,
        "agent_id": "test-agent",
        "agent_role": "explorer",
        "system": "test",
        "study_channel": "",
        "session_id": "session-001",
        "started_at": "2026-01-01T00:00:00Z",
        "ended_at": "2026-01-01T00:01:00Z",
        "status": "completed",
        "objective": "Test trace",
        "spans": [
            {
                "span_id": "span-001",
                "trace_id": trace_id,
                "sequence": 1,
                "kind": "api_call",
                "started_at": "2026-01-01T00:00:00Z",
                "ended_at": "2026-01-01T00:00:01Z",
                "duration_ms": 1000,
                "interaction": {
                    "target": "POST /test/endpoint",
                    "method": "POST",
                    "request": {"url": "https://api.test.com/test/endpoint"},
                    "response": {
                        "status_code": 200,
                        "body_summary": "OK",
                        "body_schema": {"type": "object", "properties": {"id": {"type": "string"}}},
                    },
                },
                "observation": {
                    "what_happened": "Called test endpoint",
                    "what_learned": "Endpoint returns JSON object with id field",
                    "confidence": "high",
                },
            },
        ],
        "findings": [
            {
                "finding_id": "finding-001",
                "trace_id": trace_id,
                "source_spans": ["span-001"],
                "category": "protocol",
                "title": "Test endpoint returns JSON with id field",
                "description": "The /test/endpoint returns a JSON object",
                "confidence": "confirmed",
                "actionability": "informational",
            },
        ],
    }

    traces_dir.mkdir(parents=True, exist_ok=True)
    filepath = traces_dir / f"{trace_id}.json"
    filepath.write_text(json.dumps(trace), encoding="utf-8")
    return filepath


# --- Unit tests ---


class TestDiscoverTraces:
    def test_finds_json_files(self, tmp_path):
        _write_sample_trace(tmp_path, "trace-001")
        _write_sample_trace(tmp_path, "trace-002")

        files = discover_traces(tmp_path)
        assert len(files) == 2

    def test_finds_nested_files(self, tmp_path):
        sub = tmp_path / "explorer"
        _write_sample_trace(sub, "trace-nested")

        files = discover_traces(tmp_path)
        assert len(files) == 1

    def test_empty_dir(self, tmp_path):
        files = discover_traces(tmp_path)
        assert files == []

    def test_nonexistent_dir(self):
        files = discover_traces("/nonexistent/path")
        assert files == []


# --- Integration tests ---


class TestRunPipeline:
    def test_basic_pipeline(self, tmp_path):
        _write_sample_trace(tmp_path, "trace-001")

        result = run_pipeline(tmp_path)

        assert result.traces_parsed == 1
        assert result.parse_errors == 0
        assert result.analysis is not None
        assert result.optimized is not None
        assert result.plan is not None
        assert result.plan.action_count > 0

    def test_empty_dir_returns_empty_result(self, tmp_path):
        result = run_pipeline(tmp_path)
        assert result.traces_parsed == 0
        assert result.plan is None

    def test_multiple_traces(self, tmp_path):
        _write_sample_trace(tmp_path, "trace-001")
        _write_sample_trace(tmp_path, "trace-002")

        result = run_pipeline(tmp_path)
        assert result.traces_parsed == 2


class TestRunLoop:
    def test_single_iteration(self, tmp_path):
        traces_dir = tmp_path / "traces"
        output_dir = tmp_path / "output"
        _write_sample_trace(traces_dir, "trace-001")

        result = run_loop(
            traces_dir=traces_dir,
            output_dir=output_dir,
            max_iterations=1,
        )

        assert result.iteration_count == 1
        assert result.total_actions_executed > 0

    def test_converges(self, tmp_path):
        traces_dir = tmp_path / "traces"
        output_dir = tmp_path / "output"
        _write_sample_trace(traces_dir, "trace-001")

        result = run_loop(
            traces_dir=traces_dir,
            output_dir=output_dir,
            max_iterations=5,
        )

        # Should converge before max iterations
        assert result.iteration_count <= 5
        # After convergence, final iteration should have 0 new actions
        if result.converged:
            assert result.iterations[-1].new_action_count == 0

    def test_max_iterations_respected(self, tmp_path):
        traces_dir = tmp_path / "traces"
        output_dir = tmp_path / "output"
        _write_sample_trace(traces_dir, "trace-001")

        result = run_loop(
            traces_dir=traces_dir,
            output_dir=output_dir,
            max_iterations=1,
        )

        assert result.iteration_count <= 1

    def test_summary(self, tmp_path):
        traces_dir = tmp_path / "traces"
        output_dir = tmp_path / "output"
        _write_sample_trace(traces_dir, "trace-001")

        result = run_loop(traces_dir=traces_dir, output_dir=output_dir, max_iterations=1)
        s = result.summary()
        assert "iterations" in s
        assert "converged" in s
        assert "total_actions" in s


class TestStatus:
    def test_basic_status(self, tmp_path):
        _write_sample_trace(tmp_path, "trace-001")

        result = status(tmp_path)

        assert result["traces_parsed"] == 1
        assert "findings" in result
        assert "planned_actions" in result
