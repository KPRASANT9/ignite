"""IGNITE Pipeline Orchestrator — Runs the full closed-loop pipeline.

Chains all stages: Parser -> Analyzer -> Optimizer -> Planner -> Executor -> Feedback
Detects convergence (no new actions) and terminates.

Usage:
    from ignite_parser.orchestrator import run_loop, run_pipeline
    result = run_loop("./traces", output_dir="./output", max_iterations=3)
"""

from __future__ import annotations

import json
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any

from ignite_parser.analyzer import AnalysisResult, analyze
from ignite_parser.executor import ExecutionResult, execute
from ignite_parser.feedback import capture_feedback
from ignite_parser.optimizer import OptimizedResult, optimize
from ignite_parser.parser import parse_file, parse_trace, parse_jsonl
from ignite_parser.planner import ExecutionPlan, plan


@dataclass
class PipelineResult:
    """Result of a single pipeline pass (no execution)."""
    traces_parsed: int = 0
    parse_errors: int = 0
    analysis: AnalysisResult | None = None
    optimized: OptimizedResult | None = None
    plan: ExecutionPlan | None = None

    @property
    def ok(self) -> bool:
        return self.traces_parsed > 0 and self.parse_errors == 0


@dataclass
class LoopIteration:
    """Result of one iteration of the loop."""
    iteration: int
    pipeline: PipelineResult
    execution: ExecutionResult | None = None
    feedback_path: Path | None = None
    new_action_count: int = 0


@dataclass
class LoopResult:
    """Complete result of running the loop to convergence or max iterations."""
    iterations: list[LoopIteration] = field(default_factory=list)
    converged: bool = False
    total_actions_executed: int = 0
    total_files_generated: int = 0

    @property
    def iteration_count(self) -> int:
        return len(self.iterations)

    @property
    def final_plan(self) -> ExecutionPlan | None:
        if self.iterations:
            return self.iterations[-1].pipeline.plan
        return None

    def summary(self) -> dict[str, Any]:
        return {
            "iterations": self.iteration_count,
            "converged": self.converged,
            "total_actions": self.total_actions_executed,
            "total_files": self.total_files_generated,
        }


def discover_traces(traces_dir: str | Path) -> list[Path]:
    """Find all trace files (JSON and JSONL) in a directory tree."""
    traces_path = Path(traces_dir)
    if not traces_path.exists():
        return []

    files = []
    for pattern in ("**/*.json", "**/*.jsonl"):
        files.extend(traces_path.glob(pattern))

    # Sort by modification time (oldest first)
    return sorted(files, key=lambda p: p.stat().st_mtime)


def run_pipeline(traces_dir: str | Path) -> PipelineResult:
    """Run the pipeline (parse -> analyze -> optimize -> plan) without execution.

    This is the read-only path — produces a plan but doesn't execute it.
    """
    result = PipelineResult()
    trace_files = discover_traces(traces_dir)

    if not trace_files:
        return result

    # Parse all traces (auto-detect JSON vs JSONL)
    all_traces = []
    for tf in trace_files:
        try:
            text = tf.read_text(encoding="utf-8").strip()
            if text.startswith("{"):
                # Single JSON trace
                data = json.loads(text)
                parsed = parse_trace(data, source=str(tf))
            else:
                parsed = parse_file(str(tf))
            all_traces.extend(parsed.traces)
            result.parse_errors += parsed.error_count
        except Exception:
            result.parse_errors += 1

    result.traces_parsed = len(all_traces)

    if not all_traces:
        return result

    # Analyze each trace individually, then optimize across all
    analyses = []
    # Group traces by trace_id to avoid duplicate analysis
    seen_ids: set[str] = set()
    unique_traces = []
    for t in all_traces:
        if t.trace_id not in seen_ids:
            seen_ids.add(t.trace_id)
            unique_traces.append(t)

    for trace in unique_traces:
        analysis = analyze([trace])
        analyses.append(analysis)

    result.analysis = analyses[0] if len(analyses) == 1 else analyze(unique_traces)

    # Optimize
    result.optimized = optimize(analyses)

    # Plan
    result.plan = plan(result.optimized)

    return result


def run_loop(
    traces_dir: str | Path,
    output_dir: str | Path = ".",
    max_iterations: int = 3,
    system: str = "ignite",
) -> LoopResult:
    """Run the full closed-loop pipeline until convergence or max iterations.

    Convergence: the plan produces no new actions compared to the previous iteration.

    Each iteration:
    1. Parse all traces (including feedback from prior iterations)
    2. Analyze -> Optimize -> Plan
    3. Execute the plan
    4. Capture feedback as action_result traces
    5. Re-enter the loop
    """
    loop_result = LoopResult()
    traces_path = Path(traces_dir)
    output_path = Path(output_dir)
    previous_action_ids: set[str] = set()

    for i in range(1, max_iterations + 1):
        # Run pipeline
        pipeline = run_pipeline(traces_path)

        if pipeline.plan is None or pipeline.optimized is None:
            iteration = LoopIteration(
                iteration=i,
                pipeline=pipeline,
                new_action_count=0,
            )
            loop_result.iterations.append(iteration)
            break

        # Check convergence: are there new actions?
        current_action_titles = {a.title for a in pipeline.plan.actions}
        new_actions = current_action_titles - previous_action_ids

        if not new_actions and i > 1:
            iteration = LoopIteration(
                iteration=i,
                pipeline=pipeline,
                new_action_count=0,
            )
            loop_result.iterations.append(iteration)
            loop_result.converged = True
            break

        # Execute the plan
        execution = execute(pipeline.plan, pipeline.optimized, output_dir=output_path)

        # Capture feedback
        feedback_path = capture_feedback(
            pipeline.plan,
            execution,
            output_dir=str(traces_path),
            system=system,
        )

        # Track results
        iteration = LoopIteration(
            iteration=i,
            pipeline=pipeline,
            execution=execution,
            feedback_path=feedback_path,
            new_action_count=len(new_actions),
        )
        loop_result.iterations.append(iteration)

        loop_result.total_actions_executed += execution.succeeded
        for ar in execution.results:
            loop_result.total_files_generated += len(ar.generated_files)

        previous_action_ids = current_action_titles

    return loop_result


def status(traces_dir: str | Path) -> dict[str, Any]:
    """Get current pipeline status: coverage, trace count, plan summary."""
    pipeline = run_pipeline(traces_dir)

    result: dict[str, Any] = {
        "traces_parsed": pipeline.traces_parsed,
        "parse_errors": pipeline.parse_errors,
    }

    if pipeline.optimized:
        result["findings"] = pipeline.optimized.finding_count
        result["fsms"] = pipeline.optimized.fsm_count
        result["institutions"] = pipeline.optimized.institution_count
        result["endpoints"] = len(pipeline.optimized.merged_endpoints)
        result["span_kinds_covered"] = [
            sk.value for sk in pipeline.optimized.coverage.span_kinds_seen
        ]

    if pipeline.plan:
        result["planned_actions"] = pipeline.plan.action_count
        result["actions_by_kind"] = {
            kind.value: len(pipeline.plan.actions_by_kind(kind))
            for kind in set(a.kind for a in pipeline.plan.actions)
        }

    return result
