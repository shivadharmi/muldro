"""Real-DB regression: entity dedup is best-effort, so duplicate entities with the
same canonical_name/alias can exist in a workspace. _find_by_name_or_alias must
tolerate >1 match (pick the oldest) instead of crashing with MultipleResultsFound."""

import asyncio
from contextlib import asynccontextmanager
from datetime import datetime, timedelta, timezone

import pytest
from sqlalchemy import delete
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
            db.add(User(user_id=uid, email=f"dup-{suffix}@example.com", display_name="dup"))
            db.add(Workspace(workspace_id=wid, name="dup-ws", owner_user_id=uid))
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


async def test_find_by_name_returns_oldest_of_duplicates_without_crashing():
    async with _env() as (factory, wid, uid):
        older_id = f"ent_{ULID()}"
        newer_id = f"ent_{ULID()}"
        t0 = datetime.now(timezone.utc) - timedelta(hours=1)
        async with factory() as db:
            db.add(
                Entity(
                    entity_id=older_id,
                    user_id=uid,
                    workspace_id=wid,
                    entity_type="person",
                    canonical_name="Duplicate Person",
                    created_at=t0,
                )
            )
            db.add(
                Entity(
                    entity_id=newer_id,
                    user_id=uid,
                    workspace_id=wid,
                    entity_type="person",
                    canonical_name="Duplicate Person",
                    created_at=t0 + timedelta(minutes=30),
                )
            )
            await db.commit()

        async with factory() as db:
            wm = WorldModel(settings=get_settings(), db=db)
            # Previously raised sqlalchemy.exc.MultipleResultsFound.
            found = await wm._find_by_name_or_alias(uid, "Duplicate Person", None, workspace_id=wid)
            assert found is not None
            assert found.entity_id == older_id  # deterministic: oldest wins
