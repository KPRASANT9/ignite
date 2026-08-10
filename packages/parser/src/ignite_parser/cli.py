"""CLI entry point for the IGNITE Pipeline.

Usage:
    ignite-parse <file.jsonl>                         Parse and validate traces
    ignite-parse --analyze <file.jsonl>               Parse + analyze traces
    ignite-parse run [--traces-dir] [--output-dir]    Run full closed-loop pipeline
    ignite-parse plan [--traces-dir]                  Run pipeline without execution
    ignite-parse status [--traces-dir]                Show coverage + accuracy report
"""

from __future__ import annotations

import argparse
import json
import sys
from pathlib import Path

from ignite_parser.parser import parse_file, parse_trace, parse_jsonl
from ignite_parser.analyzer import analyze
from ignite_parser.reporter import generate_report


def _parse_auto(path: Path):
    """Parse a file, auto-detecting JSON vs JSONL format."""
    text = path.read_text(encoding="utf-8")
    stripped = text.strip()
    if stripped.startswith("{"):
        try:
            data = json.loads(stripped)
            return parse_trace(data, source=str(path))
        except json.JSONDecodeError:
            pass
    return parse_jsonl(text, source=str(path))


def _cmd_parse(args) -> int:
    """Handle the parse subcommand (default behavior)."""
    if args.report:
        args.analyze = True

    if not args.file.exists():
        print(f"File not found: {args.file}", file=sys.stderr)
        return 1

    result = _parse_auto(args.file)

    if not args.json:
        print(f"Parsed: {result.valid_count} traces, {result.error_count} errors, {result.warning_count} warnings")
        for err in result.errors:
            print(f"  ERROR  {err.path}: {err.message}")
        for warn in result.warnings:
            print(f"  WARN   {warn.path}: {warn.message}")
    else:
        out = {
            "valid_count": result.valid_count,
            "error_count": result.error_count,
            "warning_count": result.warning_count,
            "errors": [{"path": e.path, "message": e.message} for e in result.errors],
            "warnings": [{"path": w.path, "message": w.message} for w in result.warnings],
        }

    if not result.ok:
        if args.json:
            print(json.dumps(out, indent=2))
        return 1

    if args.analyze:
        analysis = analyze(result.traces)
        if args.report:
            systems = list(analysis.coverage.systems_explored)
            system = systems[0] if len(systems) == 1 else None
            report = generate_report(analysis, system=system)
            print(report)
        elif not args.json:
            print(f"\nAnalysis: {analysis.endpoint_count} endpoints, "
                  f"{analysis.graph.edge_count} dependency edges, "
                  f"{analysis.cluster_count} finding clusters")
            print(f"Coverage: {analysis.coverage.total_spans} spans across "
                  f"{len(analysis.coverage.systems_explored)} systems")
            if analysis.coverage.open_questions:
                print(f"Open questions: {len(analysis.coverage.open_questions)}")
        else:
            out["analysis"] = {
                "endpoint_count": analysis.endpoint_count,
                "dependency_edges": analysis.graph.edge_count,
                "finding_clusters": analysis.cluster_count,
                "systems_explored": list(analysis.coverage.systems_explored),
                "open_questions": analysis.coverage.open_questions,
            }

    if args.json:
        print(json.dumps(out, indent=2))

    return 0


def _cmd_run(args) -> int:
    """Handle the run subcommand — full closed-loop pipeline."""
    from ignite_parser.orchestrator import run_loop

    traces_dir = Path(args.traces_dir)
    output_dir = Path(args.output_dir)

    if not traces_dir.exists():
        print(f"Traces directory not found: {traces_dir}", file=sys.stderr)
        return 1

    print(f"Running closed-loop pipeline...")
    print(f"  Traces: {traces_dir}")
    print(f"  Output: {output_dir}")
    print(f"  Max iterations: {args.max_iterations}")
    print()

    result = run_loop(
        traces_dir=traces_dir,
        output_dir=output_dir,
        max_iterations=args.max_iterations,
        system=args.system,
    )

    for it in result.iterations:
        status = "converged" if result.converged and it == result.iterations[-1] else "completed"
        exec_info = ""
        if it.execution:
            exec_info = (
                f" | Executed: {it.execution.succeeded}/{it.execution.total} succeeded"
            )
        print(f"  Iteration {it.iteration}: {it.pipeline.traces_parsed} traces, "
              f"{it.new_action_count} new actions{exec_info} [{status}]")

    print()
    print(f"Loop {'converged' if result.converged else 'reached max iterations'} "
          f"after {result.iteration_count} iteration(s)")
    print(f"Total actions executed: {result.total_actions_executed}")
    print(f"Total files generated: {result.total_files_generated}")

    if args.json:
        print(json.dumps(result.summary(), indent=2))

    return 0


def _cmd_plan(args) -> int:
    """Handle the plan subcommand — pipeline without execution."""
    from ignite_parser.orchestrator import run_pipeline

    traces_dir = Path(args.traces_dir)
    if not traces_dir.exists():
        print(f"Traces directory not found: {traces_dir}", file=sys.stderr)
        return 1

    result = run_pipeline(traces_dir)

    if result.plan is None:
        print("No plan produced (no valid traces found)")
        return 1

    print(f"Pipeline: {result.traces_parsed} traces parsed, {result.parse_errors} errors")
    if result.optimized:
        print(f"Optimized: {result.optimized.finding_count} findings, "
              f"{result.optimized.fsm_count} FSMs, "
              f"{len(result.optimized.merged_endpoints)} endpoints")
    print(f"\nPlan: {result.plan.action_count} actions")
    for action in result.plan.topological_order():
        print(f"  [{action.priority.value:8s}] {action.kind.value:18s} {action.title}")

    if result.plan.coverage_gaps:
        print(f"\nCoverage gaps: {len(result.plan.coverage_gaps)}")
        for gap in result.plan.coverage_gaps:
            print(f"  - {gap}")

    return 0


def _cmd_status(args) -> int:
    """Handle the status subcommand — coverage + plan summary."""
    from ignite_parser.orchestrator import status

    traces_dir = Path(args.traces_dir)
    if not traces_dir.exists():
        print(f"Traces directory not found: {traces_dir}", file=sys.stderr)
        return 1

    result = status(traces_dir)

    if args.json:
        print(json.dumps(result, indent=2))
    else:
        print(f"Traces: {result.get('traces_parsed', 0)} parsed, {result.get('parse_errors', 0)} errors")
        print(f"Findings: {result.get('findings', 0)}")
        print(f"FSMs: {result.get('fsms', 0)}")
        print(f"Endpoints: {result.get('endpoints', 0)}")
        print(f"Span kinds: {result.get('span_kinds_covered', [])}")
        if "planned_actions" in result:
            print(f"\nPlanned actions: {result['planned_actions']}")
            for kind, count in result.get("actions_by_kind", {}).items():
                print(f"  {kind}: {count}")

    return 0


def _cmd_ops(args) -> int:
    """Handle the ops subcommand — run ops loop and produce report."""
    from ignite_parser.ops import run_ops

    traces_dir = Path(args.traces_dir)
    output_dir = Path(args.output_dir)

    if not traces_dir.exists():
        print(f"Traces directory not found: {traces_dir}", file=sys.stderr)
        return 1

    report = run_ops(
        traces_dir=traces_dir,
        output_dir=output_dir,
        max_iterations=args.max_iterations,
        system=args.system,
        previous_accuracy=args.previous_accuracy,
    )

    if args.format == "json":
        print(report.to_json())
    else:
        print(report.to_markdown())

    return report.exit_code


def main() -> int:
    parser = argparse.ArgumentParser(
        prog="ignite-parse",
        description="IGNITE Pipeline — parse, analyze, plan, and execute trace intelligence",
    )
    subparsers = parser.add_subparsers(dest="command")

    # Default parse command (backwards-compatible)
    parser.add_argument("file", type=Path, nargs="?", help="Path to a JSON or JSONL trace file")
    parser.add_argument("--analyze", action="store_true", help="Run Analyzer after parsing")
    parser.add_argument("--report", action="store_true", help="Generate markdown report (implies --analyze)")
    parser.add_argument("--json", action="store_true", help="Output as JSON")

    # run subcommand
    run_parser = subparsers.add_parser("run", help="Run full closed-loop pipeline")
    run_parser.add_argument("--traces-dir", default="./traces", help="Directory containing trace files")
    run_parser.add_argument("--output-dir", default="./output", help="Directory for generated output")
    run_parser.add_argument("--max-iterations", type=int, default=3, help="Maximum loop iterations")
    run_parser.add_argument("--system", default="ignite", help="System name for feedback traces")
    run_parser.add_argument("--json", action="store_true", help="Output as JSON")

    # plan subcommand
    plan_parser = subparsers.add_parser("plan", help="Run pipeline without execution")
    plan_parser.add_argument("--traces-dir", default="./traces", help="Directory containing trace files")

    # status subcommand
    status_parser = subparsers.add_parser("status", help="Show coverage + accuracy report")
    status_parser.add_argument("--traces-dir", default="./traces", help="Directory containing trace files")
    status_parser.add_argument("--json", action="store_true", help="Output as JSON")

    # ops subcommand
    ops_parser = subparsers.add_parser("ops", help="Run ops loop and produce structured report")
    ops_parser.add_argument("--traces-dir", default="./traces", help="Directory containing trace files")
    ops_parser.add_argument("--output-dir", default="./output", help="Directory for generated output")
    ops_parser.add_argument("--max-iterations", type=int, default=5, help="Maximum loop iterations (safety bound)")
    ops_parser.add_argument("--system", default="ignite", help="System name for feedback traces")
    ops_parser.add_argument("--format", choices=["markdown", "json"], default="markdown", help="Output format")
    ops_parser.add_argument("--previous-accuracy", type=float, default=None, help="Accuracy from previous run (for drop detection)")

    args = parser.parse_args()

    if args.command == "run":
        return _cmd_run(args)
    elif args.command == "plan":
        return _cmd_plan(args)
    elif args.command == "status":
        return _cmd_status(args)
    elif args.command == "ops":
        return _cmd_ops(args)
    elif args.file:
        return _cmd_parse(args)
    else:
        parser.print_help()
        return 0


if __name__ == "__main__":
    sys.exit(main())
