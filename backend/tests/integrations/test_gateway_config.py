"""Tests for the Gmail gateway routing branch in `_installation_to_config`.

When `settings.gmail_via_gateway` is on (and `settings.toolhive_vmcp_url` is
set), the google-workspace installation's outbound MCP config should point
at the ToolHive vMCP instead of the native local google-workspace-mcp path.
Any other installation, or the flag being off, must fall through to the
existing native config logic untouched.
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


def test_gateway_flag_off_uses_native_config():
    """Flag OFF: the google-workspace installation keeps its native config."""
    inst = _make_installation()
    settings = _make_settings(gmail_via_gateway=False, toolhive_vmcp_url="https://vmcp.example.com")

    with patch("src.integrations.mcp_pool.get_settings", return_value=settings):
        config = _installation_to_config(inst)

    assert config["transport"] == "stdio"
    assert config["command"] == "uvx"
    assert "toolhive.example" not in str(config.get("url", ""))
    assert config.get("url") != settings.toolhive_vmcp_url


def test_gateway_flag_on_routes_google_workspace_to_toolhive():
    """Flag ON + toolhive_vmcp_url set: google-workspace routes at the gateway."""
    inst = _make_installation()
    settings = _make_settings(
        gmail_via_gateway=True, toolhive_vmcp_url="https://vmcp.example.com/gmail"
    )

    with patch("src.integrations.mcp_pool.get_settings", return_value=settings):
        config = _installation_to_config(inst)

    assert config["url"] == "https://vmcp.example.com/gmail"
    assert config["transport"] == "streamable-http"
