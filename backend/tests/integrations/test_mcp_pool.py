"""Tests for `mcp_pool._installation_to_config`'s gateway routing.

Routing to the ToolHive vMCP is driven purely by the installation's own
`auth_provider == "platform_jwt"` declaration -- never by a server-name
allowlist and never by a feature flag. Adding a new gateway-backed provider
is a registry change (seed_installations declares `auth_provider=
"platform_jwt"`), not a routing change here.
"""

from types import SimpleNamespace
from unittest.mock import MagicMock, patch

import pytest

from src.integrations.mcp_pool import GatewayNotConfigured, _installation_to_config


def _make_installation(**overrides) -> SimpleNamespace:
    """Build a minimal stand-in for an IntegrationInstallation ORM row."""
    defaults = dict(
        server_name="github",
        transport="streamable-http",
        auth_provider="platform_jwt",
        command=None,
        args=None,
        env_template=None,
        remote_url=None,
        config=None,
    )
    defaults.update(overrides)
    return SimpleNamespace(**defaults)


def _make_settings(*, toolhive_vmcp_url: str | None):
    return SimpleNamespace(toolhive_vmcp_url=toolhive_vmcp_url)


def test_any_platform_jwt_installation_routes_to_the_vmcp():
    """A GITHUB installation with auth_provider=platform_jwt routes to the
    vMCP -- proving routing is not server-name-gated."""
    inst = _make_installation(server_name="github")
    settings = _make_settings(toolhive_vmcp_url="http://localhost:8100/mcp")

    with patch("src.integrations.mcp_pool.get_settings", return_value=settings):
        config = _installation_to_config(inst)

    assert config == {
        "transport": "streamable-http",
        "auth_provider": "platform_jwt",
        "url": "http://localhost:8100/mcp",
    }


def test_google_workspace_also_routes_to_the_vmcp():
    """Regression guard: google-workspace (the originally special-cased
    server) still routes to the gateway now that routing is declarative
    rather than name-based."""
    inst = _make_installation(server_name="google-workspace")
    settings = _make_settings(toolhive_vmcp_url="http://localhost:8100/mcp")

    with patch("src.integrations.mcp_pool.get_settings", return_value=settings):
        config = _installation_to_config(inst)

    assert config["url"] == "http://localhost:8100/mcp"
    assert config["transport"] == "streamable-http"
    assert config["auth_provider"] == "platform_jwt"


def test_gateway_routing_fails_loudly_when_the_vmcp_url_is_unset():
    """No native fallback for a gateway-declared installation: an unset vMCP
    URL is a misconfiguration that must raise, not silently produce a
    broken (no command, no url) config.

    The type is NARROW (`GatewayNotConfigured`, a RuntimeError subclass) so
    `initialize_from_db` can skip exactly this case without mislabelling an
    unrelated RuntimeError as "vMCP not configured"."""
    inst = _make_installation()
    settings = _make_settings(toolhive_vmcp_url=None)

    with patch("src.integrations.mcp_pool.get_settings", return_value=settings):
        with pytest.raises(GatewayNotConfigured, match="toolhive_vmcp_url"):
            _installation_to_config(inst)
    assert issubclass(GatewayNotConfigured, RuntimeError)


def test_non_platform_jwt_installation_uses_native_config():
    """An installation NOT declared platform_jwt keeps its native config even
    when a vMCP url IS set — the vMCP url is a destination, not a switch. Only
    the installation's own ``auth_provider`` decides routing."""
    inst = _make_installation(
        server_name="google-workspace",
        transport="stdio",
        auth_provider="oauth",
        command="uvx",
        args=["google-workspace-mcp"],
    )
    settings = _make_settings(toolhive_vmcp_url="https://vmcp.example.com")

    with patch("src.integrations.mcp_pool.get_settings", return_value=settings):
        config = _installation_to_config(inst)

    assert config["transport"] == "stdio"
    assert config["command"] == "uvx"
    assert config.get("url") != settings.toolhive_vmcp_url


def test_native_installations_are_unaffected():
    """A non-gateway installation (auth_provider != platform_jwt) keeps its
    native transport config, regardless of the vMCP url setting."""
    inst = _make_installation(
        server_name="slack",
        transport="stdio",
        auth_provider="token",
        command="npx",
        args=["some-slack-mcp"],
    )
    settings = _make_settings(toolhive_vmcp_url=None)

    with patch("src.integrations.mcp_pool.get_settings", return_value=settings):
        config = _installation_to_config(inst)

    assert config["command"] == "npx"
    assert config["transport"] == "stdio"
    assert config["auth_provider"] == "token"
    assert "url" not in config


def test_native_installation_carries_tool_defaults_and_managed_local():
    """Non-regression: tool_defaults/managed_local passthrough from
    inst.config must survive the routing rewrite."""
    inst = _make_installation(
        server_name="atlassian",
        transport="sse",
        auth_provider="oauth",
        remote_url="https://atlassian.example.com/mcp",
        config={"tool_defaults": {"cloudId": "abc123"}, "managed_local": True},
    )
    settings = _make_settings(toolhive_vmcp_url=None)

    with patch("src.integrations.mcp_pool.get_settings", return_value=settings):
        config = _installation_to_config(inst)

    assert config["tool_defaults"] == {"cloudId": "abc123"}
    assert config["managed_local"] is True


async def test_one_misconfigured_gateway_installation_does_not_deregister_the_others():
    """A gateway installation with no vMCP URL must be skipped LOUDLY, not abort the loop.

    ``_installation_to_config`` raising is intentional (there is no native fallback
    for a migrated provider), but ``initialize_from_db`` wraps its whole body in a
    single try/except that logs at DEBUG. Letting the RuntimeError propagate would
    silently drop every remaining installation -- taking unmigrated servers down
    with the misconfigured gateway one. The accepted cost is losing the GATEWAY
    providers, not everything.
    """
    from src.integrations.mcp_pool import WorkspaceMCPPool

    gateway = _make_installation(
        server_name="google-workspace", auth_provider="platform_jwt", transport="streamable-http"
    )
    native = _make_installation(
        server_name="slack",
        transport="stdio",
        auth_provider="token",
        command="npx",
        args=["some-slack-mcp"],
    )
    for inst in (gateway, native):
        inst.workspace_id = "ws_x"

    pool = WorkspaceMCPPool(session_pool=MagicMock())
    added: list[str] = []

    async def _fake_add_server(workspace_id, server_name, config):
        added.append(server_name)

    class _Result:
        def scalars(self):
            return self

        def all(self):
            return [gateway, native]

    class _Db:
        async def execute(self, *a, **kw):
            return _Result()

        async def __aenter__(self):
            return self

        async def __aexit__(self, *a):
            return False

    with (
        patch(
            "src.integrations.mcp_pool.get_settings",
            return_value=_make_settings(toolhive_vmcp_url=None),
        ),
        patch("src.models.database.get_session_factory", return_value=lambda: _Db()),
        patch.object(pool, "add_server", _fake_add_server),
    ):
        count = await pool.initialize_from_db()

    # The native server still registered; only the misconfigured gateway one was skipped.
    assert added == ["slack"], added
    assert count == 1
