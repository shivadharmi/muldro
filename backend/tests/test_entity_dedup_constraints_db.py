"""Real-DB: entity de-duplication is enforced by DB constraints + upsert convergence.
- UNIQUE(workspace_id, entity_type, canonical_name) closes the same-name insert race.
- partial UNIQUE(workspace_id, alias) WHERE alias_type IN ('email','handle') makes a
  strong identifier map to one entity, so a differently-named extraction resolves to it.
"""

import asyncio
from contextlib import asynccontextmanager

import pytest
from sqlalchemy import delete, func, select
from sqlalchemy.exc import IntegrityError
from sqlalchemy.ext.asyncio import async_sessionmaker, create_async_engine
from sqlalchemy.pool import NullPool
from ulid import ULID

from src.config.settings import get_settings
from src.models.entities import Entity
from src.models.users import User, Workspace
from src.services.world_model import WorldModel


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
async def _env():
    engine = create_async_engine(get_settings().database_url, poolclass=NullPool)
    factory = async_sessionmaker(engine, expire_on_commit=False)
    suffix = str(ULID())
    uid, wid = f"usr_{suffix}", f"ws_{suffix}"
    try:
        async with factory() as db:
            db.add(User(user_id=uid, email=f"ddc-{suffix}@example.com", display_name="ddc"))
            db.add(Workspace(workspace_id=wid, name="ddc-ws", owner_user_id=uid))
            await db.commit()
        yield factory, wid, uid
    finally:
        try:
            async with factory() as db:
                await db.execute(delete(Entity).where(Entity.workspace_id == wid))
                await db.execute(delete(Workspace).where(Workspace.workspace_id == wid))
                await db.execute(delete(User).where(User.user_id == uid))
                await db.commit()
        except Exception:  # pragma: no cover
            pass
        await engine.dispose()


async def _count_entities(factory, wid) -> int:
    async with factory() as db:
        return (
            await db.execute(
                select(func.count()).select_from(Entity).where(Entity.workspace_id == wid)
            )
        ).scalar_one()


async def test_upsert_same_name_is_idempotent():
    async with _env() as (factory, wid, uid):
        async with factory() as db:
            wm = WorldModel(settings=get_settings(), db=db)
            id1 = await wm.upsert_entity(uid, "person", "Alice Smith", workspace_id=wid)
            id2 = await wm.upsert_entity(uid, "person", "Alice Smith", workspace_id=wid)
        assert id1 == id2
        assert await _count_entities(factory, wid) == 1


async def test_shared_strong_alias_resolves_to_one_entity():
    async with _env() as (factory, wid, uid):
        async with factory() as db:
            wm = WorldModel(settings=get_settings(), db=db)
            # Same email, two different extracted display names.
            id1 = await wm.upsert_entity(
                uid, "person", "Alice", aliases=["alice@example.com"], workspace_id=wid
            )
            id2 = await wm.upsert_entity(
                uid, "person", "A. Smith", aliases=["alice@example.com"], workspace_id=wid
            )
        assert id1 == id2  # resolved via the shared strong alias
        assert await _count_entities(factory, wid) == 1


async def test_duplicate_name_rejected_by_constraint():
    async with _env() as (factory, wid, uid):
        with pytest.raises(IntegrityError):
            async with factory() as db:
                db.add(
                    Entity(
                        entity_id=f"ent_{ULID()}",
                        user_id=uid,
                        workspace_id=wid,
                        entity_type="person",
                        canonical_name="Bob",
                    )
                )
                db.add(
                    Entity(
                        entity_id=f"ent_{ULID()}",
                        user_id=uid,
                        workspace_id=wid,
                        entity_type="person",
                        canonical_name="Bob",
                    )
                )
                await db.commit()
