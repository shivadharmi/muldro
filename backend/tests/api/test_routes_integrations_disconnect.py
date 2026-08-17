"""Disconnect must actually revoke a gateway-backed installation.

`_clear_connection_artifacts` resolves what to revoke through an auth_provider
map that only knows the native OAuth providers. Gateway installations declare
`auth_provider="platform_jwt"`, which misses that map — so before this test
existed, Disconnect cleared sessions, the UI optimistically showed
disconnected, and the next `integration_status` refetch read the still-`active`
`connection_map` rows and reported connected again. A lost revocation control,
not a cosmetic bug.

Runs against a real Postgres (same `_db_reachable` skip-guard idiom as
`tests/test_integration_status_gateway.py`) because the whole point is the
round trip: revoke the rows, then re-read status from them.
"""

import asyncio
from unittest.mock import AsyncMock, MagicMock, patch

import pytest

from src.api.routes_integrations import _clear_connection_artifacts
from src.config.settings import get_settings
from src.models.connection_map import DEFAULT_ACCOUNT_ALIAS, ConnectionMap
from src.services.integration_status import get_integration_statuses
from src.services.oauth_manager import TokenResult
from tests.conftest import make_test_db, seed_user_workspace

_WS = "ws_disconnect_gateway"
_USER = "usr_01JTESTDISCONNECTGATEWAY000"


def _db_reachable() -> bool:
    import asyncpg

    dsn = get_settings().database_url.replace("+asyncpg", "", 1)

    async def _probe() -> None:
        conn = await asyncpg.connect(dsn=dsn)
        try:
            await conn.execute("SELECT 1")
        finally:
            await conn.close()

    try:
        asyncio.run(_probe())
        return True
    except Exception:  # pragma: no cover
        return False


pytestmark = pytest.mark.skipif(not _db_reachable(), reason="Postgres not reachable")


def _make_inst(server_name: str, auth_provider: str | None) -> MagicMock:
    inst = MagicMock()
    inst.server_name = server_name
    inst.display_name = server_name.title()
    inst.auth_provider = auth_provider
    inst.health_status = "healthy"
    inst.enabled = True
    inst.install_id = f"inst_{server_name}"
    inst.scopes_granted = []
    return inst


async def _clear_connections(factory) -> None:
    async with factory() as db:
        await db.execute(ConnectionMap.__table__.delete().where(ConnectionMap.workspace_id == _WS))
        await db.commit()


async def _add_connection(factory, provider_id: str) -> None:
    async with factory() as db:
        db.add(
            ConnectionMap(
                tenant_id=_WS,
                workspace_id=_WS,
                principal_id=_USER,
                provider_id=provider_id,
                connection_id=f"{_WS}:{_USER}:{provider_id}:{DEFAULT_ACCOUNT_ALIAS}",
                connection_status="active",
                account_alias=DEFAULT_ACCOUNT_ALIAS,
            )
        )
        await db.commit()


async def _statuses_for(factory, inst, *, oauth_key: str = ""):
    settings = MagicMock()
    settings.oauth_encryption_key = oauth_key
    settings.google_oauth_client_id = "cid"
    settings.github_oauth_client_id = "cid"
    settings.slack_oauth_client_id = "cid"

    cp = MagicMock()
    cp.list_installations = AsyncMock(return_value=[inst])
    oauth_mgr = MagicMock()
    oauth_mgr.get_valid_token_with_reason = AsyncMock(
        return_value=TokenResult(token="tok", reason="ok")
    )

    with (
        patch("src.config.settings.get_settings", return_value=settings),
        patch("src.integrations.control_plane.IntegrationControlPlane", return_value=cp),
        patch("src.models.database.get_session_factory", return_value=factory),
        patch("src.services.oauth_manager.OAuthManager", return_value=oauth_mgr),
    ):
        async with factory() as db:
            return await get_integration_statuses(db, _USER, _WS)


async def _disconnect(factory, inst, oauth_mgr) -> None:
    settings = MagicMock()
    settings.oauth_encryption_key = "key"
    with (
        patch("src.config.settings.get_settings", return_value=settings),
        patch("src.integrations.mcp_pool.get_workspace_pool", return_value=None),
        patch("src.models.database.get_session_factory", return_value=factory),
        patch("src.services.oauth_manager.OAuthManager", return_value=oauth_mgr),
    ):
        async with factory() as db:
            await _clear_connection_artifacts(db, inst, _USER, _WS)
            await db.commit()


async def _statuses_rows(factory) -> dict[str, str]:
    from sqlalchemy import select

    async with factory() as db:
        rows = (
            await db.execute(
                select(ConnectionMap.provider_id, ConnectionMap.connection_status).where(
                    ConnectionMap.workspace_id == _WS
                )
            )
        ).all()
    return {provider: status for provider, status in rows}


async def test_disconnecting_a_gateway_installation_revokes_its_connections():
    factory, engine = make_test_db()
    try:
        await seed_user_workspace(factory, _USER, _WS)
        await _clear_connections(factory)
        await _add_connection(factory, "gmail")
        await _add_connection(factory, "googlecalendar")

        inst = _make_inst("google-workspace", "platform_jwt")
        before = await _statuses_for(factory, inst)
        assert before[0].connected is True

        await _disconnect(factory, inst, AsyncMock())

        assert await _statuses_rows(factory) == {
            "gmail": "revoked",
            "googlecalendar": "revoked",
        }
        after = await _statuses_for(factory, inst)
        assert after[0].connected is False
        assert after[0].provider_connections == {"gmail": False, "googlecalendar": False}
    finally:
        await _clear_connections(factory)
        await engine.dispose()


async def test_disconnecting_one_gateway_installation_leaves_the_other_alone():
    """Revocation is scoped to the disconnected installation's own providers."""
    factory, engine = make_test_db()
    try:
        await seed_user_workspace(factory, _USER, _WS)
        await _clear_connections(factory)
        await _add_connection(factory, "gmail")
        await _add_connection(factory, "github")

        await _disconnect(factory, _make_inst("github", "platform_jwt"), AsyncMock())

        assert await _statuses_rows(factory) == {"gmail": "active", "github": "revoked"}
    finally:
        await _clear_connections(factory)
        await engine.dispose()


async def test_disconnecting_a_native_installation_is_unchanged():
    """Native path still deletes the OAuth token and touches no connection rows."""
    factory, engine = make_test_db()
    try:
        await seed_user_workspace(factory, _USER, _WS)
        await _clear_connections(factory)
        await _add_connection(factory, "gmail")

        oauth_mgr = AsyncMock()
        await _disconnect(factory, _make_inst("slack", "slack"), oauth_mgr)

        oauth_mgr.delete_token.assert_awaited_once_with(_USER, "slack")
        assert await _statuses_rows(factory) == {"gmail": "active"}
    finally:
        await _clear_connections(factory)
        await engine.dispose()
