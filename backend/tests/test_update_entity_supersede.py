"""The update_entity MCP tool must supersede contradicting attributes via entity_facts,
not silently dict.update() the JSONB (the second overwrite site). Real Postgres."""

import asyncio
import json
from unittest.mock import MagicMock

import pytest
from sqlalchemy import delete, select
from sqlalchemy.ext.asyncio import async_sessionmaker, create_async_engine
from sqlalchemy.pool import NullPool
from ulid import ULID

from src.config.settings import get_settings
from src.models.entities import Entity, EntityFact
from src.models.users import User, Workspace
from src.services.entity_facts.store import EntityFactStore
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


async def test_update_entity_tool_supersedes_via_facts():
    import src.tools.intelligence_server as srv

    engine = create_async_engine(get_settings().database_url, poolclass=NullPool)
    factory = async_sessionmaker(engine, expire_on_commit=False)
    suffix = str(ULID())
    uid = f"usr_{suffix}"
    ws = f"ws_{suffix}"
    eid = f"ent_{suffix}"
    settings = make_mock_settings()
    settings.neo4j_url = ""

    orig_settings = srv._shared._settings
    orig_factory = srv._shared._db_factory
    try:
        # Seed User+Workspace, then Entity + a prior CTO fact (so CEO supersedes it).
        async with factory() as db:
            db.add(User(user_id=uid, email=f"s4-{suffix}@example.com", display_name="s4"))
            db.add(Workspace(workspace_id=ws, name="s4-ws", owner_user_id=uid))
            await db.flush()
            db.add(
                Entity(
                    entity_id=eid,
                    user_id=uid,
                    workspace_id=ws,
                    entity_type="person",
                    canonical_name="Eve",
                    attributes={"role": "CTO"},
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
            await db.commit()

        srv._shared._settings = settings
        srv._shared._db_factory = factory

        result = await srv.update_entity(
            entity_id=eid,
            ctx=MagicMock(),
            user_id=uid,
            attributes=json.dumps({"role": "CEO"}),
            workspace_id=ws,
        )
        assert result["status"] == "updated"

        async with factory() as db:
            store = EntityFactStore(db)
            current = await store.current_fact(eid, "role", ws)
            assert current is not None and current.attr_value == "CEO"
            rows = await db.execute(
                select(EntityFact.attr_value).where(
                    EntityFact.entity_id == eid, EntityFact.attr_key == "role"
                )
            )
            assert {r[0] for r in rows} == {"CTO", "CEO"}  # history retained, not clobbered
            ent = (await db.execute(select(Entity).where(Entity.entity_id == eid))).scalar_one()
            assert ent.attributes["role"] == "CEO"  # snapshot updated (D2)
    finally:
        srv._shared._settings = orig_settings
        srv._shared._db_factory = orig_factory
        try:
            async with factory() as db:
                await db.execute(delete(Workspace).where(Workspace.workspace_id == ws))
                await db.execute(delete(User).where(User.user_id == uid))
                await db.commit()
        except Exception:  # pragma: no cover
            pass
        await engine.dispose()
