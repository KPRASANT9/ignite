"""Web modality extension — browser/web interactions (Phase 2).

Captures web-specific interaction details: DOM selectors, page URLs,
accessibility tree state, viewport context. This is the Playwright MCP
integration point — Phase 2 after API and DB modalities are validated.

For MVP, the dataclass is defined but not wired into active pipelines.
"""

from __future__ import annotations

from dataclasses import dataclass, field
from typing import Any


@dataclass
class WebExt:
    """Typed web/browser interaction extension (Phase 2)."""
    page_url: str | None = None         # Current page URL
    page_title: str | None = None       # Current page title
    selector: str | None = None         # DOM selector used
    action: str | None = None           # "click", "type", "navigate", "scroll", "wait"
    element_role: str | None = None     # ARIA role of target element
    element_text: str | None = None     # Visible text of target element
    viewport_width: int | None = None   # Browser viewport width
    viewport_height: int | None = None  # Browser viewport height
    screenshot_ref: str | None = None   # Reference to screenshot artifact

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
        if self.element_text is not None:
            d["element_text"] = self.element_text
        if self.viewport_width is not None:
            d["viewport_width"] = self.viewport_width
        if self.viewport_height is not None:
            d["viewport_height"] = self.viewport_height
        if self.screenshot_ref is not None:
            d["screenshot_ref"] = self.screenshot_ref
        return d

    @classmethod
    def from_dict(cls, data: dict[str, Any]) -> WebExt:
        return cls(
            page_url=data.get("page_url"),
            page_title=data.get("page_title"),
            selector=data.get("selector"),
            action=data.get("action"),
            element_role=data.get("element_role"),
            element_text=data.get("element_text"),
            viewport_width=data.get("viewport_width"),
            viewport_height=data.get("viewport_height"),
            screenshot_ref=data.get("screenshot_ref"),
        )
