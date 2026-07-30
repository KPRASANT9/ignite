"""Auto-sanitization for trace data — redacts secrets and PII at capture time.

Follows the same patterns as ignite_parser.parser but applied proactively
during trace construction, not reactively at parse time.
"""

from __future__ import annotations

import re
from typing import Any

# Patterns that indicate secrets
SECRET_PATTERNS = [
    re.compile(r"(sk|pk|access|secret|token|key)[_-]?(live|test|prod)?[_-]?[a-zA-Z0-9]{20,}", re.I),
    re.compile(r"Bearer\s+[a-zA-Z0-9._-]{20,}", re.I),
    re.compile(r"Basic\s+[a-zA-Z0-9+/=]{20,}", re.I),
]

# PII patterns
PII_PATTERNS = [
    re.compile(r"\b\d{3}-\d{2}-\d{4}\b"),  # SSN
    re.compile(r"\b\d{4}[\s-]?\d{4}[\s-]?\d{4}[\s-]?\d{4}\b"),  # Card number
]

REDACTED = "[REDACTED]"
PII_REDACTED = "[PII_REDACTED]"
WEB_INPUT_MASKED = "***"
MAX_BODY_LENGTH = 1024

# Keys in modality_ext dicts that hold web input values needing privacy checks
_WEB_SENSITIVE_NAME_PATTERNS = re.compile(
    r"(password|passwd|ssn|social.?security|card.?number|cvv|cvc|"
    r"credit.?card|account.?number|routing.?number|pin)",
    re.I,
)


def sanitize_string(value: str) -> str:
    """Redact secrets and PII from a string value."""
    result = value
    for pattern in SECRET_PATTERNS:
        result = pattern.sub(REDACTED, result)
    for pattern in PII_PATTERNS:
        result = pattern.sub(PII_REDACTED, result)
    return result


def sanitize_dict(data: dict[str, Any]) -> dict[str, Any]:
    """Recursively sanitize a dictionary, redacting secrets and PII."""
    result = {}
    for key, value in data.items():
        lower_key = key.lower()
        # Keys that are always sensitive
        if any(s in lower_key for s in ("secret", "password", "token", "authorization", "credential")):
            result[key] = REDACTED
        elif isinstance(value, list):
            result[key] = [sanitize_value(item) for item in value]
        elif isinstance(value, str):
            result[key] = sanitize_string(value)
        elif isinstance(value, dict):
            result[key] = sanitize_dict(value)
        else:
            result[key] = value
    return result


def sanitize_value(value: Any) -> Any:
    """Sanitize any value — string, dict, list, or passthrough."""
    if isinstance(value, str):
        return sanitize_string(value)
    if isinstance(value, dict):
        return sanitize_dict(value)
    if isinstance(value, list):
        return [sanitize_value(item) for item in value]
    return value


def sanitize_web_input(
    value: str | None,
    element_name: str | None = None,
    input_type: str | None = None,
) -> str | None:
    """Mask web input values for sensitive fields (P0 privacy requirement).

    Returns '***' if the input is for a sensitive field, original value otherwise.
    Detection: input_type='password' OR element_name matches sensitive patterns.
    """
    if value is None:
        return None
    if input_type and input_type.lower() == "password":
        return WEB_INPUT_MASKED
    if element_name and _WEB_SENSITIVE_NAME_PATTERNS.search(element_name):
        return WEB_INPUT_MASKED
    return value


def sanitize_cli_command(command_line: str) -> str:
    """Scrub inline secrets from a CLI command string (P0 privacy requirement).

    Delegates to cli_ext.scrub_cli_secrets() for pattern matching.
    Also applies general secret pattern scrubbing.
    """
    from ignite_trace.extensions.cli_ext import scrub_cli_secrets
    result = scrub_cli_secrets(command_line)
    return sanitize_string(result)


def truncate_body(body_summary: str, max_length: int = MAX_BODY_LENGTH) -> str:
    """Truncate body summaries that exceed max length."""
    if len(body_summary) <= max_length:
        return body_summary
    return body_summary[:max_length - 3] + "..."
