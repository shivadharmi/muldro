"""Tests for the declaration-based gateway routing in `_installation_to_config`.

Routing is driven purely by the installation's own `auth_provider ==
"platform_jwt"` declaration -- not by a `gmail_via_gateway` flag and not by
a `server_name` allowlist (see `tests/integrations/test_mcp_pool.py` for the
full routing-decision test suite, including the multi-provider and
fail-loudly cases). This file keeps the google-workspace-specific
regression coverage: a non-declared (`auth_provider="oauth"`) installation
must keep falling through to its native config, with the flag having no
effect either way.
"""

from types import SimpleNamespace
from unittest.mock import patch

from src.integrations.mcp_pool import _installation_to_config


def _make_installation(**overrides) -> SimpleNamespace:
    """Build a minimal stand-in for an IntegrationInstallation ORM row."""
    defaults = dict(
        server_name="google-workspace",
        transport="stdio",
        command="uvx",
        args=["google-workspace-mcp"],
        env_template=None,
        remote_url=None,
        auth_provider="oauth",
        config=None,
    )
    defaults.update(overrides)
    return SimpleNamespace(**defaults)


def _make_settings(*, gmail_via_gateway: bool, toolhive_vmcp_url: str | None):
    return SimpleNamespace(
        gmail_via_gateway=gmail_via_gateway,
        toolhive_vmcp_url=toolhive_vmcp_url,
    )


def test_non_platform_jwt_installation_uses_native_config_regardless_of_flag():
    """A google-workspace installation NOT declared platform_jwt (e.g. still
    on auth_provider="oauth") keeps its native config even with the (now
    inert) gmail_via_gateway flag ON and a vMCP url set."""
    inst = _make_installation()
    settings = _make_settings(gmail_via_gateway=True, toolhive_vmcp_url="https://vmcp.example.com")

    with patch("src.integrations.mcp_pool.get_settings", return_value=settings):
        config = _installation_to_config(inst)

    assert config["transport"] == "stdio"
    assert config["command"] == "uvx"
    assert "toolhive.example" not in str(config.get("url", ""))
    assert config.get("url") != settings.toolhive_vmcp_url


def test_platform_jwt_declared_google_workspace_routes_to_toolhive():
    """A google-workspace installation declared auth_provider="platform_jwt"
    (the post-migration seed shape) routes to the gateway -- the flag plays
    no role in the decision."""
    inst = _make_installation(auth_provider="platform_jwt", command=None, args=None)
    settings = _make_settings(
        gmail_via_gateway=False, toolhive_vmcp_url="https://vmcp.example.com/gmail"
    )

    with patch("src.integrations.mcp_pool.get_settings", return_value=settings):
        config = _installation_to_config(inst)

    assert config["url"] == "https://vmcp.example.com/gmail"
    assert config["transport"] == "streamable-http"
