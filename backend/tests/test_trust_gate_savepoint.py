"""Step 7A P0 (6C follow-up #4): TrustGate.record_auto_execution_outcome must
SAVEPOINT-wrap its trust write, mirroring the sibling record_user_approval_outcome.

Regression for the lone unwrapped trust-write site: without begin_nested(), a failed
record_approval_decision poisons the shared session, so the run's own later commit
raises PendingRollbackError / InFailedSqlTransactionError on the live autonomous
auto-exec CONFIRMED path (dag_runner.py:437-440)."""

import asyncio
from contextlib import asynccontextmanager
from unittest.mock import MagicMock, patch

import pytest
from sqlalchemy import delete, text
from sqlalchemy.ext.asyncio import async_sessionmaker, create_async_engine
from sqlalchemy.pool import NullPool
from ulid import ULID

from src.config.settings import get_settings
from src.models.users import User, Workspace
from src.services.trust_gate import TrustGate


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
            db.add(User(user_id=user_id, email=f"tg-sp-{suffix}@example.com", display_name="tg-sp"))
            db.add(Workspace(workspace_id=workspace_id, name="tg-sp-ws", owner_user_id=user_id))
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


def _make_gate(db) -> TrustGate:
    return TrustGate(
        db=db,
        client=MagicMock(),
        redis=None,
        notifier_provider=lambda: None,
        store=MagicMock(),
        emitter=MagicMock(),
    )


async def test_record_auto_execution_outcome_does_not_poison_the_session():
    """A failed record_approval_decision must roll back only its own SAVEPOINT and
    leave the shared session usable: the subsequent SELECT + commit must succeed (no
    PendingRollbackError/InFailedSqlTransactionError) and the session must stay active."""
    async with _run_env() as (factory, ws, uid):
        async with factory() as db:
            gate = _make_gate(db)

            async def _boom(_db, *args, **kwargs):
                await _db.execute(text("SELECT 1/0"))

            with patch("src.services.risk_assessor.record_approval_decision", _boom):
                await gate.record_auto_execution_outcome("email.send", "high", ws)

            # The shared session must have survived the swallowed failure — this is
            # exactly what the run's own later commit depends on.
            result = await db.execute(text("SELECT 1"))
            assert result.scalar() == 1
            await db.commit()
            assert db.is_active
