"""Real-DB regression: entity uniqueness is per-user, not per-workspace (Codex PR #9, F1/P1).

Two users in the SAME workspace must each be able to own an entity with the same
(entity_type, canonical_name). The original ``uq_entities_ws_type_name`` constraint was
workspace-scoped (no user_id), so the second user's ``upsert_entity`` insert tripped the unique
constraint and dead-ended — the user-scoped retry lookup could not resolve the OTHER user's row,
so it raised. The constraint is now (user_id, workspace_id, entity_type, canonical_name).
"""

import asyncio
from contextlib import asynccontextmanager

import pytest
from sqlalchemy import delete, select
from sqlalchemy.ext.asyncio import async_sessionmaker, create_async_engine
from sqlalchemy.pool import NullPool
from ulid import ULID

from src.config.settings import get_settings
from src.models.entities import Entity
from src.models.users import User, Workspace
from src.services.world_model import WorldModel
from tests.conftest import make_mock_settings


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


@asynccontextmanager
async def _two_user_env():
    """One workspace, two users (both could be members). Cleanup cascades via workspace."""
    engine = create_async_engine(get_settings().database_url, poolclass=NullPool)
    factory = async_sessionmaker(engine, expire_on_commit=False)
    suffix = str(ULID())
    ws = f"ws_{suffix}"
    ua, ub = f"usr_a_{suffix}", f"usr_b_{suffix}"
    try:
        async with factory() as db:
            db.add(User(user_id=ua, email=f"a-{suffix}@example.com", display_name="a"))
            db.add(User(user_id=ub, email=f"b-{suffix}@example.com", display_name="b"))
            db.add(Workspace(workspace_id=ws, name="shared", owner_user_id=ua))
            await db.commit()
        yield factory, ws, ua, ub
    finally:
        try:
            async with factory() as db:
                await db.execute(delete(Entity).where(Entity.workspace_id == ws))
                await db.execute(delete(Workspace).where(Workspace.workspace_id == ws))
                await db.execute(delete(User).where(User.user_id.in_([ua, ub])))
                await db.commit()
        except Exception:  # pragma: no cover - teardown best-effort
            pass
        await engine.dispose()


async def test_two_users_in_one_workspace_can_own_same_entity_name():
    async with _two_user_env() as (factory, ws, ua, ub):
        async with factory() as db:
            wm = WorldModel(settings=make_mock_settings(), db=db)
            eid_a = await wm.upsert_entity(ua, "project", "Website Redesign", workspace_id=ws)
            # Before the fix this second insert tripped uq_entities_ws_type_name and raised.
            eid_b = await wm.upsert_entity(ub, "project", "Website Redesign", workspace_id=ws)

        assert eid_a != eid_b
        async with factory() as db:
            rows = (
                (await db.execute(select(Entity).where(Entity.workspace_id == ws))).scalars().all()
            )
        assert len(rows) == 2
        assert {r.user_id for r in rows} == {ua, ub}
