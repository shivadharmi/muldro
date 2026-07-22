"""The four world-model read tools are workspace-filtered fail-closed (spec §4.6 item 5).
Real Postgres. Tools open their own session via the configured intelligence server."""

import asyncio
from contextlib import asynccontextmanager
from datetime import datetime, timezone
from unittest.mock import MagicMock

import pytest
from sqlalchemy import delete
from sqlalchemy.ext.asyncio import async_sessionmaker, create_async_engine
from sqlalchemy.pool import NullPool
from ulid import ULID

from src.config.settings import get_settings
from src.models.entities import Entity, EntityRelationship
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
async def _env():
    import src.tools.intelligence_server as srv

    engine = create_async_engine(get_settings().database_url, poolclass=NullPool)
    factory = async_sessionmaker(engine, expire_on_commit=False)
    suffix = str(ULID())
    uid = f"usr_{suffix}"
    ws = f"ws_{suffix}"
    other = f"wso_{suffix}"
    eid = f"ent_{suffix}"
    eid2 = f"entb_{suffix}"
    orig_factory = srv._shared._db_factory
    try:
        async with factory() as db:
            db.add(User(user_id=uid, email=f"s4-{suffix}@example.com", display_name="s4"))
            db.add(Workspace(workspace_id=ws, name="s4-ws", owner_user_id=uid))
            db.add(Workspace(workspace_id=other, name="s4-other", owner_user_id=uid))
            await db.flush()
            db.add(
                Entity(
                    entity_id=eid,
                    user_id=uid,
                    workspace_id=ws,
                    entity_type="person",
                    canonical_name="Grace",
                    attributes={"role": "CTO"},
                )
            )
            db.add(
                Entity(
                    entity_id=eid2,
                    user_id=uid,
                    workspace_id=ws,
                    entity_type="organization",
                    canonical_name="Acme",
                )
            )
            await db.flush()
            await EntityFactStore(db).record_fact(
                entity_id=eid,
                workspace_id=ws,
                user_id=uid,
                attr_key="role",
                attr_value="CTO",
                origin="user_message",
            )
            db.add(
                EntityRelationship(
                    relation_id=f"rel_{suffix}",
                    user_id=uid,
                    workspace_id=ws,
                    from_entity_id=eid,
                    relation_type="works_on",
                    to_entity_id=eid2,
                )
            )
            await db.commit()
        srv._shared._db_factory = factory
        yield ws, other, uid, eid, eid2
    finally:
        srv._shared._db_factory = orig_factory
        try:
            async with factory() as db:
                await db.execute(delete(Workspace).where(Workspace.workspace_id.in_([ws, other])))
                await db.execute(delete(User).where(User.user_id == uid))
                await db.commit()
        except Exception:  # pragma: no cover
            pass
        await engine.dispose()


async def test_get_entity_returns_entity_and_current_facts():
    from src.tools.intelligence_server.world_model_tools import get_entity

    async with _env() as (ws, other, uid, eid, eid2):
        res = await get_entity(user_id=uid, entity_id=eid, ctx=MagicMock(), workspace_id=ws)
        assert res["entity"]["canonical_name"] == "Grace"
        assert any(f["attr_key"] == "role" and f["attr_value"] == "CTO" for f in res["facts"])
        assert "confidence" in res["facts"][0]


async def test_get_entity_is_workspace_fail_closed():
    from src.tools.intelligence_server.world_model_tools import get_entity

    async with _env() as (ws, other, uid, eid, eid2):
        res = await get_entity(user_id=uid, entity_id=eid, ctx=MagicMock(), workspace_id=other)
        assert res.get("entity") is None


async def test_query_facts_as_of_returns_current_belief_and_is_fail_closed():
    from src.tools.intelligence_server.world_model_tools import query_facts

    async with _env() as (ws, other, uid, eid, eid2):
        res = await query_facts(
            user_id=uid,
            entity_id=eid,
            as_of=datetime.now(timezone.utc).isoformat(),
            ctx=MagicMock(),
            workspace_id=ws,
        )
        assert any(f["attr_key"] == "role" for f in res["facts"])
        res_other = await query_facts(
            user_id=uid, entity_id=eid, as_of="", ctx=MagicMock(), workspace_id=other
        )
        assert res_other["facts"] == []


async def test_get_provenance_returns_origin():
    from src.tools.intelligence_server.world_model_tools import get_provenance

    async with _env() as (ws, other, uid, eid, eid2):
        res = await get_provenance(user_id=uid, entity_id=eid, ctx=MagicMock(), workspace_id=ws)
        assert res["provenance"]
        assert res["provenance"][0]["provenance"]["origin"] == "user_message"


async def test_get_provenance_is_workspace_fail_closed():
    from src.tools.intelligence_server.world_model_tools import get_provenance

    async with _env() as (ws, other, uid, eid, eid2):
        res = await get_provenance(user_id=uid, entity_id=eid, ctx=MagicMock(), workspace_id=other)
        assert res["provenance"] == []


async def test_traverse_returns_edges_and_is_workspace_scoped():
    from src.tools.intelligence_server.world_model_tools import traverse

    async with _env() as (ws, other, uid, eid, eid2):
        res = await traverse(user_id=uid, entity_id=eid, ctx=MagicMock(), workspace_id=ws)
        assert any(r["to_entity_id"] == eid2 for r in res["relationships"])
        res_other = await traverse(user_id=uid, entity_id=eid, ctx=MagicMock(), workspace_id=other)
        assert res_other["relationships"] == []
