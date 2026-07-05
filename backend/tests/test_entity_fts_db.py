"""Real-DB proof that the trigger populates entities.search_vector and that
FTSService.search_table('entities', ...) — previously always empty — now matches.
Skips (does not fail) when Postgres is unreachable. Mirrors tests/idempotency/
test_ledger_db.py: own engine per test (NullPool), FK-parent seeding, CASCADE
cleanup, in-loop dispose."""

import asyncio
from contextlib import asynccontextmanager

import pytest
from sqlalchemy import delete, text
from sqlalchemy.ext.asyncio import async_sessionmaker, create_async_engine
from sqlalchemy.pool import NullPool
from ulid import ULID

from src.config.settings import get_settings
from src.models.entities import Entity
from src.models.users import User, Workspace
from src.services.fts_service import FTSService


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
    except Exception:  # pragma: no cover - environment-dependent
        return False


pytestmark = pytest.mark.skipif(not _db_reachable(), reason="Postgres not reachable")


@asynccontextmanager
async def _entity_env():
    """Yields (sessionmaker, workspace_id, user_id) with FK parents seeded. On
    exit: delete Workspace (CASCADE removes entities) + User, dispose engine."""
    engine = create_async_engine(get_settings().database_url, poolclass=NullPool)
    factory = async_sessionmaker(engine, expire_on_commit=False)
    suffix = str(ULID())
    user_id = f"usr_{suffix}"
    workspace_id = f"ws_{suffix}"
    try:
        async with factory() as db:
            db.add(User(user_id=user_id, email=f"fts-{suffix}@example.com", display_name="fts"))
            db.add(Workspace(workspace_id=workspace_id, name="fts-ws", owner_user_id=user_id))
            await db.commit()
        yield factory, workspace_id, user_id
    finally:
        try:
            async with factory() as db:
                await db.execute(delete(Workspace).where(Workspace.workspace_id == workspace_id))
                await db.execute(delete(User).where(User.user_id == user_id))
                await db.commit()
        except Exception:  # pragma: no cover
            pass
        await engine.dispose()


async def test_trigger_populates_search_vector_on_insert():
    async with _entity_env() as (factory, workspace_id, user_id):
        async with factory() as db:
            db.add(
                Entity(
                    entity_id=f"ent_{ULID()}",
                    user_id=user_id,
                    workspace_id=workspace_id,
                    entity_type="person",
                    canonical_name="Bob Smith",
                )
            )
            await db.commit()
            row = await db.execute(
                text(
                    "SELECT search_vector::text FROM entities "
                    "WHERE workspace_id = :ws AND canonical_name = 'Bob Smith'"
                ),
                {"ws": workspace_id},
            )
            sv = row.scalar_one()
            assert sv and "bob" in sv and "smith" in sv  # trigger populated it


async def test_fts_service_matches_entity_after_activation():
    async with _entity_env() as (factory, workspace_id, user_id):
        async with factory() as db:
            db.add(
                Entity(
                    entity_id=f"ent_{ULID()}",
                    user_id=user_id,
                    workspace_id=workspace_id,
                    entity_type="person",
                    canonical_name="Bob Smith",
                )
            )
            await db.commit()
            hits = await FTSService(db, workspace_id).search_table("entities", "Bob Smith", limit=5)
            assert any(h["title"] == "Bob Smith" for h in hits), f"FTS returned nothing: {hits}"
