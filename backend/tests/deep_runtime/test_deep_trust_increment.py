"""Step 7C P2: the deep-path trust-increment-on-CONFIRMED helper.

Real-Postgres, self-contained (module-level `_db_reachable` skipif + own NullPool engine +
User→Workspace seed in FK-flush order — NO shared `db_session` fixture). Mirror of the autonomous
deferred-tick savepoint test (test_deferred_verification_savepoint_db.py).
"""

import asyncio
from contextlib import asynccontextmanager

import pytest
from sqlalchemy import delete, select
from sqlalchemy.ext.asyncio import async_sessionmaker, create_async_engine
from sqlalchemy.pool import NullPool
from ulid import ULID

from src.config.settings import get_settings
from src.deep_runtime.trust_increment import record_deep_confirmed_outcome
from src.models.trust_state import TrustState
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
async def _ws_env():
    engine = create_async_engine(get_settings().database_url, poolclass=NullPool)
    factory = async_sessionmaker(engine, expire_on_commit=False)
    suffix = str(ULID())
    user_id = f"usr_{suffix}"
    workspace_id = f"ws_{suffix}"
    try:
        async with factory() as db:
            db.add(User(user_id=user_id, email=f"p2-{suffix}@example.com", display_name="p2"))
            db.add(Workspace(workspace_id=workspace_id, name="p2-ws", owner_user_id=user_id))
            await db.commit()
        yield factory, workspace_id, user_id
    finally:
        try:
            async with factory() as db:
                # trust_states FK to workspaces is ON DELETE CASCADE → removed with the ws.
                await db.execute(delete(Workspace).where(Workspace.workspace_id == workspace_id))
                await db.execute(delete(User).where(User.user_id == user_id))
                await db.commit()
        except Exception:  # pragma: no cover
            pass
        await engine.dispose()


async def test_confirmed_increment_persists():
    """A CONFIRMED deep write increments TrustState.approved_count and commits durably."""
    async with _ws_env() as (factory, ws, _uid):
        await record_deep_confirmed_outcome(
            db_factory=factory,
            workspace_id=ws,
            capability="email.send",
            risk_level="high",
        )

        async with factory() as db:
            state = (
                await db.execute(
                    select(TrustState).where(
                        TrustState.workspace_id == ws,
                        TrustState.capability == "email.send",
                        TrustState.risk_level == "high",
                    )
                )
            ).scalar_one()
            assert state.approved_count == 1


async def test_best_effort_swallows_a_failing_increment():
    """LOAD-BEARING guard, with teeth: a raising record_approval_decision must NOT propagate.

    The helper imports record_approval_decision INSIDE the function from src.services.risk_assessor,
    so we patch it there. Assert the helper reached it (await_count == 1) AND returned normally.

    Negative control: delete the outer try/except in trust_increment.py → this RuntimeError
    propagates → this test goes red (reproduced by the Phase 4 / holistic pass).
    """
    from unittest.mock import AsyncMock, patch

    boom = AsyncMock(side_effect=RuntimeError("boom"))

    async with _ws_env() as (factory, ws, _uid):
        with patch("src.services.risk_assessor.record_approval_decision", boom):
            # Must NOT raise — the best-effort try/except swallows the RuntimeError.
            await record_deep_confirmed_outcome(
                db_factory=factory,
                workspace_id=ws,
                capability="email.send",
                risk_level="high",
            )

        assert boom.await_count == 1  # proves we reached the swallowed inner call, not a no-op

        # And no TrustState row leaked (the failing increment rolled back its SAVEPOINT).
        async with factory() as db:
            leaked = (
                await db.execute(select(TrustState).where(TrustState.workspace_id == ws))
            ).scalar_one_or_none()
            assert leaked is None
