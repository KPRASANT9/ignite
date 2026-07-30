"""API modality extension — types the existing interaction.request/response.

The existing SpanBuilder.request()/response() methods already capture API
interactions. This extension formalizes the typing without renaming anything.
It's the implicit default for modality="api" spans.
"""

from __future__ import annotations

from dataclasses import dataclass, field
from typing import Any


@dataclass
class ApiExt:
    """Typed API interaction extension.

    Maps directly to the existing interaction.request/response fields.
    This extension exists so all modalities share the same typed interface.
    """
    method: str | None = None           # HTTP method: GET, POST, PUT, DELETE, PATCH
    url: str | None = None              # Full URL
    path: str | None = None             # URL path (e.g., /transactions/get)
    status_code: int | None = None      # HTTP response status code
    request_content_type: str | None = None   # e.g., application/json
    response_content_type: str | None = None  # e.g., application/json
    request_body_size: int | None = None      # bytes
    response_body_size: int | None = None     # bytes
    idempotency_key: str | None = None  # For mutation safety detection

    def to_dict(self) -> dict[str, Any]:
        d: dict[str, Any] = {"type": "api_ext"}
        if self.method is not None:
            d["method"] = self.method
        if self.url is not None:
            d["url"] = self.url
        if self.path is not None:
            d["path"] = self.path
        if self.status_code is not None:
            d["status_code"] = self.status_code
        if self.request_content_type is not None:
            d["request_content_type"] = self.request_content_type
        if self.response_content_type is not None:
            d["response_content_type"] = self.response_content_type
        if self.request_body_size is not None:
            d["request_body_size"] = self.request_body_size
        if self.response_body_size is not None:
            d["response_body_size"] = self.response_body_size
        if self.idempotency_key is not None:
            d["idempotency_key"] = self.idempotency_key
        return d

    @classmethod
    def from_dict(cls, data: dict[str, Any]) -> ApiExt:
        return cls(
            method=data.get("method"),
            url=data.get("url"),
            path=data.get("path"),
            status_code=data.get("status_code"),
            request_content_type=data.get("request_content_type"),
            response_content_type=data.get("response_content_type"),
            request_body_size=data.get("request_body_size"),
            response_body_size=data.get("response_body_size"),
            idempotency_key=data.get("idempotency_key"),
        )
