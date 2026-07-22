"""Real-DB proof that EntityResolver resolves a mention span to the right entity
via the activated FTS + exact signals (embedding_service/vector_store = None, so
no Voyage/Qdrant needed). Skips when Postgres is unreachable. Mirrors the
test_entity_fts_db env."""

import asyncio
from contextlib import asynccontextmanager

import pytest
from sqlalchemy import delete
from sqlalchemy.ext.asyncio import async_sessionmaker, create_async_engine
from sqlalchemy.pool import NullPool
from ulid import ULID

from src.config.settings import get_settings
from src.models.entities import Entity
from src.models.users import User, Workspace
from src.services.entity_resolver import EntityResolver


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
async def _seeded_entity():
    engine = create_async_engine(get_settings().database_url, poolclass=NullPool)
    factory = async_sessionmaker(engine, expire_on_commit=False)
    suffix = str(ULID())
    user_id = f"usr_{suffix}"
    workspace_id = f"ws_{suffix}"
    try:
        async with factory() as db:
            db.add(User(user_id=user_id, email=f"res-{suffix}@example.com", display_name="res"))
            db.add(Workspace(workspace_id=workspace_id, name="res-ws", owner_user_id=user_id))
            await db.commit()
        async with factory() as db:
            db.add(
                Entity(
                    entity_id=f"ent_{suffix}",
                    user_id=user_id,
                    workspace_id=workspace_id,
                    entity_type="person",
                    canonical_name="Bob Smith",
                )
            )
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


async def test_resolves_mention_span_via_fts():
    async with _seeded_entity() as (factory, workspace_id, user_id):
        async with factory() as db:
            resolver = EntityResolver(db, workspace_id, embedding_service=None, vector_store=None)
            out = await resolver.resolve(user_id, "please email Bob Smith the Q3 deck", limit=10)
    names = [e["canonical_name"] for e in out]
    assert "Bob Smith" in names, f"resolver missed the entity via FTS: {names}"


async def test_resolves_exact_clean_name():
    async with _seeded_entity() as (factory, workspace_id, user_id):
        async with factory() as db:
            resolver = EntityResolver(db, workspace_id, embedding_service=None, vector_store=None)
            out = await resolver.resolve(user_id, "Bob Smith", limit=10)
    assert [e["canonical_name"] for e in out] == ["Bob Smith"]


async def test_other_workspace_cannot_resolve_it():
    async with _seeded_entity() as (factory, workspace_id, user_id):
        async with factory() as db:
            resolver = EntityResolver(db, "ws_other", embedding_service=None, vector_store=None)
            out = await resolver.resolve(user_id, "Bob Smith", limit=10)
    assert out == []  # workspace hydration gate is fail-closed
