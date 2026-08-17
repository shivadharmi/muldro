"""Native OAuth + transport retirement for the gateway-migrated providers.

``google-workspace`` and ``github`` are served entirely by the OpenConnector
gateway (see ``src.integrations.gateway_actions``): credentials live inside
OpenConnector and the MCP traffic goes through the ToolHive vMCP. The native
OAuth registrations, the native connect routes and the ``gmail_via_gateway``
feature flag that used to select between the two paths are therefore dead —
and, worse, misleading (a stale third "is this gateway-backed?" signal already
dead-ended the connect UI in an HTTP 400).

These tests pin the retirement: the machinery must stay gone, and unmigrated
providers must stay untouched.
"""

import pytest
from fastapi import HTTPException

from tests.conftest import make_mock_settings


def test_migrated_providers_have_no_native_oauth_registration():
    from src.integrations.auth_providers import SUPPORTED_PROVIDERS

    assert "google" not in SUPPORTED_PROVIDERS
    assert "github" not in SUPPORTED_PROVIDERS
    assert "slack" in SUPPORTED_PROVIDERS  # unmigrated providers untouched


def test_gateway_flag_is_gone():
    from src.config.settings import Settings

    assert not hasattr(Settings(), "gmail_via_gateway")


def test_gateway_providers_module_is_deleted():
    with pytest.raises(ModuleNotFoundError):
        import src.integrations.gateway_providers  # noqa: F401


def test_provider_map_has_no_server_entry_for_migrated_providers():
    from src.integrations.provider_map import _PROVIDER_SERVERS

    assert "google" not in _PROVIDER_SERVERS
    assert "github" not in _PROVIDER_SERVERS
    assert _PROVIDER_SERVERS["slack"] == ["slack"]


@pytest.mark.parametrize("provider", ["google", "github"])
async def test_oauth_authorize_rejects_migrated_providers(provider):
    """The native connect route must 400 rather than mint credentials nothing reads."""
    from src.api.routes_auth_oauth import oauth_authorize

    with pytest.raises(HTTPException) as exc:
        await oauth_authorize(provider, scopes="", user_id="usr_1", settings=make_mock_settings())

    assert exc.value.status_code == 400
    assert exc.value.detail == f"Unknown provider: {provider}"


@pytest.mark.parametrize("provider", ["google", "github"])
async def test_oauth_callback_rejects_migrated_providers(provider):
    from fastapi import BackgroundTasks

    from src.api.routes_auth_oauth import oauth_callback

    with pytest.raises(HTTPException) as exc:
        await oauth_callback(
            provider,
            BackgroundTasks(),
            code="abc",
            state="usr_1",
            error="",
            settings=make_mock_settings(),
        )

    assert exc.value.status_code == 400
    assert exc.value.detail == f"Unknown provider: {provider}"
