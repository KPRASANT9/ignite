"""Web modality profile — error taxonomy, intent classification, and ErrP overrides.

Hardest modality: session-shaped, intent-ambiguous, compression-heavy.
Same template as DB profile: error codes → retry strategies,
element role → intent classification, action → risk weighting.

Source: Honey Web Modality Product Spec.
"""

from __future__ import annotations


# --- Web error taxonomy ---
# 4 families, mapped to retry strategies.
# Mirrors DB/Plaid pattern: error code determines retry safety.

WEB_ERROR_CODES: dict[str, dict[str, str]] = {
    # Navigation errors — conditional retry (page may load on retry)
    "navigation_timeout": {"family": "navigation", "retry": "conditional"},
    "page_not_found": {"family": "navigation", "retry": "never"},
    "ssl_error": {"family": "navigation", "retry": "never"},
    "dns_resolution_failed": {"family": "navigation", "retry": "conditional"},
    "connection_refused": {"family": "navigation", "retry": "conditional"},
    # Element errors — conditional retry (wait + re-query AXTree)
    "element_not_found": {"family": "element", "retry": "conditional"},
    "element_not_visible": {"family": "element", "retry": "conditional"},
    "element_not_interactable": {"family": "element", "retry": "conditional"},
    "stale_element": {"family": "element", "retry": "conditional"},
    # Session errors — never retry (re-auth required)
    "session_expired": {"family": "session", "retry": "never"},
    "auth_required": {"family": "session", "retry": "never"},
    "csrf_token_invalid": {"family": "session", "retry": "never"},
    # Browser errors — unsafe (environment issue)
    "browser_crashed": {"family": "browser", "retry": "unsafe"},
    "out_of_memory": {"family": "browser", "retry": "unsafe"},
    "context_destroyed": {"family": "browser", "retry": "unsafe"},
}


# --- ErrP correction overrides ---

WEB_ERROR_CODE_CORRECTIONS: dict[str, str] = {
    # Navigation — backoff retry for transient, escalate for permanent
    "navigation_timeout": "backoff_retry",
    "page_not_found": "escalate_human",
    "ssl_error": "escalate_human",
    "dns_resolution_failed": "backoff_retry",
    "connection_refused": "backoff_retry",
    # Element — idempotency retry (wait + re-query AXTree)
    "element_not_found": "idempotency_retry",
    "element_not_visible": "idempotency_retry",
    "element_not_interactable": "idempotency_retry",
    "stale_element": "idempotency_retry",
    # Session — restart flow (re-auth required)
    "session_expired": "restart_flow",
    "auth_required": "restart_flow",
    "csrf_token_invalid": "restart_flow",
    # Browser — log + escalate
    "browser_crashed": "log_escalate",
    "out_of_memory": "log_escalate",
    "context_destroyed": "log_escalate",
}


def lookup_web_errp_override(error_code: str) -> str | None:
    """Look up web-specific ErrP correction override by error code.

    Returns CorrectionAction value string, or None if unknown error code.
    """
    return WEB_ERROR_CODE_CORRECTIONS.get(error_code)


# --- Intent classification ---

# Roles that indicate read/navigation intent
_QUERY_ROLES = frozenset({"link", "menuitem", "tab", "treeitem", "option"})

# Roles that indicate write/action intent
_MUTATION_ROLES = frozenset({"button", "switch", "checkbox", "radio", "slider"})

# Roles that indicate input (mutation when value changes)
_INPUT_ROLES = frozenset({"textbox", "combobox", "searchbox", "spinbutton"})


def classify_web_intent(
    element_role: str | None = None,
    action: str | None = None,
) -> str:
    """Classify web interaction intent from element role and action.

    Role-based classification:
    - link/menuitem/tab → query (navigation/read)
    - button/switch/checkbox → mutation (action/write)
    - textbox with input → mutation (data entry)
    - navigate/observe → query
    - scroll/wait → query (passive)

    Returns RequestIntent value string.
    """
    if element_role:
        role_lower = element_role.lower()
        if role_lower in _QUERY_ROLES:
            return "query"
        if role_lower in _MUTATION_ROLES:
            return "mutation"
        if role_lower in _INPUT_ROLES:
            return "mutation"

    if action:
        action_lower = action.lower()
        if action_lower in ("navigate", "scroll", "wait", "observe"):
            return "query"
        if action_lower in ("click", "type", "submit", "upload", "drag"):
            return "mutation"

    return "query"  # safe default — observation


def classify_web_risk(
    action: str | None = None,
    element_role: str | None = None,
) -> str:
    """Classify web interaction risk level.

    Risk hierarchy: button actions > input mutations > link navigations > observations.
    Returns: "low", "medium", "high".
    """
    if element_role:
        role_lower = element_role.lower()
        if role_lower in _MUTATION_ROLES:
            return "medium"  # button clicks can have side effects
        if role_lower in _INPUT_ROLES:
            return "medium"  # data entry

    if action:
        action_lower = action.lower()
        if action_lower in ("submit", "upload"):
            return "high"
        if action_lower in ("click", "type", "drag"):
            return "medium"
        if action_lower in ("navigate", "scroll", "wait", "observe"):
            return "low"

    return "low"


# --- Structural anomaly detection ---

def is_web_structural_anomaly(action: str | None, error_code: str) -> bool:
    """Detect structurally impossible web errors.

    An 'observe' action producing an element_not_found is anomalous —
    observation should not require element interaction.
    """
    if action and action.lower() == "observe" and error_code in (
        "element_not_interactable", "stale_element"
    ):
        return True
    return False
