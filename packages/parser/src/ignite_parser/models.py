"""Typed data models for IGNITE traces, spans, and findings.

These are the internal representations the Analyzer stage consumes.
Schema validation happens at parse time — if you have a Trace object,
it passed validation.
"""

from __future__ import annotations

from dataclasses import dataclass, field
from datetime import datetime
from enum import Enum
from typing import Any


# --- Enums ---

class AgentRole(str, Enum):
    AUTH_NAVIGATOR = "auth_navigator"
    PROTOCOL_MAPPER = "protocol_mapper"
    DATA_NORMALIZER = "data_normalizer"
    INFRA_SCOUT = "infra_scout"
    AA_BRIDGE = "aa_bridge"
    EXPLORER = "explorer"


class TraceStatus(str, Enum):
    ACTIVE = "active"
    COMPLETED = "completed"
    FAILED = "failed"
    ABANDONED = "abandoned"


class SpanKind(str, Enum):
    API_CALL = "api_call"
    AUTH_FLOW = "auth_flow"
    DOC_READ = "doc_read"
    CONFIG_INSPECT = "config_inspect"
    SDK_INSPECT = "sdk_inspect"
    ERROR_PROBE = "error_probe"
    STATE_TRANSITION = "state_transition"
    ACTION_RESULT = "action_result"
    WORKFLOW_TRACE = "workflow_trace"


class FindingCategory(str, Enum):
    PROTOCOL = "protocol"
    AUTH = "auth"
    DATA_FORMAT = "data_format"
    RATE_LIMIT = "rate_limit"
    ERROR_PATTERN = "error_pattern"
    STATE_MACHINE = "state_machine"
    DEPENDENCY = "dependency"
    COMPLIANCE = "compliance"
    UNDOCUMENTED = "undocumented"
    TRACE_SURFACE = "trace_surface"
    CROSS_MODALITY = "cross_modality"
    INTELLIGENCE = "intelligence"
    ARCHITECTURE = "architecture"
    SECURITY = "security"


class Confidence(str, Enum):
    HIGH = "high"
    MEDIUM = "medium"
    LOW = "low"


class FindingConfidence(str, Enum):
    CONFIRMED = "confirmed"
    PROBABLE = "probable"
    HYPOTHESIS = "hypothesis"


class Actionability(str, Enum):
    IMMEDIATE = "immediate"
    NEEDS_VALIDATION = "needs_validation"
    INFORMATIONAL = "informational"


# --- v0.2 classification enums ---

class Modality(str, Enum):
    API = "api"
    WEB = "web"
    DB = "db"
    CLI = "cli"
    MESSAGE = "message"
    CUSTOM = "custom"


class RequestIntent(str, Enum):
    STATE_TRANSITION = "state_transition"
    QUERY = "query"
    MUTATION = "mutation"
    CONFIG_CHANGE = "config_change"
    DECISION = "decision"


class ResponseOutcome(str, Enum):
    SUCCESS = "success"
    ERROR = "error"
    PARTIAL = "partial"
    TIMEOUT = "timeout"
    RATE_LIMITED = "rate_limited"
    AUTH_FAILURE = "auth_failure"
    ASYNC_PENDING = "async_pending"  # Expected wait state (e.g., PRODUCT_NOT_READY), not an error


class SignalClass(str, Enum):
    INTENT_CARRYING = "intent_carrying"
    NOISE = "noise"
    AMBIGUOUS = "ambiguous"


class DeltaFromPrior(str, Enum):
    NONE = "none"
    PARTIAL = "partial"
    FULL = "full"


class StateTransitionSubtype(str, Enum):
    LIFECYCLE_START = "lifecycle_start"
    STATE_CHANGE = "state_change"


class SpanStructure(str, Enum):
    """How spans within a session relate to each other structurally."""
    INDEPENDENT = "independent"        # API — each span is self-contained
    NARRATIVE = "narrative"            # CLI — sequence tells a story
    TRANSACTIONAL = "transactional"    # DB — atomically grouped operations
    NAVIGATIONAL = "navigational"      # Web — page/state navigation flow
    SEQUENTIAL = "sequential"          # Generic ordered sequence
    CONVERSATIONAL = "conversational"  # Message — request/reply exchanges
    THREADED = "threaded"              # Message — parallel conversation threads


class SessionIntent(str, Enum):
    """Purpose of the entire session — why the agent executed this sequence."""
    VALIDATION = "validation"          # assess → verify → execute → measure
    DEBUGGING = "debugging"            # reproduce → isolate → fix → verify
    DEPLOYMENT = "deployment"          # pull → build → test → deploy
    EXPLORATION = "exploration"        # navigate → inspect → read → understand
    CONFIGURATION = "configuration"    # read → modify → test → apply
    MONITORING = "monitoring"          # observe → alert → diagnose → resolve


class CorrectionAction(str, Enum):
    """ErrP correction strategy — what to do when intent diverges from outcome."""
    BACKOFF_RETRY = "backoff_retry"
    REAUTH_RETRY = "reauth_retry"
    LOG_ESCALATE = "log_escalate"
    IDEMPOTENCY_RETRY = "idempotency_retry"
    RESTART_FLOW = "restart_flow"
    ESCALATE_HUMAN = "escalate_human"


# --- Data classes ---

@dataclass
class ContainsContext:
    """A cross-modality context reference within a span."""
    modality: str = ""
    relationship: str = ""  # processes, reads_from, writes_to, triggers, analyzes, learns_from, wraps, embeds
    active: bool = True     # True = data flows now; False = architecturally possible but not realized
    context_id: str | None = None
    description: str | None = None


@dataclass
class Request:
    url: str | None = None
    headers: dict[str, Any] | None = None
    params: dict[str, Any] | None = None
    body: dict[str, Any] | None = None


@dataclass
class Response:
    status_code: int | None = None
    headers: dict[str, Any] | None = None
    body_summary: str = ""
    body_schema: dict[str, Any] | None = None
    error: str | None = None


@dataclass
class Interaction:
    target: str = ""
    method: str | None = None
    request: Request = field(default_factory=Request)
    response: Response = field(default_factory=Response)


@dataclass
class Observation:
    what_happened: str = ""
    what_learned: str = ""
    confidence: Confidence = Confidence.LOW
    surprises: str | None = None
    questions_raised: list[str] = field(default_factory=list)


@dataclass
class State:
    preconditions: list[str] = field(default_factory=list)
    postconditions: list[str] = field(default_factory=list)
    state_changes: list[str] = field(default_factory=list)


@dataclass
class Relationships:
    depends_on: list[str] = field(default_factory=list)
    enables: list[str] = field(default_factory=list)
    contradicts: list[str] = field(default_factory=list)
    refines: list[str] = field(default_factory=list)


@dataclass
class ExpectedOutcome:
    """What the agent/system expected to happen — set before execution."""
    description: str = ""
    confidence: float = 0.0  # 0.0-1.0 — how confident was the prediction


@dataclass
class ActualOutcome:
    """What actually happened — set after execution."""
    description: str = ""


@dataclass
class Span:
    span_id: str = ""
    trace_id: str = ""
    parent_span_id: str | None = None
    sequence: int = 0
    kind: SpanKind = SpanKind.API_CALL
    started_at: datetime | None = None
    ended_at: datetime | None = None
    duration_ms: int = 0
    interaction: Interaction = field(default_factory=Interaction)
    observation: Observation = field(default_factory=Observation)
    state: State = field(default_factory=State)
    relationships: Relationships = field(default_factory=Relationships)
    tags: list[str] = field(default_factory=list)
    metadata: dict[str, Any] = field(default_factory=dict)
    # v0.2 classification fields (all optional with defaults for backward compat)
    modality: Modality = Modality.API
    request_intent: RequestIntent = RequestIntent.QUERY
    response_outcome: ResponseOutcome = ResponseOutcome.SUCCESS
    signal_class: SignalClass = SignalClass.INTENT_CARRYING
    delta_from_prior: DeltaFromPrior = DeltaFromPrior.FULL
    state_transition_subtype: StateTransitionSubtype | None = None
    correlation_ids: list[str] = field(default_factory=list)
    # v0.2 feedback divergence fields
    expected_outcome: ExpectedOutcome | None = None
    actual_outcome: ActualOutcome | None = None
    divergence_score: float | None = None
    # v0.2 modality extension (typed per-modality interaction data)
    modality_ext: dict[str, Any] | None = None
    # Schema v13 fields
    span_structure: SpanStructure | None = None
    contains_contexts: list[ContainsContext] = field(default_factory=list)
    compression_ratio: float | None = None


@dataclass
class Finding:
    finding_id: str = ""
    trace_id: str = ""
    source_spans: list[str] = field(default_factory=list)
    category: FindingCategory = FindingCategory.PROTOCOL
    title: str = ""
    description: str = ""
    evidence: list[str] = field(default_factory=list)
    confidence: FindingConfidence = FindingConfidence.HYPOTHESIS
    actionability: Actionability = Actionability.INFORMATIONAL
    related_findings: list[str] = field(default_factory=list)
    tags: list[str] = field(default_factory=list)
    metadata: dict[str, Any] = field(default_factory=dict)


@dataclass
class Trace:
    schema_version: str = "0.1"
    trace_id: str = ""
    agent_id: str = ""
    agent_role: AgentRole = AgentRole.EXPLORER
    system: str = ""
    study_channel: str = ""
    session_id: str = ""
    started_at: datetime | None = None
    ended_at: datetime | None = None
    status: TraceStatus = TraceStatus.ACTIVE
    objective: str = ""
    findings_summary: str | None = None
    spans: list[Span] = field(default_factory=list)
    findings: list[Finding] = field(default_factory=list)
    metadata: dict[str, Any] = field(default_factory=dict)
    # Schema v13 field
    session_intent: SessionIntent | None = None


# --- Parse result ---

@dataclass
class ValidationError:
    path: str
    message: str
    severity: str = "error"  # "error" or "warning"


@dataclass
class ParseResult:
    """Result of parsing a single trace or batch of traces."""
    traces: list[Trace] = field(default_factory=list)
    errors: list[ValidationError] = field(default_factory=list)
    warnings: list[ValidationError] = field(default_factory=list)

    @property
    def valid_count(self) -> int:
        return len(self.traces)

    @property
    def error_count(self) -> int:
        return len(self.errors)

    @property
    def warning_count(self) -> int:
        return len(self.warnings)

    @property
    def ok(self) -> bool:
        return self.error_count == 0
