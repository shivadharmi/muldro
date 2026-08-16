"""Inbound-request helpers for the gateway adapter.

Single source of the bearer-token extraction shared by the entrypoint's
decorated tools and the warm-started named-action tools. Never reads identity
from tool args — only from the HTTP Authorization header.
"""

from __future__ import annotations

from fastmcp.server.dependencies import get_http_headers


def bearer_token() -> str:
    """Extract the raw bearer token from the inbound Authorization header."""
    headers = get_http_headers(include={"authorization"})
    raw = headers.get("authorization", "")
    if raw.lower().startswith("bearer "):
        return raw[len("bearer ") :]
    return raw
