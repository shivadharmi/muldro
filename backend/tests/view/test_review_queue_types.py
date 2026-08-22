"""The review queue holds everything it can act on — against REAL SQL.

It asked for `prepared_action` alone while five approval types existed, so a
filter proposal, a step approval and the Governor's plan-level rows were
written and rendered nowhere. A queue nobody renders looks exactly like a queue
with nothing in it, which is why that went unnoticed.

These run against Postgres deliberately. The first attempt at the widened query
used `artifact_refs['chat'].astext != 'true'`, which is correct-looking and
wrong: a row with no `chat` key yields SQL NULL from the JSONB lookup, and
`NULL != 'true'` is NULL rather than TRUE, so Postgres dropped every approval
that was NOT a chat one. A fake db that compares dicts in Python agrees with
the broken version.
"""

import asyncio
from contextlib import asynccontextmanager

import pytest
from sqlalchemy.ext.asyncio import async_sessionmaker, create_async_engine
from sqlalchemy.pool import NullPool
from ulid import ULID

from src.config.settings import get_settings
from src.models.approvals import Approval
from src.models.users import User, Workspace
from src.view.domain_units import prepared_work_unit


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
    except Exception:
        return False


pytestmark = pytest.mark.skipif(not _db_reachable(), reason="Postgres not reachable")


@asynccontextmanager
async def _session():
    engine = create_async_engine(get_settings().database_url, poolclass=NullPool)
    factory = async_sessionmaker(engine, expire_on_commit=False)
    async with factory() as db:
        yield db
    await engine.dispose()


async def _seed(db) -> tuple[str, str]:
    suffix = str(ULID())
    uid, ws = f"usr_{suffix}", f"ws_{suffix}"
    db.add(User(user_id=uid, email=f"queue-{suffix}@example.com", display_name="q"))
    await db.flush()
    db.add(Workspace(workspace_id=ws, name="q", owner_user_id=uid))
    await db.flush()
    return uid, ws


def _approval(uid, ws, *, approval_type, refs=None, title="A decision"):
    return Approval(
        approval_id=f"apr_{ULID()}",
        user_id=uid,
        workspace_id=ws,
        execution_id="",
        approval_type=approval_type,
        title=title,
        artifact_refs=refs,
        risk_level="low",
        status="pending",
    )


async def _queue(db, uid, ws):
    return await prepared_work_unit(db, workspace_id=ws, user_id=uid)


@pytest.mark.asyncio
async def test_a_filter_proposal_reaches_the_queue():
    """It has no `chat` key at all — the case the NULL comparison dropped."""
    async with _session() as db:
        uid, ws = await _seed(db)
        db.add(_approval(uid, ws, approval_type="filter_proposal", refs={"senders": []}))
        await db.flush()
        unit = await _queue(db, uid, ws)
        assert unit is not None
        assert unit.frame.event_count == 1
        await db.rollback()


@pytest.mark.asyncio
async def test_an_approval_with_no_artifact_refs_at_all_reaches_it():
    async with _session() as db:
        uid, ws = await _seed(db)
        db.add(_approval(uid, ws, approval_type="step:email.send", refs=None))
        await db.flush()
        assert await _queue(db, uid, ws) is not None
        await db.rollback()


@pytest.mark.asyncio
async def test_a_prepared_action_still_reaches_it():
    async with _session() as db:
        uid, ws = await _seed(db)
        db.add(_approval(uid, ws, approval_type="prepared_action", refs={"prepared": True}))
        await db.flush()
        assert await _queue(db, uid, ws) is not None
        await db.rollback()


@pytest.mark.asyncio
async def test_a_chat_approval_is_excluded():
    """Those resume a suspended turn via /chat/resume and are 409'd by the
    decide endpoints on purpose. They are answered in the conversation."""
    async with _session() as db:
        uid, ws = await _seed(db)
        db.add(_approval(uid, ws, approval_type="tool:send_email", refs={"chat": True}))
        await db.flush()
        assert await _queue(db, uid, ws) is None
        await db.rollback()


@pytest.mark.asyncio
async def test_mixed_types_are_counted_together_and_chat_is_not():
    async with _session() as db:
        uid, ws = await _seed(db)
        db.add(_approval(uid, ws, approval_type="prepared_action", refs={"prepared": True}))
        db.add(_approval(uid, ws, approval_type="filter_proposal", refs={"senders": []}))
        db.add(_approval(uid, ws, approval_type="email.send", refs={"plan_id": "p"}))
        db.add(_approval(uid, ws, approval_type="tool:x", refs={"chat": True}))
        await db.flush()
        unit = await _queue(db, uid, ws)
        assert unit is not None
        assert unit.frame.event_count == 3
        await db.rollback()


@pytest.mark.asyncio
async def test_the_headline_does_not_claim_everything_was_prepared():
    """A step approval sits on a run already underway; a proposal has nothing
    staged at all. "prepared for your review" was untrue of both."""
    async with _session() as db:
        uid, ws = await _seed(db)
        db.add(_approval(uid, ws, approval_type="filter_proposal", refs={"senders": []}))
        await db.flush()
        unit = await _queue(db, uid, ws)
        assert "prepared" not in unit.frame.headline.lower()
        assert "waiting for you" in unit.frame.headline
        await db.rollback()


@pytest.mark.asyncio
async def test_an_empty_queue_is_absent_not_a_card_announcing_idleness():
    async with _session() as db:
        uid, ws = await _seed(db)
        assert await _queue(db, uid, ws) is None
        await db.rollback()
