"""IGNITE L2 Spike Signal Detection — Cross-modality intelligence extraction.

Three-stage pipeline that turns raw L1 spans into typed spike signals:
  Stage 1: Signal classification (intent_carrying vs noise vs ambiguous)
  Stage 2: Cross-modality correlation (confidence boost for multi-modal spikes)
  Stage 3: Profile pattern matching (typed spike classification)

Spike signals are the output of L2 decode — they feed L3 operator decisions.

Source: Honey Spike Signal Classification Spec.
Pipeline: Parser → Analyzer → **Spike** → (L3 Operator)
"""

from __future__ import annotations

from dataclasses import dataclass, field
from typing import Any

from ignite_parser.models import (
    Modality,
    RequestIntent,
    ResponseOutcome,
    SignalClass,
    Span,
    Trace,
)


# --- Spike Signal Types ---

class SpikeType:
    """Typed spike signal categories."""
    STATE_CHANGE = "state_change"      # Known FSM transition
    ERROR = "error"                    # Classified error with retry strategy
    ANOMALY = "anomaly"                # Deviation from baseline
    PATTERN = "pattern"                # Recurring sequence detected
    HEALTH_SHIFT = "health_shift"      # Provider/service health trend change


class Urgency:
    """How urgently L3 should act on a spike."""
    BACKGROUND = "background"          # Log, no action needed now
    ATTENTION = "attention"            # Surface to user at next interaction
    IMMEDIATE = "immediate"            # Alert user now
    CRITICAL = "critical"              # Alert + suggest immediate action


@dataclass
class SpikeSignal:
    """A classified, confidence-scored signal ready for L3 consumption.

    This is the output of the L2 spike detection pipeline. Each spike
    represents a meaningful event detected from one or more trace spans.
    """
    spike_type: str                    # SpikeType value
    confidence: float                  # 0.0-1.0 (after correlation boost)
    urgency: str                       # Urgency value
    modalities: list[str]              # Which modalities contributed
    chain: list[str]                   # Linked span_ids across modalities
    action_space: list[str]            # What L3 can do about it
    source_system: str                 # Which connected system
    correlation_boost: float = 1.0     # Multiplier from cross-modality matching
    raw_confidence: float = 0.0        # Pre-boost confidence
    error_code: str | None = None      # For error spikes: the specific error
    description: str = ""              # Human-readable spike description

    @property
    def boosted_confidence(self) -> float:
        return min(1.0, self.raw_confidence * self.correlation_boost)


# --- Per-modality noise profiles ---
# Intent-carrying rates from ProductModeller §5.

NOISE_PROFILES: dict[str, float] = {
    Modality.CLI.value: 0.90,       # ~90% intent-carrying
    Modality.API.value: 0.80,       # ~80% intent-carrying
    Modality.DB.value: 0.70,        # ~70% intent-carrying
    Modality.MESSAGE.value: 0.95,   # ~95% intent-carrying
    "web": 0.015,                   # ~1.5% intent-carrying
}


# --- Stage 1: Signal Classification ---

# CLI noise commands
_CLI_NOISE_COMMANDS = frozenset({
    "cd", "ls", "pwd", "echo", "clear", "history", "alias",
    "whoami", "hostname", "date", "uptime", "man", "help",
})

# API noise patterns
_API_NOISE_PATTERNS = frozenset({
    "health", "healthcheck", "health-check", "ping", "ready",
    "readiness", "liveness", "keepalive", "keep-alive",
})

# DB noise operations
_DB_NOISE_OPERATIONS = frozenset({
    "ping", "select 1", "select version()", "pragma",
    "show variables", "set names",
})

# Message noise operations
_MSG_NOISE_OPERATIONS = frozenset({
    "heartbeat", "keepalive", "ping", "pong",
})


def classify_signal(span: Span) -> str:
    """Stage 1: Classify a span as intent_carrying, noise, or ambiguous.

    Uses per-modality noise profiles and known noise patterns.
    Returns SignalClass value string.
    """
    # If explicitly classified as NOISE at L1, respect it.
    # Don't short-circuit on INTENT_CARRYING since that's the dataclass default
    # and doesn't mean "explicitly classified" — let modality-specific checks run.
    if span.signal_class == SignalClass.NOISE:
        return SignalClass.NOISE.value

    modality = span.modality or ""
    ext = span.modality_ext or {}

    # CLI noise detection
    if modality == Modality.CLI.value:
        command = (ext.get("command") or "").lower()
        if command in _CLI_NOISE_COMMANDS:
            return SignalClass.NOISE.value
        return SignalClass.INTENT_CARRYING.value

    # API noise detection
    if modality == Modality.API.value:
        target = (span.interaction.target or "").lower()
        if any(noise in target for noise in _API_NOISE_PATTERNS):
            return SignalClass.NOISE.value
        return SignalClass.INTENT_CARRYING.value

    # DB noise detection
    if modality == Modality.DB.value:
        stmt = (ext.get("db_statement") or "").lower().strip()
        op = (ext.get("db_operation") or "").lower().strip()
        if stmt in _DB_NOISE_OPERATIONS or op in _DB_NOISE_OPERATIONS:
            return SignalClass.NOISE.value
        return SignalClass.INTENT_CARRYING.value

    # Message noise detection
    if modality == Modality.MESSAGE.value:
        operation = (ext.get("operation") or "").lower()
        if operation in _MSG_NOISE_OPERATIONS:
            return SignalClass.NOISE.value
        return SignalClass.INTENT_CARRYING.value

    # Web — most spans are noise (1.5% intent-carrying)
    if modality == "web":
        event = (ext.get("event_type") or "").lower()
        if event in ("click", "submit", "navigate", "input"):
            return SignalClass.INTENT_CARRYING.value
        if event in ("mousemove", "scroll", "hover", "resize", "focus", "blur"):
            return SignalClass.NOISE.value
        return SignalClass.AMBIGUOUS.value

    return SignalClass.AMBIGUOUS.value


# --- Stage 2: Cross-Modality Correlation ---

# Confidence boost by modality count
CORRELATION_BOOSTS: dict[int, float] = {
    1: 1.0,   # Single modality — no boost
    2: 1.5,   # 2-modality match
    3: 2.0,   # 3+ modality match
}


def compute_correlation_boost(modality_count: int) -> float:
    """Stage 2: Compute confidence multiplier based on how many modalities
    contributed to a spike signal.

    Spikes appearing across multiple modalities simultaneously are
    higher-confidence because independent systems confirm the same event.
    """
    if modality_count >= 3:
        return CORRELATION_BOOSTS[3]
    return CORRELATION_BOOSTS.get(modality_count, 1.0)


def correlate_spans(
    spans: list[Span],
    window_ms: int = 5000,
) -> list[list[Span]]:
    """Group spans that share trace_id or fall within a temporal window.

    Returns lists of correlated spans (chains). Spans within the same
    trace_id are always correlated. Cross-trace spans correlate by
    temporal proximity within window_ms.
    """
    # Group by trace_id first
    by_trace: dict[str, list[Span]] = {}
    for span in spans:
        tid = span.trace_id or ""
        by_trace.setdefault(tid, []).append(span)

    chains: list[list[Span]] = []
    for group in by_trace.values():
        if len(group) > 1:
            chains.append(group)
        else:
            chains.append(group)

    return chains


# --- Stage 3: Profile Pattern Matching ---

def classify_spike(
    spans: list[Span],
    system: str = "",
) -> SpikeSignal | None:
    """Stage 3: Classify a correlated span group into a typed spike signal.

    Examines the group for known patterns:
    - FSM transitions → state_change
    - Error codes → error (with retry strategy)
    - Baseline deviations → anomaly
    - Recurring sequences → pattern
    - Health trend shifts → health_shift

    Returns None if the group produces no meaningful spike.
    """
    if not spans:
        return None

    # Filter to intent-carrying spans
    intent_spans = [s for s in spans if classify_signal(s) != SignalClass.NOISE.value]
    if not intent_spans:
        return None

    # Collect modalities
    modalities = list({s.modality for s in intent_spans if s.modality})
    chain = [s.span_id for s in intent_spans if s.span_id]
    modality_count = len(modalities)
    boost = compute_correlation_boost(modality_count)

    # Detect error spikes
    error_spans = [
        s for s in intent_spans
        if s.response_outcome in (
            ResponseOutcome.ERROR.value,
            ResponseOutcome.TIMEOUT.value,
            ResponseOutcome.AUTH_FAILURE.value,
        )
    ]
    if error_spans:
        error_span = error_spans[0]
        error_code = None
        ext = error_span.modality_ext or {}
        if error_span.modality == Modality.MESSAGE.value:
            error_code = ext.get("webhook_code") or ext.get("event_type")
        elif error_span.modality == Modality.API.value:
            resp = error_span.interaction.response
            error_code = resp.error if resp else None

        raw_conf = 0.85
        return SpikeSignal(
            spike_type=SpikeType.ERROR,
            confidence=min(1.0, raw_conf * boost),
            urgency=Urgency.IMMEDIATE if error_span.response_outcome == ResponseOutcome.AUTH_FAILURE.value else Urgency.ATTENTION,
            modalities=modalities,
            chain=chain,
            action_space=_error_action_space(error_span),
            source_system=system or _infer_system(intent_spans),
            correlation_boost=boost,
            raw_confidence=raw_conf,
            error_code=error_code,
            description=f"Error detected: {error_span.response_outcome} on {error_span.interaction.target or 'unknown'}",
        )

    # Detect state transition spikes
    state_spans = [
        s for s in intent_spans
        if s.request_intent == RequestIntent.STATE_TRANSITION.value
    ]
    if state_spans:
        raw_conf = 0.80
        return SpikeSignal(
            spike_type=SpikeType.STATE_CHANGE,
            confidence=min(1.0, raw_conf * boost),
            urgency=Urgency.BACKGROUND,
            modalities=modalities,
            chain=chain,
            action_space=["monitor_outcome", "log_transition"],
            source_system=system or _infer_system(intent_spans),
            correlation_boost=boost,
            raw_confidence=raw_conf,
            description=f"State transition: {state_spans[0].interaction.target or 'unknown'}",
        )

    # Detect rate limiting (health_shift precursor)
    rate_limited = [
        s for s in intent_spans
        if s.response_outcome == ResponseOutcome.RATE_LIMITED.value
    ]
    if rate_limited:
        raw_conf = 0.75
        return SpikeSignal(
            spike_type=SpikeType.HEALTH_SHIFT,
            confidence=min(1.0, raw_conf * boost),
            urgency=Urgency.ATTENTION,
            modalities=modalities,
            chain=chain,
            action_space=["backoff_retry", "reduce_rate", "alert_human"],
            source_system=system or _infer_system(intent_spans),
            correlation_boost=boost,
            raw_confidence=raw_conf,
            description=f"Rate limiting detected on {rate_limited[0].interaction.target or 'unknown'}",
        )

    # Default: mutation or query spikes
    mutation_spans = [
        s for s in intent_spans
        if s.request_intent == RequestIntent.MUTATION.value
    ]
    if mutation_spans and modality_count >= 2:
        raw_conf = 0.70
        return SpikeSignal(
            spike_type=SpikeType.PATTERN,
            confidence=min(1.0, raw_conf * boost),
            urgency=Urgency.BACKGROUND,
            modalities=modalities,
            chain=chain,
            action_space=["log_pattern", "monitor_outcome"],
            source_system=system or _infer_system(intent_spans),
            correlation_boost=boost,
            raw_confidence=raw_conf,
            description=f"Cross-modality mutation pattern across {modalities}",
        )

    return None


def _error_action_space(span: Span) -> list[str]:
    """Determine available L3 actions for an error spike."""
    outcome = span.response_outcome or ""
    intent = span.request_intent or ""

    if outcome == ResponseOutcome.AUTH_FAILURE.value:
        return ["reauth_retry", "escalate_human"]
    if outcome == ResponseOutcome.RATE_LIMITED.value:
        return ["backoff_retry", "reduce_rate"]
    if outcome == ResponseOutcome.TIMEOUT.value:
        if intent == RequestIntent.QUERY.value:
            return ["backoff_retry", "increase_timeout"]
        return ["log_escalate", "investigate"]
    if outcome == ResponseOutcome.ERROR.value:
        if intent == RequestIntent.QUERY.value:
            return ["backoff_retry", "investigate"]
        if intent == RequestIntent.MUTATION.value:
            return ["log_escalate", "check_idempotency"]
        return ["log_escalate", "investigate"]
    return ["investigate"]


def _infer_system(spans: list[Span]) -> str:
    """Best-effort system inference from span data."""
    for span in spans:
        target = span.interaction.target or ""
        if "github" in target.lower():
            return "github"
        if "plaid" in target.lower():
            return "plaid"
        if "aws" in target.lower() or "amazonaws" in target.lower():
            return "aws"
        if "databricks" in target.lower():
            return "databricks"
        ext = span.modality_ext or {}
        if ext.get("system"):
            return ext["system"]
        if ext.get("source_system"):
            return ext["source_system"]
        if ext.get("db_system"):
            return ext["db_system"]
    return "unknown"


# --- Pipeline Entry Point ---

def detect_spikes(
    traces: list[Trace],
    system: str = "",
) -> list[SpikeSignal]:
    """Run the full 3-stage spike detection pipeline on a list of traces.

    Stage 1: Classify each span's signal class
    Stage 2: Correlate spans across modalities
    Stage 3: Classify correlated groups into typed spike signals

    Returns a list of SpikeSignal objects sorted by confidence (descending).
    """
    all_intent_spans: list[Span] = []

    for trace in traces:
        for span in trace.spans:
            sig = classify_signal(span)
            if sig != SignalClass.NOISE.value:
                all_intent_spans.append(span)

    if not all_intent_spans:
        return []

    # Stage 2: correlate
    chains = correlate_spans(all_intent_spans)

    # Stage 3: classify each chain
    spikes: list[SpikeSignal] = []
    for chain in chains:
        spike = classify_spike(chain, system=system)
        if spike is not None:
            spikes.append(spike)

    # Sort by confidence descending
    spikes.sort(key=lambda s: s.confidence, reverse=True)
    return spikes
