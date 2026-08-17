"""Gateway-backed connectivity for IntegrationStatus.

A gateway-backed installation's credential lives inside OpenConnector, so its
`connected` flag must come from the `connection_map` table, never from
`OAuthManager` (which holds no token for it and would report it permanently
disconnected).

The connection_map side runs against a real Postgres (same `_db_reachable`
skip-guard idiom as `tests/services/test_connection_service.py`) so the
provider/workspace/`connection_status` filtering is exercised for real; only the
installation catalog and settings are faked, mirroring
`tests/test_integration_status_reauth.py`.
"""

import asyncio
from unittest.mock import AsyncMock, MagicMock, patch

import pytest

from src.config.settings import get_settings
from src.models.connection_map import DEFAULT_ACCOUNT_ALIAS, ConnectionMap
from src.services.integration_status import get_integration_statuses
from src.services.oauth_manager import TokenResult
from tests.conftest import make_test_db, seed_user_workspace

# Dedicated workspace so rows seeded by other real-DB tests (which use
# TEST_WORKSPACE_ID) cannot leak into these assertions.
_WS = "ws_intstatus_gateway"
_USER = "usr_01JTESTINTSTATUSGATEWAY0000"


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


async def _add_connection(
    factory,
    provider_id: str,
    status: str = "active",
    *,
    principal_id: str = _USER,
    alias: str = DEFAULT_ACCOUNT_ALIAS,
) -> None:
    """Insert a connection_map row. Defaults mirror what the resolver resolves.

    ``principal_id``/``alias`` are overridable so the divergence cases (another
    member's row, a non-default alias) can be seeded explicitly.
    """
    async with factory() as db:
        db.add(
            ConnectionMap(
                tenant_id=_WS,
                workspace_id=_WS,
                principal_id=principal_id,
                provider_id=provider_id,
                connection_id=f"{_WS}:{principal_id}:{provider_id}:{alias}",
                connection_status=status,
                account_alias=alias,
            )
        )
        await db.commit()


async def _statuses(factory, installations, *, oauth_key: str = "key", token_reason: str = "ok"):
    """Run get_integration_statuses against a real session with a faked catalog."""
    settings = MagicMock()
    settings.oauth_encryption_key = oauth_key
    settings.google_oauth_client_id = "cid"
    settings.github_oauth_client_id = "cid"
    settings.slack_oauth_client_id = "cid"

    cp = MagicMock()
    cp.list_installations = AsyncMock(return_value=list(installations))

    oauth_mgr = MagicMock()
    token = "tok" if token_reason == "ok" else None
    oauth_mgr.get_valid_token_with_reason = AsyncMock(
        return_value=TokenResult(token=token, reason=token_reason)
    )

    with (
        patch("src.config.settings.get_settings", return_value=settings),
        patch("src.integrations.control_plane.IntegrationControlPlane", return_value=cp),
        patch("src.models.database.get_session_factory", return_value=factory),
        patch("src.services.oauth_manager.OAuthManager", return_value=oauth_mgr),
    ):
        async with factory() as db:
            return await get_integration_statuses(db, _USER, _WS)


async def test_gateway_all_providers_active_is_connected():
    factory, engine = make_test_db()
    try:
        await seed_user_workspace(factory, _USER, _WS)
        await _clear_connections(factory)
        await _add_connection(factory, "gmail")
        await _add_connection(factory, "googlecalendar")

        statuses = await _statuses(factory, [_make_inst("google-workspace", "google")])
        s = statuses[0]
        assert s.oc_providers == ["gmail", "googlecalendar"]
        assert s.provider_connections == {"gmail": True, "googlecalendar": True}
        assert s.connected is True
        assert s.needs_reauth is False
    finally:
        await _clear_connections(factory)
        await engine.dispose()


async def test_gateway_partial_connection_keeps_working_provider_visible():
    factory, engine = make_test_db()
    try:
        await seed_user_workspace(factory, _USER, _WS)
        await _clear_connections(factory)
        await _add_connection(factory, "gmail")  # calendar never linked

        statuses = await _statuses(factory, [_make_inst("google-workspace", "google")])
        s = statuses[0]
        # The point: the linked connector stays VISIBLE instead of the whole
        # installation collapsing into a single "disconnected".
        assert s.provider_connections == {"gmail": True, "googlecalendar": False}
        assert s.connected is False
        assert s.oc_providers == ["gmail", "googlecalendar"]
    finally:
        await _clear_connections(factory)
        await engine.dispose()


async def test_gateway_non_active_status_is_not_connected():
    factory, engine = make_test_db()
    try:
        await seed_user_workspace(factory, _USER, _WS)
        await _clear_connections(factory)
        await _add_connection(factory, "gmail", status="pending")
        await _add_connection(factory, "googlecalendar")

        statuses = await _statuses(factory, [_make_inst("google-workspace", "google")])
        s = statuses[0]
        assert s.provider_connections == {"gmail": False, "googlecalendar": True}
        assert s.connected is False
    finally:
        await _clear_connections(factory)
        await engine.dispose()


async def test_gateway_github_connected_without_any_oauth_manager():
    factory, engine = make_test_db()
    try:
        await seed_user_workspace(factory, _USER, _WS)
        await _clear_connections(factory)
        await _add_connection(factory, "github")

        # No encryption key -> get_integration_statuses builds no OAuthManager at
        # all, so this asserts behaviourally that the gateway path never needs a
        # token.
        statuses = await _statuses(factory, [_make_inst("github", "github")], oauth_key="")
        s = statuses[0]
        assert s.oc_providers == ["github"]
        assert s.provider_connections == {"github": True}
        assert s.connected is True
        assert s.needs_reauth is False
    finally:
        await _clear_connections(factory)
        await engine.dispose()


async def test_non_gateway_installation_still_uses_oauth_token_path():
    factory, engine = make_test_db()
    try:
        await seed_user_workspace(factory, _USER, _WS)
        await _clear_connections(factory)

        ok = await _statuses(factory, [_make_inst("slack", "slack")])
        assert ok[0].connected is True
        assert ok[0].oc_providers == []
        assert ok[0].provider_connections == {}

        revoked = await _statuses(factory, [_make_inst("slack", "slack")], token_reason="revoked")
        assert revoked[0].connected is False
        assert revoked[0].needs_reauth is True
        assert revoked[0].oc_providers == []
    finally:
        await engine.dispose()


async def test_another_members_connection_does_not_make_this_user_connected():
    """A workspace-only query reported Bob connected on Alice's row.

    ``resolve_connection`` keys on (tenant, principal, provider, alias), so a
    row owned by another member of the same workspace resolves for its owner
    only. "Connected" must mean "the resolver will resolve this FOR ME".
    """
    other = "usr_01JTESTINTSTATUSOTHERMEMBER"
    factory, engine = make_test_db()
    try:
        await seed_user_workspace(factory, _USER, _WS)
        await _clear_connections(factory)
        await _add_connection(factory, "github", principal_id=other)

        statuses = await _statuses(factory, [_make_inst("github", "platform_jwt")])
        s = statuses[0]
        assert s.provider_connections == {"github": False}
        assert s.connected is False
    finally:
        await _clear_connections(factory)
        await engine.dispose()


async def test_non_default_alias_row_does_not_count_as_connected():
    """An active row under a non-default alias is not what the resolver reads.

    The adapter defaults an absent ``account_alias`` to ``DEFAULT_ACCOUNT_ALIAS``
    and every gateway call arrives without one, so a connection stored under
    e.g. "work" is denied at call time — it must not report connected. The alias
    is a client-supplied body field on the connect route, so this is reachable
    through the public API, not a hypothetical.
    """
    factory, engine = make_test_db()
    try:
        await seed_user_workspace(factory, _USER, _WS)
        await _clear_connections(factory)
        await _add_connection(factory, "github", alias="work")

        statuses = await _statuses(factory, [_make_inst("github", "platform_jwt")])
        assert statuses[0].connected is False

        # ... and the same row under the default alias DOES count, proving the
        # alias is what made the difference.
        await _add_connection(factory, "github")
        statuses = await _statuses(factory, [_make_inst("github", "platform_jwt")])
        assert statuses[0].connected is True
    finally:
        await _clear_connections(factory)
        await engine.dispose()


async def test_gateway_installations_get_distinct_slugs_and_registry_scopes():
    """platform_jwt must not collapse both brands into one slug.

    Both migrated installations declare auth_provider="platform_jwt", so a slug
    derived from the auth provider yields "platform" for BOTH. Scopes likewise
    come from the registry, not from the (now None) hand-maintained
    ``scopes_granted``, so the UI badges are not empty.
    """
    factory, engine = make_test_db()
    try:
        await seed_user_workspace(factory, _USER, _WS)
        await _clear_connections(factory)

        statuses = await _statuses(
            factory,
            [
                _make_inst("google-workspace", "platform_jwt"),
                _make_inst("github", "platform_jwt"),
            ],
            oauth_key="",
        )
        by_server = {s.server_name: s for s in statuses}
        assert by_server["google-workspace"].slug == "google"
        assert by_server["github"].slug == "github"
        assert len({s.slug for s in statuses}) == 2

        gw = by_server["google-workspace"]
        assert "email.send" in gw.scopes
        assert set(gw.access_scopes) == {"read", "write"}
        assert by_server["github"].scopes
    finally:
        await _clear_connections(factory)
        await engine.dispose()
