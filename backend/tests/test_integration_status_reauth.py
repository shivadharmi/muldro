"""Tests for the needs_reauth field on IntegrationStatus."""

from unittest.mock import AsyncMock, MagicMock, patch

import pytest

from src.services.integration_status import IntegrationStatus, get_integration_statuses
from src.services.oauth_manager import TokenResult


def _make_inst(server_name="github", auth_provider="github", **kw):
    inst = MagicMock()
    inst.server_name = server_name
    inst.display_name = kw.get("display_name", server_name.title())
    inst.auth_provider = auth_provider
    inst.health_status = kw.get("health_status", "unknown")
    inst.enabled = kw.get("enabled", True)
    inst.install_id = kw.get("install_id", "inst_1")
    inst.scopes_granted = kw.get("scopes_granted", [])
    return inst


def test_dataclass_has_needs_reauth_default_false():
    s = IntegrationStatus(
        server_name="github",
        display_name="GitHub",
        provider="github",
        category="oauth",
        configured=True,
        connected=True,
        health_status="healthy",
        enabled=True,
        install_id="inst_1",
        scopes=[],
    )
    assert s.needs_reauth is False


@pytest.mark.asyncio
async def _run_statuses(token_result, *, configured=True):
    inst = _make_inst()
    cp = MagicMock()
    cp.list_installations = AsyncMock(return_value=[inst])

    settings = MagicMock()
    settings.oauth_encryption_key = "key" if configured else ""
    settings.github_oauth_client_id = "cid" if configured else ""

    oauth_mgr = MagicMock()
    oauth_mgr.get_valid_token_with_reason = AsyncMock(return_value=token_result)

    with (
        patch("src.config.settings.get_settings", return_value=settings),
        patch(
            "src.integrations.control_plane.IntegrationControlPlane",
            return_value=cp,
        ),
        patch("src.models.database.get_session_factory", return_value=MagicMock()),
        patch("src.services.oauth_manager.OAuthManager", return_value=oauth_mgr),
    ):
        return await get_integration_statuses(MagicMock(), "u1", "ws_1")


@pytest.mark.asyncio
async def test_revoked_token_sets_needs_reauth_true():
    statuses = await _run_statuses(TokenResult(token=None, reason="revoked"))
    s = statuses[0]
    assert s.connected is False
    assert s.needs_reauth is True


@pytest.mark.asyncio
async def test_ok_token_needs_reauth_false():
    statuses = await _run_statuses(TokenResult(token="tok", reason="ok"))
    s = statuses[0]
    assert s.connected is True
    assert s.needs_reauth is False


@pytest.mark.asyncio
async def test_refresh_failed_is_not_needs_reauth():
    # Transient refresh blip — not connected, but NOT a permanent reauth state.
    statuses = await _run_statuses(TokenResult(token=None, reason="refresh_failed"))
    s = statuses[0]
    assert s.connected is False
    assert s.needs_reauth is False


@pytest.mark.asyncio
async def test_unconfigured_provider_not_needs_reauth():
    statuses = await _run_statuses(TokenResult(token=None, reason="no_token"), configured=False)
    s = statuses[0]
    assert s.configured is False
    assert s.needs_reauth is False
