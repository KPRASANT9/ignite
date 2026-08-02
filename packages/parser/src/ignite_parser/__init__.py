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
from ignite_parser.executor import execute, ExecutionResult
from ignite_parser.feedback import capture_feedback, compute_divergence, lookup_correction
from ignite_parser.orchestrator import run_pipeline, run_loop, LoopResult
from ignite_parser.accuracy import measure_accuracy, AccuracyReport
from ignite_parser.ops import run_ops, OpsReport
from ignite_parser.spike import detect_spikes, SpikeSignal, SpikeType, Urgency
from ignite_parser.loop_detector import detect_loops, DetectedLoop, LoopDetectionResult

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
    # Executor
    "execute", "ExecutionResult",
    # Feedback
    "capture_feedback", "compute_divergence", "lookup_correction",
    # Orchestrator
    "run_pipeline", "run_loop", "LoopResult",
    # Accuracy
    "measure_accuracy", "AccuracyReport",
    # Ops
    "run_ops", "OpsReport",
    # Spike Detection
    "detect_spikes", "SpikeSignal", "SpikeType", "Urgency",
    # Loop Detection
    "detect_loops", "DetectedLoop", "LoopDetectionResult",
]
