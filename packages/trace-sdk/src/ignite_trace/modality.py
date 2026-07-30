"""v0.2 classification enums for the universal trace surface.

These enums enable L1 spike-sorting: every span is classified at capture time
so L2 decoders consume pre-classified intent signals, not raw telemetry.

Design decisions:
- request_intent and response_outcome are SEPARATE fields (not conflated).
  A 429 on /transactions/get is a query that got rate_limited, not an "error intent."
- signal_class is assigned at L1 (SpanProcessor), not L2.
- modality identifies which digital system surface produced the span.
"""

from enum import Enum


class Modality(str, Enum):
    """Digital system surface type that produced this span."""
    API = "api"
    WEB = "web"
    DB = "db"
    CLI = "cli"
    MESSAGE = "message"
    CUSTOM = "custom"


class RequestIntent(str, Enum):
    """What the agent/system attempted — the request-level classification.

    Five universal classes validated against 34 API interactions across
    Plaid, Stripe, and GitHub (ProductExplorer trace-intent-taxonomy-004).
    """
    STATE_TRANSITION = "state_transition"  # Lifecycle start or state change
    QUERY = "query"                        # Read operation
    MUTATION = "mutation"                  # Write with side effects
    CONFIG_CHANGE = "config_change"        # System configuration modified
    DECISION = "decision"                  # Operator-level choice (L3)


class ResponseOutcome(str, Enum):
    """What actually happened — the response-level classification.

    Separated from request_intent so L2 can correlate:
    which intents produce which outcomes?
    """
    SUCCESS = "success"
    ERROR = "error"
    PARTIAL = "partial"
    TIMEOUT = "timeout"
    RATE_LIMITED = "rate_limited"
    AUTH_FAILURE = "auth_failure"
    ASYNC_PENDING = "async_pending"  # Expected wait state (e.g., PRODUCT_NOT_READY), not an error


class SignalClass(str, Enum):
    """L1 spike-sorting classification.

    Assigned at capture time. L2 focuses decode effort on
    intent_carrying spans while preserving noise for audit.
    """
    INTENT_CARRYING = "intent_carrying"
    NOISE = "noise"
    AMBIGUOUS = "ambiguous"


class DeltaFromPrior(str, Enum):
    """How much changed since the previous span of the same type.

    Region4Web shows 52.9% of web observations carry zero new information.
    This field enables compression without data loss.
    """
    NONE = "none"      # No change from previous
    PARTIAL = "partial"  # Some fields changed
    FULL = "full"      # Entirely new observation


class StateTransitionSubtype(str, Enum):
    """Distinguishes lifecycle starts from state changes within a lifecycle.

    Stripe PaymentIntent.create = lifecycle_start
    Plaid ITEM_LOGIN_REQUIRED = state_change
    """
    LIFECYCLE_START = "lifecycle_start"
    STATE_CHANGE = "state_change"


class SpanStructure(str, Enum):
    """How spans within a session relate to each other structurally.

    CLI sessions are narrative (command sequence tells a story).
    DB sessions are transactional (atomically grouped operations).
    API interactions are typically independent.
    """
    INDEPENDENT = "independent"
    NARRATIVE = "narrative"
    TRANSACTIONAL = "transactional"
    NAVIGATIONAL = "navigational"
    SEQUENTIAL = "sequential"
    CONVERSATIONAL = "conversational"
    THREADED = "threaded"


class SessionIntent(str, Enum):
    """Purpose of the entire session — why the agent executed this sequence.

    Validated against trace-cli-session-006 (CLI) and trace-db-session-007 (DB).
    CLI and Web have the strongest narrative signal; universal but modality-weighted.
    """
    VALIDATION = "validation"
    DEBUGGING = "debugging"
    DEPLOYMENT = "deployment"
    EXPLORATION = "exploration"
    CONFIGURATION = "configuration"
    MONITORING = "monitoring"
