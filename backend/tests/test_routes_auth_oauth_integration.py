"""Post-OAuth eager MCP discovery keys off MCP server names, not source names.

A perception source is not necessarily an MCP server name, so discovery must
translate source -> OAuth provider -> server(s) via ``provider_map`` and dedupe:
reloading a *source* name as a server made ``reload_server`` fail with a
spurious "no active installation" warning and silently skipped discovery.

Scope note: this helper now only ever sees the natively-authenticated providers
(notion, atlassian). gmail/calendar/github connect through the OpenConnector
gateway, whose callback never reaches here.
"""

from src.api.routes_auth_oauth_integration import _mcp_servers_for_sources


def test_source_maps_to_same_named_server():
    assert _mcp_servers_for_sources(["notion"]) == ["notion"]


def test_order_preserving_dedup():
    assert _mcp_servers_for_sources(["notion", "atlassian", "notion"]) == [
        "notion",
        "atlassian",
    ]


def test_empty_sources():
    assert _mcp_servers_for_sources([]) == []
