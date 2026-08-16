"""Real-DB tests for the connection resolver — the alias-ownership +
namespacing core of the adapter's tenant boundary.

Verifies: (1) an owned active alias resolves to its namespaced
connection_id, (2) a different principal_id requesting the same alias is
denied — the isolation property that keeps one tenant's connections from
leaking to another principal, and (3) a connection in a non-active status
(e.g. ``needs_reconnect``) is denied even when owned. Skips when Postgres is
unreachable, mirroring tests/models/test_connection_map.py.
"""

import asyncio

import pytest
from ulid import ULID

from src.adapter.connection_resolver import ConnectionDenied, resolve_connection
from src.adapter.identity import AdapterPrincipal
from src.config.settings import get_settings
from src.models.connection_map import ConnectionMap
from tests.conftest import TEST_WORKSPACE_ID, make_test_db, seed_user_workspace


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


async def _seed_connection(factory, *, principal_id: str, alias: str, status: str = "active"):
    connection_id = f"{TEST_WORKSPACE_ID}:{principal_id}:gmail:{alias}"
    async with factory() as db:
        db.add(
            ConnectionMap(
                tenant_id=TEST_WORKSPACE_ID,
                workspace_id=TEST_WORKSPACE_ID,
                principal_id=principal_id,
                provider_id="gmail",
                connection_id=connection_id,
                connection_status=status,
                account_alias=alias,
            )
        )
        await db.commit()
    return connection_id


async def _delete_connection(factory, *, principal_id: str, alias: str):
    async with factory() as db:
        await db.execute(
            ConnectionMap.__table__.delete().where(
                ConnectionMap.tenant_id == TEST_WORKSPACE_ID,
                ConnectionMap.principal_id == principal_id,
                ConnectionMap.provider_id == "gmail",
                ConnectionMap.account_alias == alias,
            )
        )
        await db.commit()


async def test_resolve_connection_returns_namespaced_id_for_owned_active_alias():
    factory, engine = make_test_db()
    suffix = str(ULID())
    principal_id = f"usr_owner_{suffix}"
    alias = f"work_{suffix}"
    try:
        await seed_user_workspace(factory, principal_id, TEST_WORKSPACE_ID)
        connection_id = await _seed_connection(factory, principal_id=principal_id, alias=alias)

        principal = AdapterPrincipal(
            principal_id=principal_id,
            tenant_id=TEST_WORKSPACE_ID,
            workspace_id=TEST_WORKSPACE_ID,
            capabilities=("email.search",),
        )
        async with factory() as db:
            resolved = await resolve_connection(
                db, principal, provider_id="gmail", account_alias=alias
            )

        assert resolved == connection_id
        assert resolved == f"{TEST_WORKSPACE_ID}:{principal_id}:gmail:{alias}"
    finally:
        await _delete_connection(factory, principal_id=principal_id, alias=alias)
        await engine.dispose()


async def test_resolve_connection_denies_different_principal_for_same_alias():
    factory, engine = make_test_db()
    suffix = str(ULID())
    owner_id = f"usr_owner_{suffix}"
    other_id = f"usr_other_{suffix}"
    alias = f"work_{suffix}"
    try:
        await seed_user_workspace(factory, owner_id, TEST_WORKSPACE_ID)
        await _seed_connection(factory, principal_id=owner_id, alias=alias)

        intruder = AdapterPrincipal(
            principal_id=other_id,
            tenant_id=TEST_WORKSPACE_ID,
            workspace_id=TEST_WORKSPACE_ID,
            capabilities=("email.search",),
        )
        async with factory() as db:
            with pytest.raises(ConnectionDenied):
                await resolve_connection(db, intruder, provider_id="gmail", account_alias=alias)
    finally:
        await _delete_connection(factory, principal_id=owner_id, alias=alias)
        await engine.dispose()


async def test_resolve_connection_denies_needs_reconnect_status():
    factory, engine = make_test_db()
    suffix = str(ULID())
    principal_id = f"usr_owner_{suffix}"
    alias = f"broken_{suffix}"
    try:
        await seed_user_workspace(factory, principal_id, TEST_WORKSPACE_ID)
        await _seed_connection(
            factory, principal_id=principal_id, alias=alias, status="needs_reconnect"
        )

        principal = AdapterPrincipal(
            principal_id=principal_id,
            tenant_id=TEST_WORKSPACE_ID,
            workspace_id=TEST_WORKSPACE_ID,
            capabilities=("email.search",),
        )
        async with factory() as db:
            with pytest.raises(ConnectionDenied):
                await resolve_connection(db, principal, provider_id="gmail", account_alias=alias)
    finally:
        await _delete_connection(factory, principal_id=principal_id, alias=alias)
        await engine.dispose()
