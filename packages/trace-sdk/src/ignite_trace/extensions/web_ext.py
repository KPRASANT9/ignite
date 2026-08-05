"""Web modality extension — browser/web interactions.

Captures web-specific interaction details: DOM selectors, page URLs,
accessibility tree state, viewport context. This is the Playwright MCP
integration point.

Privacy: input_value MUST be masked for sensitive fields (password, SSN,
card number). Use mask_sensitive_input() before setting input_value.

Reference: Playwright AXTree, WAI-ARIA roles.
"""

from __future__ import annotations

import re
from dataclasses import dataclass, field
from typing import Any

# Element names/roles that indicate sensitive input
_SENSITIVE_NAME_PATTERNS = re.compile(
    r"(password|passwd|ssn|social.?security|card.?number|cvv|cvc|"
    r"credit.?card|account.?number|routing.?number|pin)",
    re.I,
)


def mask_sensitive_input(
    value: str | None,
    element_name: str | None = None,
    element_role: str | None = None,
    input_type: str | None = None,
) -> str | None:
    """Mask input values for sensitive fields. Returns '***' if sensitive.

    Detection rules (any match → mask):
    - input_type is 'password'
    - element_name matches sensitive patterns (password, ssn, card, etc.)
    """
    if value is None:
        return None
    if input_type and input_type.lower() == "password":
        return "***"
    if element_name and _SENSITIVE_NAME_PATTERNS.search(element_name):
        return "***"
    return value


@dataclass
class WebExt:
    """Typed web/browser interaction extension."""
    page_url: str | None = None         # Current page URL
    page_title: str | None = None       # Current page title
    selector: str | None = None         # DOM selector used
    action: str | None = None           # "click", "type", "navigate", "scroll", "wait", "observe"
    element_role: str | None = None     # ARIA role: link, button, textbox, menuitem, etc.
    element_name: str | None = None     # Accessible name of element (ARIA label / visible label)
    element_text: str | None = None     # Visible text of target element
    input_value: str | None = None      # Value typed/entered (MUST be masked for sensitive fields)
    input_type: str | None = None       # HTML input type: text, password, email, etc.
    viewport_width: int | None = None   # Browser viewport width
    viewport_height: int | None = None  # Browser viewport height
    screenshot_ref: str | None = None   # Reference to screenshot artifact
    element_state: dict[str, Any] | None = None  # checked, selected, expanded, disabled
    snapshot_hash: str | None = None    # Hash of AXTree state for delta compression
    delta_from: str | None = None       # span_id of previous snapshot (delta chain)

    def to_dict(self) -> dict[str, Any]:
        d: dict[str, Any] = {"type": "web_ext"}
        if self.page_url is not None:
            d["page_url"] = self.page_url
        if self.page_title is not None:
            d["page_title"] = self.page_title
        if self.selector is not None:
            d["selector"] = self.selector
        if self.action is not None:
            d["action"] = self.action
        if self.element_role is not None:
            d["element_role"] = self.element_role
        if self.element_name is not None:
            d["element_name"] = self.element_name
        if self.element_text is not None:
            d["element_text"] = self.element_text
        if self.input_value is not None:
            d["input_value"] = self.input_value
        if self.input_type is not None:
            d["input_type"] = self.input_type
        if self.viewport_width is not None:
            d["viewport_width"] = self.viewport_width
        if self.viewport_height is not None:
            d["viewport_height"] = self.viewport_height
        if self.screenshot_ref is not None:
            d["screenshot_ref"] = self.screenshot_ref
        if self.element_state is not None:
            d["element_state"] = self.element_state
        if self.snapshot_hash is not None:
            d["snapshot_hash"] = self.snapshot_hash
        if self.delta_from is not None:
            d["delta_from"] = self.delta_from
        return d

    @classmethod
    def from_dict(cls, data: dict[str, Any]) -> WebExt:
        return cls(
            page_url=data.get("page_url"),
            page_title=data.get("page_title"),
            selector=data.get("selector"),
            action=data.get("action"),
            element_role=data.get("element_role"),
            element_name=data.get("element_name"),
            element_text=data.get("element_text"),
            input_value=data.get("input_value"),
            input_type=data.get("input_type"),
            viewport_width=data.get("viewport_width"),
            viewport_height=data.get("viewport_height"),
            screenshot_ref=data.get("screenshot_ref"),
            element_state=data.get("element_state"),
            snapshot_hash=data.get("snapshot_hash"),
            delta_from=data.get("delta_from"),
        )
