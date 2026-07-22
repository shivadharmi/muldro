"""Real-DB regression: external connector ids (e.g. Google Calendar recurring-instance
ids) exceed the old varchar(128/256) bounds. normalized_events.entity_id /
idempotency_key and dead_letter_queue.source_id are Text — inserting a long id must
not raise StringDataRightTruncationError (the perception_cycle crash)."""

import asyncio
from contextlib import asynccontextmanager
from datetime import datetime, timezone

import pytest
from sqlalchemy import delete, select
from sqlalchemy.ext.asyncio import async_sessionmaker, create_async_engine
from sqlalchemy.pool import NullPool
from ulid import ULID

from src.config.settings import get_settings
from src.models.dead_letter import DeadLetterEntry
from src.models.events import NormalizedEvent
from src.models.users import User, Workspace


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

# A realistic long Google Calendar recurring-instance id (288 chars — over both the
# old entity_id varchar(128) and the idempotency_key varchar(256)).
_LONG_ID = "e9im6r31d5miqobjedkn6t1d" * 12


@asynccontextmanager
async def _env():
    engine = create_async_engine(get_settings().database_url, poolclass=NullPool)
    factory = async_sessionmaker(engine, expire_on_commit=False)
    suffix = str(ULID())
    uid, wid = f"usr_{suffix}", f"ws_{suffix}"
    try:
        async with factory() as db:
            db.add(User(user_id=uid, email=f"lid-{suffix}@example.com", display_name="lid"))
            db.add(Workspace(workspace_id=wid, name="lid-ws", owner_user_id=uid))
            await db.commit()
        yield factory, wid, uid
    finally:
        try:
            async with factory() as db:
                await db.execute(delete(NormalizedEvent).where(NormalizedEvent.workspace_id == wid))
                await db.execute(delete(DeadLetterEntry).where(DeadLetterEntry.workspace_id == wid))
                await db.execute(delete(Workspace).where(Workspace.workspace_id == wid))
                await db.execute(delete(User).where(User.user_id == uid))
                await db.commit()
        except Exception:  # pragma: no cover
            pass
        await engine.dispose()


async def test_long_entity_id_and_idempotency_key_persist():
    async with _env() as (factory, wid, uid):
        async with factory() as db:
            db.add(
                NormalizedEvent(
                    event_id=f"evt_{ULID()}",
                    user_id=uid,
                    workspace_id=wid,
                    source="calendar",
                    source_account_id="acct_1",
                    event_type="event_created",
                    entity_type="calendar_event",
                    entity_id=_LONG_ID,
                    occurred_at=datetime.now(timezone.utc),
                    # embeds the long entity_id → > 256 chars (old idempotency_key bound)
                    idempotency_key=f"calendar:{_LONG_ID}:event_created",
                )
            )
            await db.commit()  # pre-migration: StringDataRightTruncationError

        async with factory() as db:
            row = (
                await db.execute(
                    select(NormalizedEvent).where(NormalizedEvent.entity_id == _LONG_ID)
                )
            ).scalar_one()
            assert row.entity_id == _LONG_ID
            assert len(row.idempotency_key) > 256


async def test_long_dlq_source_id_persists():
    async with _env() as (factory, wid, uid):
        async with factory() as db:
            db.add(
                DeadLetterEntry(
                    entry_id=f"dlq_{ULID()}",
                    user_id=uid,
                    workspace_id=wid,
                    operation_type="event_ingest",
                    source_id=_LONG_ID,
                    error_type="StringDataRightTruncationError",
                )
            )
            await db.commit()  # pre-migration: StringDataRightTruncationError
