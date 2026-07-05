"""Bi-temporal fact store (spec §4.6 item 3): supersede-on-change, corroborate-on-same,
insert-on-new, plus current/as-of/provenance reads. Real Postgres (migration applied)."""

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
from src.services.entity_facts.store import EntityFactStore


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
async def _entity_env(entity_id: str):
    """Own engine per test; seed User + Workspace + one Entity (FK parents for
    entity_facts). Cleanup: delete Workspace (CASCADE removes entity + facts) + User."""
    engine = create_async_engine(get_settings().database_url, poolclass=NullPool)
    factory = async_sessionmaker(engine, expire_on_commit=False)
    suffix = str(ULID())
    user_id = f"usr_{suffix}"
    workspace_id = f"ws_{suffix}"
    try:
        async with factory() as db:
            db.add(User(user_id=user_id, email=f"s4-{suffix}@example.com", display_name="s4"))
            db.add(Workspace(workspace_id=workspace_id, name="s4-ws", owner_user_id=user_id))
            await db.flush()  # parents must exist before the Entity FK insert
            db.add(
                Entity(
                    entity_id=entity_id,
                    user_id=user_id,
                    workspace_id=workspace_id,
                    entity_type="person",
                    canonical_name="Bob",
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


async def test_insert_then_corroborate_then_supersede():
    async with _entity_env("ent_facts_1") as (factory, ws, uid):
        async with factory() as db:
            store = EntityFactStore(db)
            fid1, superseded = await store.record_fact(
                entity_id="ent_facts_1",
                workspace_id=ws,
                user_id=uid,
                attr_key="role",
                attr_value="CTO",
                origin="user_message",
            )
            assert superseded is False
            current = await store.current_fact("ent_facts_1", "role", ws)
            assert current.attr_value == "CTO"
            assert current.corroboration_count == 1
            assert current.valid_to is None
            assert abs(current.confidence - 0.95) < 1e-9

            fid1b, superseded = await store.record_fact(
                entity_id="ent_facts_1",
                workspace_id=ws,
                user_id=uid,
                attr_key="role",
                attr_value="CTO",
                origin="user_message",
            )
            assert superseded is False
            assert fid1b == fid1
            current = await store.current_fact("ent_facts_1", "role", ws)
            assert current.corroboration_count == 2
            assert current.confidence > 0.95

            fid2, superseded = await store.record_fact(
                entity_id="ent_facts_1",
                workspace_id=ws,
                user_id=uid,
                attr_key="role",
                attr_value="CEO",
                origin="perception",
            )
            assert superseded is True
            assert fid2 != fid1
            current = await store.current_fact("ent_facts_1", "role", ws)
            assert current.attr_value == "CEO"
            assert current.fact_id == fid2
            old = await store.get_fact(fid1)
            assert old.valid_to is not None
            assert old.superseded_by == fid2


async def test_facts_as_of_returns_the_belief_valid_at_a_past_time():
    async with _entity_env("ent_facts_2") as (factory, ws, uid):
        async with factory() as db:
            store = EntityFactStore(db)
            t0 = datetime.now(timezone.utc) - timedelta(days=2)
            await store.record_fact(
                entity_id="ent_facts_2",
                workspace_id=ws,
                user_id=uid,
                attr_key="city",
                attr_value="NYC",
                origin="user_message",
                now=t0,
            )
            t1 = datetime.now(timezone.utc)
            await store.record_fact(
                entity_id="ent_facts_2",
                workspace_id=ws,
                user_id=uid,
                attr_key="city",
                attr_value="SF",
                origin="user_message",
                now=t1,
            )

            as_of = t0 + timedelta(days=1)
            facts = await store.facts_as_of("ent_facts_2", ws, as_of)
            assert {f.attr_key: f.attr_value for f in facts}["city"] == "NYC"

            facts_now = await store.facts_as_of("ent_facts_2", ws, datetime.now(timezone.utc))
            assert {f.attr_key: f.attr_value for f in facts_now}["city"] == "SF"


async def test_workspace_isolation_is_fail_closed():
    async with _entity_env("ent_facts_3") as (factory, ws, uid):
        async with factory() as db:
            store = EntityFactStore(db)
            await store.record_fact(
                entity_id="ent_facts_3",
                workspace_id=ws,
                user_id=uid,
                attr_key="role",
                attr_value="CTO",
                origin="user_message",
            )
            assert await store.current_fact("ent_facts_3", "role", "ws_other") is None
            assert await store.current_facts("ent_facts_3", "ws_other") == []


async def test_corroborate_and_weaken_adjust_the_stored_base():
    async with _entity_env("ent_facts_4") as (factory, ws, uid):
        async with factory() as db:
            store = EntityFactStore(db)
            fid, _ = await store.record_fact(
                entity_id="ent_facts_4",
                workspace_id=ws,
                user_id=uid,
                attr_key="role",
                attr_value="CTO",
                origin="perception",
            )
            before = (await store.get_fact(fid)).confidence
            await store.corroborate(fid)
            raised = (await store.get_fact(fid)).confidence
            assert raised > before
            await store.weaken(fid)
            lowered = (await store.get_fact(fid)).confidence
            assert lowered < raised
