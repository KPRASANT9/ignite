"""CLI entry point for the IGNITE Parser + Analyzer pipeline.

Usage:
    ignite-parse <file.jsonl>           Parse and validate traces
    ignite-parse --analyze <file.jsonl> Parse + analyze traces
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
    # If the file starts with '{' and is valid JSON, treat as a single trace
    if stripped.startswith("{"):
        try:
            data = json.loads(stripped)
            return parse_trace(data, source=str(path))
        except json.JSONDecodeError:
            pass
    # Otherwise treat as JSONL
    return parse_jsonl(text, source=str(path))


def main() -> int:
    parser = argparse.ArgumentParser(
        prog="ignite-parse",
        description="IGNITE L2 Parser — validate and analyze trace files (JSON or JSONL)",
    )
    parser.add_argument("file", type=Path, help="Path to a JSON or JSONL trace file")
    parser.add_argument("--analyze", action="store_true", help="Run Analyzer after parsing")
    parser.add_argument("--report", action="store_true", help="Generate markdown report (implies --analyze)")
    parser.add_argument("--json", action="store_true", help="Output as JSON")

    args = parser.parse_args()

    # --report implies --analyze
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
            # Determine system name from traces
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


if __name__ == "__main__":
    sys.exit(main())
