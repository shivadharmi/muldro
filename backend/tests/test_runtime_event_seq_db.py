"""runtime_events.seq is a server-monotonic BIGINT ordering column (Step 5, §4.8).
Real-DB: assigned at INSERT with no client value, strictly increasing across a
session boundary (resume-across-deploy)."""

import asyncio
from contextlib import asynccontextmanager

import pytest
from sqlalchemy import delete, select
from sqlalchemy.ext.asyncio import async_sessionmaker, create_async_engine
from sqlalchemy.pool import NullPool
from ulid import ULID

from src.config.settings import get_settings
from src.models.runtime_event import RuntimeEvent
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


@asynccontextmanager
async def _run_env():
    engine = create_async_engine(get_settings().database_url, poolclass=NullPool)
    factory = async_sessionmaker(engine, expire_on_commit=False)
    suffix = str(ULID())
    user_id = f"usr_{suffix}"
    workspace_id = f"ws_{suffix}"
    try:
        async with factory() as db:
            db.add(User(user_id=user_id, email=f"s5-{suffix}@example.com", display_name="s5"))
            db.add(Workspace(workspace_id=workspace_id, name="s5-ws", owner_user_id=user_id))
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


def test_seq_column_declared():
    cols = set(RuntimeEvent.__table__.c.keys())
    assert "seq" in cols
    assert RuntimeEvent.__table__.c.seq.nullable is False


async def test_seq_is_server_assigned_and_monotonic():
    async with _run_env() as (factory, ws, _uid):
        seqs: list[int] = []
        # Two separate committed sessions == a deploy/resume boundary.
        for i in range(2):
            async with factory() as db:
                e = RuntimeEvent(
                    event_id=f"revt_{ULID()}",
                    workspace_id=ws,
                    event_type="spike",
                    payload={"i": i},
                )
                db.add(e)
                await db.commit()
                await db.refresh(e)
                assert e.seq is not None  # server-assigned, no client value
                seqs.append(e.seq)
        assert seqs[1] > seqs[0]  # strictly increasing across the boundary


async def test_events_orderable_by_seq():
    async with _run_env() as (factory, ws, _uid):
        async with factory() as db:
            for i in range(3):
                db.add(
                    RuntimeEvent(
                        event_id=f"revt_{ULID()}", workspace_id=ws, event_type=f"e{i}", payload={}
                    )
                )
            await db.commit()
        async with factory() as db:
            rows = (
                (
                    await db.execute(
                        select(RuntimeEvent)
                        .where(RuntimeEvent.workspace_id == ws)
                        .order_by(RuntimeEvent.seq)
                    )
                )
                .scalars()
                .all()
            )
        got = [r.seq for r in rows]
        assert got == sorted(got)
        assert len(set(got)) == len(got)  # distinct
