"""Entrypoint for the Gmail gateway Connection Context Adapter.

Exposes two MCP tools over streamable-http: ``execute_action`` and
``list_connections``. The bearer token carrying the caller's platform JWT is
read from the incoming HTTP ``Authorization`` header (never from tool args —
see ``src.adapter.identity``); each call opens its own DB session via the
canonical ``get_session_factory()`` pattern used across the codebase
(``async with get_session_factory()() as db:``).

Run with: ``python run_adapter.py`` (from ``backend/``).
"""

from __future__ import annotations

import logging

from fastmcp import FastMCP
from fastmcp.server.dependencies import get_http_headers

from src.adapter.server import handle_execute_action, handle_list_connections
from src.models.database import get_session_factory

logger = logging.getLogger(__name__)

adapter = FastMCP("gmail-gateway-adapter")

_HOST = "0.0.0.0"  # noqa: S104 - intentional: gateway must be reachable off-host
_PORT = 8100


def _bearer_token() -> str:
    """Extract the raw bearer token from the inbound Authorization header.

    Never trusts a caller-supplied identity in tool args — this is the only
    place the adapter reads the platform JWT from.
    """
    headers = get_http_headers(include={"authorization"})
    raw = headers.get("authorization", "")
    if raw.lower().startswith("bearer "):
        return raw[len("bearer ") :]
    return raw


@adapter.tool()
async def execute_action(
    actionId: str,  # noqa: N803 - matches OpenConnector's camelCase tool schema
    input: dict,
    account_alias: str | None = None,
) -> dict:
    """Execute an allowlisted Gmail action through the caller's owned connection."""
    token = _bearer_token()
    args = {"actionId": actionId, "input": input}
    if account_alias is not None:
        args["account_alias"] = account_alias
    async with get_session_factory()() as db:
        return await handle_execute_action(db, token=token, args=args)


@adapter.tool()
async def list_connections() -> dict:
    """List only the calling principal's own connections (never global enumeration)."""
    token = _bearer_token()
    async with get_session_factory()() as db:
        return await handle_list_connections(db, token=token)


if __name__ == "__main__":
    adapter.run(transport="http", host=_HOST, port=_PORT)
