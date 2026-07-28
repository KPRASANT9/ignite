"""IGNITE L2 Pipeline — Parser + Analyzer stages.

Parser: Ingests JSONL trace data from L1 agent swarms, validates against the
IGNITE Trace Schema, and produces typed internal records.

Analyzer: Consumes validated traces and produces structured analysis —
endpoint catalog, dependency graph, finding index, and coverage report.
"""

from ignite_parser.models import Trace, Span, Finding, ParseResult
from ignite_parser.parser import parse_trace, parse_jsonl, parse_file
from ignite_parser.analyzer import analyze, AnalysisResult
from ignite_parser.reporter import generate_report
from ignite_parser.optimizer import optimize, OptimizedResult
from ignite_parser.planner import plan, ExecutionPlan

__all__ = [
    # Parser
    "Trace", "Span", "Finding", "ParseResult",
    "parse_trace", "parse_jsonl", "parse_file",
    # Analyzer
    "analyze", "AnalysisResult",
    # Reporter
    "generate_report",
    # Optimizer
    "optimize", "OptimizedResult",
    # Planner
    "plan", "ExecutionPlan",
]
