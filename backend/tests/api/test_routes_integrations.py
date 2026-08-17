"""Unified integrations response exposes the gateway registry, not a restatement.

The unified-integrations API surfaces the registry's per-server provider list
(`oc_providers`), per-provider connectivity (`provider_connections`), and
per-provider display labels (`oc_provider_labels`) — sourced from
`providers_for_server` / `provider_labels_for_server` so they cannot drift
from `src/integrations/gateway_actions`. There is no singular `oc_provider`
back-compat alias: a gateway installation fans out to N providers, so any
API shape must be plural.

Gateway installations also derive their brand `slug` from `server_name`, not
`auth_provider` — every gateway installation declares the same
`auth_provider="platform_jwt"`, so deriving the slug from it would collapse
google-workspace and github into the same "platform" slug.

Runs against a real Postgres (same `_db_reachable` skip-guard idiom as
`tests/api/test_routes_integrations_disconnect.py`), since `get_integration_statuses`
reads live connectivity from `connection_map`.
"""

import asyncio
from unittest.mock import AsyncMock, MagicMock, patch

import pytest

from src.api.routes_integrations import disconnect_installation, list_unified_integrations
from src.config.settings import get_settings
from src.integrations.gateway_actions import provider_labels_for_server, providers_for_server
from src.models.connection_map import ConnectionMap
from tests.conftest import make_test_db, seed_user_workspace

_WS = "ws_unified_integrations"
_USER = "usr_01JTESTUNIFIEDINTEGRATIONS0"


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


def _make_inst(server_name: str, display_name: str, auth_provider: str | None) -> MagicMock:
    inst = MagicMock()
    inst.server_name = server_name
    inst.display_name = display_name
    inst.auth_provider = auth_provider
    inst.health_status = "healthy"
    inst.enabled = True
    inst.install_id = f"inst_{server_name}"
    inst.scopes_granted = []
    inst.created_at = None
    return inst


async def _clear_connections(factory) -> None:
    async with factory() as db:
        await db.execute(ConnectionMap.__table__.delete().where(ConnectionMap.workspace_id == _WS))
        await db.commit()


async def _unified(factory, installations):
    settings = MagicMock()
    settings.oauth_encryption_key = ""
    settings.google_oauth_client_id = "cid"
    settings.github_oauth_client_id = "cid"
    settings.slack_oauth_client_id = "cid"

    cp = MagicMock()
    cp.list_installations = AsyncMock(return_value=installations)

    with (
        patch("src.config.settings.get_settings", return_value=settings),
        patch("src.integrations.control_plane.IntegrationControlPlane", return_value=cp),
        patch("src.models.database.get_session_factory", return_value=factory),
    ):
        async with factory() as db:
            return await list_unified_integrations(user_id=_USER, workspace_id=_WS, db=db)


async def _disconnect(factory, inst):
    settings = MagicMock()
    settings.oauth_encryption_key = ""

    cp = MagicMock()
    cp.get_installation = AsyncMock(return_value=inst)

    with (
        patch("src.config.settings.get_settings", return_value=settings),
        patch("src.integrations.control_plane.IntegrationControlPlane", return_value=cp),
        patch("src.integrations.mcp_pool.get_workspace_pool", return_value=None),
        patch("src.models.database.get_session_factory", return_value=factory),
    ):
        async with factory() as db:
            return await disconnect_installation(
                install_id=inst.install_id, user_id=_USER, workspace_id=_WS, db=db
            )


async def test_unified_integrations_expose_plural_oc_provider_fields():
    factory, engine = make_test_db()
    try:
        await seed_user_workspace(factory, _USER, _WS)
        await _clear_connections(factory)

        installations = [
            _make_inst("google-workspace", "Google Workspace", "platform_jwt"),
            _make_inst("github", "GitHub", "platform_jwt"),
            _make_inst("slack", "Slack", "slack"),
        ]

        results = await _unified(factory, installations)
    finally:
        await _clear_connections(factory)
        await engine.dispose()

    by_server = {r.server_name: r for r in results}

    gws = by_server["google-workspace"]
    assert gws.oc_providers == list(providers_for_server("google-workspace"))
    assert gws.oc_providers == ["gmail", "googlecalendar"]
    assert gws.provider_connections == {"gmail": False, "googlecalendar": False}

    gh = by_server["github"]
    assert gh.oc_providers == list(providers_for_server("github"))
    assert gh.oc_providers == ["github"]
    assert gh.provider_connections == {"github": False}

    # slack is not gateway-backed: registry has nothing for it.
    slack = by_server["slack"]
    assert slack.oc_providers == list(providers_for_server("slack"))
    assert slack.oc_providers == []
    assert slack.provider_connections == {}

    for r in results:
        assert "oc_provider" not in r.model_dump()


async def test_unified_integrations_expose_registry_sourced_provider_labels():
    factory, engine = make_test_db()
    try:
        await seed_user_workspace(factory, _USER, _WS)
        await _clear_connections(factory)

        installations = [
            _make_inst("google-workspace", "Google Workspace", "platform_jwt"),
            _make_inst("github", "GitHub", "platform_jwt"),
            _make_inst("slack", "Slack", "slack"),
        ]

        results = await _unified(factory, installations)
    finally:
        await _clear_connections(factory)
        await engine.dispose()

    by_server = {r.server_name: r for r in results}

    gws = by_server["google-workspace"]
    assert gws.oc_provider_labels == provider_labels_for_server("google-workspace")
    assert gws.oc_provider_labels == {"gmail": "Gmail", "googlecalendar": "Google Calendar"}

    gh = by_server["github"]
    assert gh.oc_provider_labels == provider_labels_for_server("github")
    assert gh.oc_provider_labels == {"github": "GitHub"}

    # slack is not gateway-backed: registry has no labels for it.
    slack = by_server["slack"]
    assert slack.oc_provider_labels == {}


async def test_disconnect_gives_distinct_slugs_to_both_gateway_installations():
    """Both migrated installations share auth_provider="platform_jwt" — the
    slug must come from server_name, or they collapse into the same brand key.
    """
    factory, engine = make_test_db()
    try:
        await seed_user_workspace(factory, _USER, _WS)
        await _clear_connections(factory)

        gws_resp = await _disconnect(
            factory, _make_inst("google-workspace", "Google Workspace", "platform_jwt")
        )
        gh_resp = await _disconnect(factory, _make_inst("github", "GitHub", "platform_jwt"))

        assert gws_resp.slug != gh_resp.slug
        assert gws_resp.slug == "google"
        assert gh_resp.slug == "github"
        assert "oc_provider" not in gws_resp.model_dump()
        assert "oc_provider" not in gh_resp.model_dump()
    finally:
        await _clear_connections(factory)
        await engine.dispose()


async def test_disconnect_carries_provider_list_with_all_false_connectivity():
    """A disconnected gateway installation's response must still carry its
    provider list (all-False connectivity) rather than empty [] / {} — the
    empty shape is indistinguishable from a native-OAuth integration, which
    sends a client that trusts this response (e.g. an optimistic-update cache
    write) down the native branch and into the gateway's HTTP 400.
    """
    factory, engine = make_test_db()
    try:
        await seed_user_workspace(factory, _USER, _WS)
        await _clear_connections(factory)

        gws_resp = await _disconnect(
            factory, _make_inst("google-workspace", "Google Workspace", "platform_jwt")
        )
    finally:
        await _clear_connections(factory)
        await engine.dispose()

    assert gws_resp.oc_providers == list(providers_for_server("google-workspace"))
    assert gws_resp.oc_providers == ["gmail", "googlecalendar"]
    assert gws_resp.provider_connections == {"gmail": False, "googlecalendar": False}
    assert gws_resp.oc_provider_labels == provider_labels_for_server("google-workspace")
