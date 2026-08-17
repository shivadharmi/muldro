"""Real-HTTP e2e: adapter warm-start actually registers named tools over MCP.

Task 8 wired ``run_adapter.warm_start()`` to run before ``adapter.run(...)`` in
``if __name__ == "__main__":``, so a live adapter process serves one named
FastMCP tool per allowlisted action (e.g. ``hackernews_get_top_stories`` — the
agent-legal, underscore form of the dotted OC actionId
``hackernews.get_top_stories``; see ``gateway_naming.action_id_to_tool_name``)
in addition to the generic ``execute_action`` / ``list_connections`` tools.
This connects a real FastMCP client to the adapter's ``/mcp`` and checks the
named tool shows up with a non-empty object input schema — the actual
regression this task guards against: warm-start silently not running, or
running but never reaching the served tool list. (Under the ``hackernews``
profile the served schema is the opaque fallback, since
``gateway_actions.gmail.GMAIL_ACTIONS`` is gmail-only; this test verifies the named
tool is *served*, not its exact schema shape.)

Runs only when the stack is up (conftest gates on adapter :8100) AND the
adapter was started under ``JARVIS_GATEWAY_PROVIDER=hackernews`` (spike
§8/no-auth profile — no OAuth needed, so this needs no platform JWT and no
DB seeding, unlike ``test_adapter_openconnector_live.py``). ``list_tools()``
needs no bearer token: the adapter has no transport-level auth middleware,
only the per-call ``bearer_token()`` reads inside ``execute_action`` /
``list_connections`` / the warm-started handlers themselves.
"""

from __future__ import annotations

import os

import pytest
from fastmcp import Client

from src.integrations.gateway_naming import action_id_to_tool_name

_ADAPTER_MCP = "http://127.0.0.1:8100/mcp"
_HN_TOOL = action_id_to_tool_name("hackernews.get_top_stories")

pytestmark = pytest.mark.skipif(
    os.environ.get("JARVIS_GATEWAY_PROVIDER", "gmail") != "hackernews",
    reason=(
        "requires the adapter process to be running with "
        "JARVIS_GATEWAY_PROVIDER=hackernews (no-auth profile) — "
        "see infra/gateway/docker-compose.integration.yml"
    ),
)


async def test_warm_started_named_tool_is_served_over_live_mcp():
    async with Client(_ADAPTER_MCP) as client:
        tools = await client.list_tools()

    by_name = {t.name: t for t in tools}
    assert _HN_TOOL in by_name, f"expected warm-started {_HN_TOOL!r}, got {sorted(by_name)}"

    schema = by_name[_HN_TOOL].inputSchema
    assert schema, f"{_HN_TOOL} was served with an empty input schema"
    assert schema.get("type") == "object"

    # the generic tools are still present alongside the named ones
    assert "execute_action" in by_name
    assert "list_connections" in by_name
