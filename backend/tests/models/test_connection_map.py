"""Real-DB test for the ConnectionMap model.

Verifies a ConnectionMap row round-trips through Postgres: insert for the
(TEST_WORKSPACE_ID, TEST_USER_ID) principal, commit, select back by
(principal_id, provider_id, account_alias) — the same tuple the unique
constraint ``uq_connection_map_principal_alias`` is keyed on (tenant_id is
also part of that constraint; here tenant_id == workspace_id).
"""

import asyncio

import pytest
from sqlalchemy import select

from src.config.settings import get_settings
from src.models.connection_map import ConnectionMap
from tests.conftest import TEST_USER_ID, TEST_WORKSPACE_ID, make_test_db, seed_user_workspace


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


async def test_connection_map_round_trips_through_postgres():
    factory, engine = make_test_db()
    connection_id = f"{TEST_WORKSPACE_ID}:{TEST_USER_ID}:gmail:work"
    try:
        await seed_user_workspace(factory, TEST_USER_ID, TEST_WORKSPACE_ID)

        async with factory() as db:
            db.add(
                ConnectionMap(
                    tenant_id=TEST_WORKSPACE_ID,
                    workspace_id=TEST_WORKSPACE_ID,
                    principal_id=TEST_USER_ID,
                    provider_id="gmail",
                    provider_account_id="work@example.com",
                    connection_id=connection_id,
                    credential_reference="secretref_abc123",
                    granted_scopes=["gmail.readonly", "gmail.send"],
                    account_alias="work",
                )
            )
            await db.commit()

        async with factory() as db:
            row = (
                await db.execute(
                    select(ConnectionMap).where(
                        ConnectionMap.principal_id == TEST_USER_ID,
                        ConnectionMap.provider_id == "gmail",
                        ConnectionMap.account_alias == "work",
                    )
                )
            ).scalar_one()

        assert row.connection_id.endswith(":gmail:work")
        assert row.connection_status == "active"
    finally:
        try:
            async with factory() as db:
                await db.execute(
                    ConnectionMap.__table__.delete().where(
                        ConnectionMap.principal_id == TEST_USER_ID,
                        ConnectionMap.provider_id == "gmail",
                        ConnectionMap.account_alias == "work",
                    )
                )
                await db.commit()
        except Exception:  # pragma: no cover - teardown best-effort
            pass
        await engine.dispose()
