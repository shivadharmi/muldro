"""Connectivity for installations that authenticate with a process env var.

`get_integration_statuses` initialises `configured`/`connected` to True and then
narrows them in two branches: gateway-backed (connection_map) and
`category == "oauth"` (OAuthManager). An installation whose `auth_provider` is
the literal `"token"` reaches neither, so the optimistic default survives and
the card reports a connection nothing ever checked.

That default is indistinguishable, from the UI, from a check that passed. These
tests pin the two halves apart:

* a `token` installation is connected only when a credential its `env_template`
  declares is actually present in the process environment; and
* a `local` installation (``auth_provider is None``) needs no credential at all,
  so it stays connected — the narrowing must not sweep it up.
"""

import asyncio
import os
from unittest.mock import AsyncMock, MagicMock, patch

import pytest

from src.config.settings import get_settings
from src.services.integration_status import get_integration_statuses
from tests.conftest import make_test_db, seed_user_workspace

_WS = "ws_intstatus_token"
_USER = "usr_01JTESTINTSTATUSTOKEN000000"

# Names chosen so a real deployment cannot accidentally satisfy them.
_ENV_A = "MULDRO_TEST_TOKEN_CATEGORY_A"
_ENV_B = "MULDRO_TEST_TOKEN_CATEGORY_B"


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


def _make_inst(server_name: str, auth_provider: str | None, env_template: dict | None) -> MagicMock:
    inst = MagicMock()
    inst.server_name = server_name
    inst.display_name = server_name.title()
    inst.auth_provider = auth_provider
    inst.env_template = env_template
    inst.health_status = "healthy"
    inst.enabled = True
    inst.install_id = f"inst_{server_name}"
    inst.scopes_granted = []
    return inst


async def _statuses(factory, installations):
    settings = MagicMock()
    settings.oauth_encryption_key = "key"

    cp = MagicMock()
    cp.list_installations = AsyncMock(return_value=list(installations))

    oauth_mgr = MagicMock()

    with (
        patch("src.config.settings.get_settings", return_value=settings),
        patch("src.integrations.control_plane.IntegrationControlPlane", return_value=cp),
        patch("src.models.database.get_session_factory", return_value=factory),
        patch("src.services.oauth_manager.OAuthManager", return_value=oauth_mgr),
    ):
        async with factory() as db:
            return await get_integration_statuses(db, _USER, _WS)


async def test_token_installation_with_no_env_credential_is_not_connected():
    """The defect: nothing populates the env var, yet the card claims connected."""
    factory, engine = make_test_db()
    try:
        await seed_user_workspace(factory, _USER, _WS)
        for name in (_ENV_A, _ENV_B):
            os.environ.pop(name, None)

        inst = _make_inst(
            "slack",
            "token",
            {_ENV_A: "A user OAuth token", _ENV_B: "A bot OAuth token"},
        )
        statuses = await _statuses(factory, [inst])
        s = statuses[0]

        assert s.category == "token"
        assert s.connected is False
        assert s.configured is False
    finally:
        await engine.dispose()


async def test_token_installation_with_one_declared_env_var_set_is_connected():
    """The declared vars are alternatives, not a conjunction — either suffices."""
    factory, engine = make_test_db()
    try:
        await seed_user_workspace(factory, _USER, _WS)
        os.environ.pop(_ENV_B, None)
        os.environ[_ENV_A] = "xoxp-not-a-real-token"

        inst = _make_inst(
            "slack",
            "token",
            {_ENV_A: "A user OAuth token", _ENV_B: "A bot OAuth token"},
        )
        statuses = await _statuses(factory, [inst])
        s = statuses[0]

        assert s.category == "token"
        assert s.connected is True
        assert s.configured is True
    finally:
        os.environ.pop(_ENV_A, None)
        await engine.dispose()


async def test_local_installation_needs_no_credential_and_stays_connected():
    """Guard against over-narrowing: a no-auth server has nothing to check."""
    factory, engine = make_test_db()
    try:
        await seed_user_workspace(factory, _USER, _WS)

        inst = _make_inst("intelligence-server", None, {})
        statuses = await _statuses(factory, [inst])
        s = statuses[0]

        assert s.category == "local"
        assert s.connected is True
        assert s.configured is True
    finally:
        await engine.dispose()
