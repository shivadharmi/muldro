import asyncio
import datetime
from contextlib import asynccontextmanager
from unittest.mock import AsyncMock, patch

import pytest

from src.services.provenance import SourceRef


def test_record_attribute_facts_passes_source_ref():
    from src.services.world_model import WorldModel
    from tests.conftest import make_mock_settings

    wm = WorldModel(settings=make_mock_settings(), db=AsyncMock())
    with patch(
        "src.services.entity_facts.store.EntityFactStore.record_fact",
        new=AsyncMock(return_value=("fact_1", False)),
    ) as rec:
        asyncio.run(
            wm._record_attribute_facts(
                "ent_1",
                "user_1",
                "ws_1",
                {"role": "investor"},
                "perception",
                datetime.datetime(2026, 7, 21, tzinfo=datetime.timezone.utc),
                source_ref=SourceRef(source="gmail", event_id="evt_9"),
            )
        )
    assert rec.await_count == 1
    assert rec.call_args.kwargs["source_ref"] == {"source": "gmail", "event_id": "evt_9"}


def test_record_attribute_facts_none_source_ref_passes_none():
    from src.services.world_model import WorldModel
    from tests.conftest import make_mock_settings

    wm = WorldModel(settings=make_mock_settings(), db=AsyncMock())
    with patch(
        "src.services.entity_facts.store.EntityFactStore.record_fact",
        new=AsyncMock(return_value=("fact_1", False)),
    ) as rec:
        asyncio.run(
            wm._record_attribute_facts(
                "ent_1",
                "user_1",
                "ws_1",
                {"role": "investor"},
                "perception",
                datetime.datetime(2026, 7, 21, tzinfo=datetime.timezone.utc),
            )
        )
    assert rec.call_args.kwargs["source_ref"] is None


def test_upsert_entity_create_branch_sets_entity_source_refs_offline():
    """Offline fallback: patch _record_attribute_facts and inspect the Entity object
    handed to db.add() in the create branch, without touching a real DB."""
    from src.services.world_model import WorldModel
    from tests.conftest import make_mock_settings

    mock_db = AsyncMock()
    added = []
    mock_db.add = lambda obj: added.append(obj)
    mock_db.commit = AsyncMock()
    mock_db.execute = AsyncMock()

    wm = WorldModel(settings=make_mock_settings(), db=mock_db)
    wm._find_by_name_or_alias = AsyncMock(return_value=None)
    wm._record_attribute_facts = AsyncMock()
    wm._enqueue_failed_embedding = AsyncMock()
    wm._emit_event = AsyncMock()

    asyncio.run(
        wm.upsert_entity(
            user_id="user_1",
            entity_type="person",
            canonical_name="Jane VC",
            attributes={"role": "investor"},
            workspace_id="ws_1",
            origin="perception",
            source_ref=SourceRef(source="gmail", event_id="evt_1"),
        )
    )

    from src.models.entities import Entity

    entities = [o for o in added if isinstance(o, Entity)]
    assert len(entities) == 1
    assert entities[0].source_refs == [{"source": "gmail", "event_id": "evt_1"}]
    wm._record_attribute_facts.assert_awaited_once()
    assert wm._record_attribute_facts.call_args.kwargs.get("source_ref") == SourceRef(
        source="gmail", event_id="evt_1"
    )


def _db_reachable() -> bool:
    import asyncpg

    from src.config.settings import get_settings

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


@asynccontextmanager
async def _env():
    from sqlalchemy import delete
    from sqlalchemy.ext.asyncio import async_sessionmaker, create_async_engine
    from sqlalchemy.pool import NullPool
    from ulid import ULID

    from src.config.settings import get_settings
    from src.models.users import User, Workspace

    engine = create_async_engine(get_settings().database_url, poolclass=NullPool)
    factory = async_sessionmaker(engine, expire_on_commit=False)
    suffix = str(ULID())
    user_id = f"usr_{suffix}"
    workspace_id = f"ws_{suffix}"
    try:
        async with factory() as db:
            db.add(User(user_id=user_id, email=f"s3-{suffix}@example.com", display_name="s3"))
            db.add(Workspace(workspace_id=workspace_id, name="s3-ws", owner_user_id=user_id))
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
    from src.services.world_model import WorldModel
    from tests.conftest import make_mock_settings

    return WorldModel(settings=make_mock_settings(), db=db)


@pytest.mark.skipif(not _db_reachable(), reason="Postgres not reachable")
async def test_upsert_entity_records_and_accumulates_source_refs():
    from sqlalchemy import select

    from src.models.entities import Entity, EntityFact

    async with _env() as (factory, ws, uid):
        async with factory() as db:
            ent_id = await _wm(db).upsert_entity(
                user_id=uid,
                entity_type="person",
                canonical_name="Jane VC",
                attributes={"role": "investor"},
                workspace_id=ws,
                origin="perception",
                source_ref=SourceRef(source="gmail", event_id="evt_1"),
            )

        async with factory() as db:
            row = (await db.execute(select(Entity).where(Entity.entity_id == ent_id))).scalar_one()
            assert row.source_refs == [{"source": "gmail", "event_id": "evt_1"}]

        async with factory() as db:
            # Different attr value so EntityFactStore.record_fact takes the supersede
            # path (insert a new current row) rather than corroborate-in-place, which
            # is what actually stamps the fact's provenance with this observation's
            # source_ref (corroborate-in-place only bumps corroboration_count).
            await _wm(db).upsert_entity(
                user_id=uid,
                entity_type="person",
                canonical_name="Jane VC",
                attributes={"role": "lead investor"},
                workspace_id=ws,
                origin="perception",
                source_ref=SourceRef(source="gmail", event_id="evt_2"),
            )

        async with factory() as db:
            row = (await db.execute(select(Entity).where(Entity.entity_id == ent_id))).scalar_one()
            assert row.source_refs == [
                {"source": "gmail", "event_id": "evt_1"},
                {"source": "gmail", "event_id": "evt_2"},
            ]

            facts = (
                (await db.execute(select(EntityFact).where(EntityFact.entity_id == ent_id)))
                .scalars()
                .all()
            )
            assert any(
                (f.provenance or {}).get("source_ref") == {"source": "gmail", "event_id": "evt_2"}
                for f in facts
            )
