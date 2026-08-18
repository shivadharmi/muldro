"""One gateway adapter endpoint serves several installations' tools.

`list_tools()` against the shared `/mcp` endpoint returns the union of every
provider's named tools plus the generic escape hatches, so a discovery response
is not per-installation. These tests pin that the session pool narrows a
gateway server's response to the tools the registry says it owns — otherwise
whichever gateway installation is discovered first claims every name and
`get_server_for_tool` resolves a tool to the wrong installation (which mints
the wrong platform-JWT capabilities).
"""

from unittest.mock import AsyncMock, MagicMock, patch

from src.integrations.gateway_actions import PROVIDER_REGISTRY, providers_for_server
from src.integrations.gateway_naming import action_id_to_tool_name
from src.integrations.session_pool import UserMCPSessionPool

WORKSPACE = "ws_gateway_scope"

# The two generic tools the adapter always serves alongside the named ones.
GENERIC_TOOLS = ("execute_action", "list_connections")


def _owned_names(server_name: str) -> set[str]:
    """Tool names the gateway registry assigns to a Muldro installation."""
    return {
        action_id_to_tool_name(action.action_id)
        for provider_id in providers_for_server(server_name)
        for action in PROVIDER_REGISTRY[provider_id].actions
    }


def _all_adapter_tool_names() -> list[str]:
    """Every name the shared adapter endpoint reports, regardless of installation."""
    names = {
        action_id_to_tool_name(action.action_id)
        for provider in PROVIDER_REGISTRY.values()
        for action in provider.actions
    }
    return sorted(names) + list(GENERIC_TOOLS)


def _fake_tool(name: str) -> MagicMock:
    tool = MagicMock()
    tool.name = name
    tool.description = f"desc for {name}"
    tool.inputSchema = {"type": "object", "properties": {}}
    return tool


def _make_pool(*server_names: str) -> UserMCPSessionPool:
    pool = UserMCPSessionPool()
    for server_name in server_names:
        pool.register_server_config(
            server_name,
            {
                "transport": "streamable-http",
                "auth_provider": "none",
                "url": "http://localhost:8100/mcp",
            },
            workspace_id=WORKSPACE,
        )
    return pool


async def _discover(pool: UserMCPSessionPool, server_name: str, tool_names: list[str]) -> None:
    """Run one real discovery pass for `server_name` returning `tool_names`."""
    fake_client = AsyncMock()
    fake_client.list_tools = AsyncMock(return_value=[_fake_tool(n) for n in tool_names])
    fake_ctx = AsyncMock()
    fake_ctx.__aenter__ = AsyncMock(return_value=fake_client)
    fake_ctx.__aexit__ = AsyncMock(return_value=None)

    with (
        patch("src.integrations.session_pool.Client", MagicMock(return_value=fake_ctx)),
        patch.object(pool, "_register_discovered_tools", AsyncMock()) as registered,
    ):
        await pool.get_or_create_session(server_name, user_id="u", workspace_id=WORKSPACE)
    pool._last_registered_tools = [  # type: ignore[attr-defined]
        t.name for t in registered.call_args.args[0]
    ]


async def test_github_discovery_claims_only_its_own_tools():
    """The shared endpoint reports everything; github must claim only github_*."""
    pool = _make_pool("github")
    await _discover(pool, "github", _all_adapter_tool_names())

    claimed = set(pool._server_tools[(WORKSPACE, "github")])
    assert claimed == _owned_names("github")

    # No cross-installation leakage, and no generic escape hatches.
    assert not {n for n in claimed if n.startswith(("gmail_", "googlecalendar_"))}
    assert not claimed & set(GENERIC_TOOLS)

    # _tool_metadata and _register_discovered_tools derive from the same list.
    metadata_for_github = {
        key[2] for key, meta in pool._tool_metadata.items() if meta["server"] == "github"
    }
    assert metadata_for_github == _owned_names("github")
    assert set(pool._last_registered_tools) == _owned_names("github")


async def test_gmail_tool_does_not_resolve_to_github_when_github_discovered_first():
    """Discovery order was what made the live bug deterministic."""
    pool = _make_pool("github", "google-workspace")
    await _discover(pool, "github", _all_adapter_tool_names())

    resolved = pool.get_server_for_tool("gmail_get_profile", workspace_id=WORKSPACE)
    assert resolved != "github"

    await _discover(pool, "google-workspace", _all_adapter_tool_names())
    assert pool.get_server_for_tool("gmail_get_profile", workspace_id=WORKSPACE) == (
        "google-workspace"
    )


async def test_google_workspace_discovery_claims_exactly_its_own_tools():
    pool = _make_pool("google-workspace")
    await _discover(pool, "google-workspace", _all_adapter_tool_names())

    claimed = set(pool._server_tools[(WORKSPACE, "google-workspace")])
    assert claimed == _owned_names("google-workspace")
    assert not {n for n in claimed if n.startswith("github_")}
    assert not claimed & set(GENERIC_TOOLS)


async def test_non_gateway_server_still_claims_everything_it_reports():
    """The filter is scoped to gateway-backed servers only."""
    pool = _make_pool("slack")
    reported = ["slack_send_message", "slack_read_channel", "some_brand_new_tool"]
    await _discover(pool, "slack", reported)

    assert set(pool._server_tools[(WORKSPACE, "slack")]) == set(reported)
    assert set(pool._last_registered_tools) == set(reported)
