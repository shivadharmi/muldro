"""Real-DB regression: mark_retrying flushes last_attempted_at to a TIMESTAMP column,
so it must assign a datetime (not an ISO string) — asyncpg rejects a str for a DateTime
column with DataError. Mocked tests can't catch this (a MagicMock never flushes)."""

import asyncio
from contextlib import asynccontextmanager
from datetime import datetime

import pytest
from sqlalchemy import delete, select
from sqlalchemy.ext.asyncio import async_sessionmaker, create_async_engine
from sqlalchemy.pool import NullPool
from ulid import ULID

from src.config.settings import get_settings
from src.models.dead_letter import DeadLetterEntry
from src.models.users import User, Workspace
from src.services.dead_letter import DeadLetterService


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
            db.add(User(user_id=uid, email=f"dlq-{suffix}@example.com", display_name="dlq"))
            db.add(Workspace(workspace_id=wid, name="dlq-ws", owner_user_id=uid))
            await db.commit()
        yield factory, wid, uid
    finally:
        try:
            async with factory() as db:
                await db.execute(delete(DeadLetterEntry).where(DeadLetterEntry.workspace_id == wid))
                await db.execute(delete(Workspace).where(Workspace.workspace_id == wid))
                await db.execute(delete(User).where(User.user_id == uid))
                await db.commit()
        except Exception:  # pragma: no cover
            pass
        await engine.dispose()


async def test_mark_retrying_persists_datetime_not_string():
    async with _env() as (factory, wid, uid):
        async with factory() as db:
            entry_id = await DeadLetterService(db).enqueue(
                user_id=uid,
                operation_type="event_ingest",
                error_type="SomeError",
                error_message="boom",
                workspace_id=wid,
            )
            await db.commit()

        async with factory() as db:
            # Pre-fix this raised asyncpg DataError on flush (str for a TIMESTAMP column).
            ok = await DeadLetterService(db).mark_retrying(entry_id)
            await db.commit()
        assert ok is True

        async with factory() as db:
            row = (
                await db.execute(
                    select(DeadLetterEntry).where(DeadLetterEntry.entry_id == entry_id)
                )
            ).scalar_one()
            assert row.status == "retrying"
            assert row.attempt_count == 2
            assert isinstance(row.last_attempted_at, datetime)
