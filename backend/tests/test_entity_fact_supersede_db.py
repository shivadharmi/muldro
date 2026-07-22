"""upsert_entity supersedes contradicting attributes via entity_facts instead of the
silent {**old, **new} clobber, sets the first-ever evidence-derived confidence_score,
and both entity->dict builders carry confidence + provenance. Real Postgres."""

import asyncio
from contextlib import asynccontextmanager

import pytest
from sqlalchemy import delete, select
from sqlalchemy.ext.asyncio import async_sessionmaker, create_async_engine
from sqlalchemy.pool import NullPool
from ulid import ULID

from src.config.settings import get_settings
from src.models.entities import Entity, EntityFact
from src.models.users import User, Workspace
from src.services.entity_facts.store import EntityFactStore
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
async def _env():
    engine = create_async_engine(get_settings().database_url, poolclass=NullPool)
    factory = async_sessionmaker(engine, expire_on_commit=False)
    suffix = str(ULID())
    user_id = f"usr_{suffix}"
    workspace_id = f"ws_{suffix}"
    try:
        async with factory() as db:
            db.add(User(user_id=user_id, email=f"s4-{suffix}@example.com", display_name="s4"))
            db.add(Workspace(workspace_id=workspace_id, name="s4-ws", owner_user_id=user_id))
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


def _wm(db):
    return WorldModel(settings=make_mock_settings(), db=db)


async def test_contradicting_attribute_is_superseded_not_clobbered():
    async with _env() as (factory, ws, uid):
        async with factory() as db:
            eid = await _wm(db).upsert_entity(
                user_id=uid,
                entity_type="person",
                canonical_name="Bob",
                attributes={"role": "CTO"},
                workspace_id=ws,
                origin="user_message",
            )
        async with factory() as db:
            await _wm(db).upsert_entity(
                user_id=uid,
                entity_type="person",
                canonical_name="Bob",
                attributes={"role": "CEO"},
                workspace_id=ws,
                origin="perception",
            )
        async with factory() as db:
            store = EntityFactStore(db)
            current = await store.current_fact(eid, "role", ws)
            assert current.attr_value == "CEO"
            rows = await db.execute(
                select(EntityFact.attr_value).where(
                    EntityFact.entity_id == eid, EntityFact.attr_key == "role"
                )
            )
            assert {r[0] for r in rows} == {"CTO", "CEO"}  # history retained, not clobbered
            ent = (await db.execute(select(Entity).where(Entity.entity_id == eid))).scalar_one()
            assert ent.attributes["role"] == "CEO"  # snapshot updated (D2)


async def test_confidence_score_is_evidence_derived_not_constant_one():
    async with _env() as (factory, ws, uid):
        async with factory() as db:
            eid = await _wm(db).upsert_entity(
                user_id=uid,
                entity_type="person",
                canonical_name="Carol",
                attributes={"role": "eng"},
                workspace_id=ws,
                origin="perception",
            )
        async with factory() as db:
            ent = (await db.execute(select(Entity).where(Entity.entity_id == eid))).scalar_one()
            assert ent.confidence_score != 1.0
            assert 0.0 < ent.confidence_score <= 0.75  # perception n=1 fresh = 0.7


async def test_both_dict_builders_carry_confidence_and_provenance():
    async with _env() as (factory, ws, uid):
        async with factory() as db:
            await _wm(db).upsert_entity(
                user_id=uid,
                entity_type="person",
                canonical_name="Dave",
                attributes={"role": "CTO"},
                workspace_id=ws,
                origin="user_message",
            )
        async with factory() as db:
            wm = _wm(db)
            found = await wm.find_entity(uid, "Dave", workspace_id=ws)
            assert found and "confidence" in found[0] and "provenance" in found[0]
            resolved = await wm.resolve_entities(uid, "Dave", workspace_id=ws)
            assert resolved and "confidence" in resolved[0] and "provenance" in resolved[0]
