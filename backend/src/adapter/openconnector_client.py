"""Thin wrapper over the shared OpenConnector MCP endpoint.

The Gmail gateway slice routes tool calls through a single shared
OpenConnector MCP server rather than a per-user connector process. This
module authenticates to that shared endpoint with the static runtime
token configured for the gateway (``openconnector_runtime_token``) —
per-user isolation is NOT enforced here; it is the adapter layer's job
(``force_connection_name`` in ``src.adapter.enforcement``) to force the
correct per-user ``connectionName`` onto every call before it reaches
this module.
"""

from __future__ import annotations

from fastmcp import Client
from fastmcp.client.auth import BearerAuth

from src.config.settings import get_settings


async def _client_call(tool_name: str, args: dict) -> dict:
    """Open a session against the shared OpenConnector MCP endpoint and call one tool.

    Kept as a separate seam (rather than inlined into ``call_openconnector``)
    so tests can patch out the real MCP round trip.
    """
    s = get_settings()
    token = s.openconnector_runtime_token
    auth = BearerAuth(token=token) if token else None
    client_ctx = (
        Client(s.openconnector_mcp_url, auth=auth) if auth else Client(s.openconnector_mcp_url)
    )
    async with client_ctx as client:
        result = await client.call_tool(tool_name, args)
    return result


async def call_openconnector(tool_name: str, args: dict) -> dict:
    """Single call point to the shared OpenConnector MCP endpoint."""
    return await _client_call(tool_name, args)
