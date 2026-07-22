"""Post-OAuth eager MCP discovery keys off MCP server names, not source names.

gmail and calendar are native-connector perception sources whose MCP tools are
served by the single ``google-workspace`` server. The eager-discovery step must
resolve sources -> provider -> MCP server (deduped); using the raw source name
made ``reload_server(ws, "gmail")`` fail with a spurious "no active installation"
warning and silently skipped discovery for the real server.
"""

from src.api.routes_auth_oauth_integration import _mcp_servers_for_sources


def test_google_sources_collapse_to_single_server():
    assert _mcp_servers_for_sources(["gmail", "calendar"]) == ["google-workspace"]


def test_non_google_source_maps_to_same_named_server():
    assert _mcp_servers_for_sources(["github"]) == ["github"]


def test_order_preserving_dedup_across_providers():
    # calendar + gmail still collapse to one google-workspace entry; github kept.
    assert _mcp_servers_for_sources(["calendar", "github", "gmail"]) == [
        "google-workspace",
        "github",
    ]


def test_empty_sources():
    assert _mcp_servers_for_sources([]) == []
