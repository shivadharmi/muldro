import asyncio
from unittest.mock import AsyncMock

import pytest
from sqlalchemy import select
from ulid import ULID

from src.config.settings import get_settings
from src.models.connection_map import ConnectionMap
from src.services.connection_service import ConnectionService, mint_connection_name
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


async def _cleanup(factory, principal_id, alias):
    async with factory() as db:
        await db.execute(
            ConnectionMap.__table__.delete().where(
                ConnectionMap.tenant_id == TEST_WORKSPACE_ID,
                ConnectionMap.principal_id == principal_id,
                ConnectionMap.account_alias == alias,
            )
        )
        await db.commit()


async def test_begin_connection_upserts_pending_and_returns_url():
    factory, engine = make_test_db()
    pid = f"usr_{ULID()}"
    alias = "work"
    try:
        await seed_user_workspace(factory, pid, TEST_WORKSPACE_ID)
        admin = AsyncMock()
        admin.start_authorization = AsyncMock(
            return_value={"service": "gmail", "authorizationUrl": "https://consent", "state": "s1"}
        )
        svc = ConnectionService(admin_client=admin)
        async with factory() as db:
            url = await svc.begin_connection(
                db, workspace_id=TEST_WORKSPACE_ID, principal_id=pid, provider="gmail", alias=alias
            )
            await db.commit()

        assert url == "https://consent"
        expected_name = mint_connection_name(TEST_WORKSPACE_ID, pid, "gmail", alias)
        admin.start_authorization.assert_awaited_once_with(
            service="gmail", connection_name=expected_name
        )
        async with factory() as db:
            row = (
                await db.execute(
                    select(ConnectionMap).where(
                        ConnectionMap.principal_id == pid, ConnectionMap.account_alias == alias
                    )
                )
            ).scalar_one()
        assert row.connection_status == "pending"
        assert row.connection_id == expected_name
    finally:
        await _cleanup(factory, pid, alias)
        await engine.dispose()


async def test_confirm_connection_flips_pending_to_active_when_configured():
    factory, engine = make_test_db()
    pid = f"usr_{ULID()}"
    alias = "work"
    try:
        await seed_user_workspace(factory, pid, TEST_WORKSPACE_ID)
        name = mint_connection_name(TEST_WORKSPACE_ID, pid, "gmail", alias)
        async with factory() as db:
            db.add(
                ConnectionMap(
                    tenant_id=TEST_WORKSPACE_ID,
                    workspace_id=TEST_WORKSPACE_ID,
                    principal_id=pid,
                    provider_id="gmail",
                    connection_id=name,
                    connection_status="pending",
                    account_alias=alias,
                )
            )
            await db.commit()

        admin = AsyncMock()
        admin.list_connections = AsyncMock(
            return_value=[{"connectionName": name, "configured": True}]
        )
        svc = ConnectionService(admin_client=admin)
        async with factory() as db:
            active = await svc.confirm_connection(
                db, workspace_id=TEST_WORKSPACE_ID, principal_id=pid, provider="gmail", alias=alias
            )
            await db.commit()

        assert active is True
        async with factory() as db:
            row = (
                await db.execute(select(ConnectionMap).where(ConnectionMap.principal_id == pid))
            ).scalar_one()
        assert row.connection_status == "active"
    finally:
        await _cleanup(factory, pid, alias)
        await engine.dispose()


async def test_confirm_connection_stays_pending_when_not_configured():
    factory, engine = make_test_db()
    pid = f"usr_{ULID()}"
    alias = "work"
    try:
        await seed_user_workspace(factory, pid, TEST_WORKSPACE_ID)
        name = mint_connection_name(TEST_WORKSPACE_ID, pid, "gmail", alias)
        async with factory() as db:
            db.add(
                ConnectionMap(
                    tenant_id=TEST_WORKSPACE_ID,
                    workspace_id=TEST_WORKSPACE_ID,
                    principal_id=pid,
                    provider_id="gmail",
                    connection_id=name,
                    connection_status="pending",
                    account_alias=alias,
                )
            )
            await db.commit()

        admin = AsyncMock()
        admin.list_connections = AsyncMock(return_value=[])  # not yet consented
        svc = ConnectionService(admin_client=admin)
        async with factory() as db:
            active = await svc.confirm_connection(
                db, workspace_id=TEST_WORKSPACE_ID, principal_id=pid, provider="gmail", alias=alias
            )
            await db.commit()

        assert active is False
        async with factory() as db:
            row = (
                await db.execute(select(ConnectionMap).where(ConnectionMap.principal_id == pid))
            ).scalar_one()
        assert row.connection_status == "pending"
    finally:
        await _cleanup(factory, pid, alias)
        await engine.dispose()


async def test_begin_connection_does_not_demote_active_connection():
    """A stray re-begin on an already-active connection must not demote it."""
    factory, engine = make_test_db()
    pid = f"usr_{ULID()}"
    alias = "work"
    try:
        await seed_user_workspace(factory, pid, TEST_WORKSPACE_ID)
        name = mint_connection_name(TEST_WORKSPACE_ID, pid, "gmail", alias)
        async with factory() as db:
            db.add(
                ConnectionMap(
                    tenant_id=TEST_WORKSPACE_ID,
                    workspace_id=TEST_WORKSPACE_ID,
                    principal_id=pid,
                    provider_id="gmail",
                    connection_id=name,
                    connection_status="active",
                    account_alias=alias,
                )
            )
            await db.commit()

        admin = AsyncMock()
        admin.start_authorization = AsyncMock(
            return_value={"service": "gmail", "authorizationUrl": "https://consent", "state": "s"}
        )
        svc = ConnectionService(admin_client=admin)
        async with factory() as db:
            url = await svc.begin_connection(
                db, workspace_id=TEST_WORKSPACE_ID, principal_id=pid, provider="gmail", alias=alias
            )
            await db.commit()

        assert url == "https://consent"  # re-auth URL still issued
        async with factory() as db:
            row = (
                await db.execute(select(ConnectionMap).where(ConnectionMap.principal_id == pid))
            ).scalar_one()
        assert row.connection_status == "active"  # NOT demoted to pending
    finally:
        await _cleanup(factory, pid, alias)
        await engine.dispose()
