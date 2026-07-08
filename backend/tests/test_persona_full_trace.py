"""Real-DB proof that the Persona batch tick learns over the full interaction
trace (response_preview + plan_summary + intent + timestamp), not a lossy
one-liner that drops everything but message_preview -> intent.

Skips when Postgres is unreachable. Mirrors the test_entity_resolver_db env.
"""

import asyncio
from contextlib import asynccontextmanager
from datetime import datetime, timedelta, timezone
from unittest.mock import AsyncMock

import pytest
from sqlalchemy import delete
from sqlalchemy.ext.asyncio import async_sessionmaker, create_async_engine
from sqlalchemy.pool import NullPool
from ulid import ULID

from src.config.settings import get_settings
from src.models.interaction_log import InteractionLog
from src.models.users import User, Workspace
from src.services.scheduler.persona_tick import PersonaTickMixin


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


class _PersonaTickHost(PersonaTickMixin):
    """Minimal object carrying just what _tick_persona_batch needs."""

    def __init__(self, orchestrator):
        self._tick_count = 0
        self._last_persona_batch_at = None
        self._orchestrator = orchestrator


@asynccontextmanager
async def _seeded_interactions():
    engine = create_async_engine(get_settings().database_url, poolclass=NullPool)
    factory = async_sessionmaker(engine, expire_on_commit=False)
    suffix = str(ULID())
    user_id = f"usr_{suffix}"
    workspace_id = f"ws_{suffix}"
    try:
        async with factory() as db:
            db.add(User(user_id=user_id, email=f"persona-{suffix}@example.com", display_name="p"))
            db.add(Workspace(workspace_id=workspace_id, name="persona-ws", owner_user_id=user_id))
            await db.commit()

        now = datetime.now(timezone.utc)
        async with factory() as db:
            for idx, letter in enumerate("ABCDE"):
                db.add(
                    InteractionLog(
                        interaction_id=f"ilog_{suffix}_{idx}",
                        workspace_id=workspace_id,
                        user_id=user_id,
                        trace_id=f"trc_{suffix}_{idx}",
                        message_preview=f"msg-sentinel-{letter}",
                        intent="command",
                        plan_summary=f"plan-sentinel-{letter}",
                        response_preview=f"resp-sentinel-{letter}",
                        created_at=now - timedelta(minutes=idx),
                    )
                )
            await db.commit()
        yield factory, workspace_id, user_id
    finally:
        try:
            async with factory() as db:
                await db.execute(
                    delete(InteractionLog).where(InteractionLog.workspace_id == workspace_id)
                )
                await db.execute(delete(Workspace).where(Workspace.workspace_id == workspace_id))
                await db.execute(delete(User).where(User.user_id == user_id))
                await db.commit()
        except Exception:  # pragma: no cover
            pass
        await engine.dispose()


async def test_persona_batch_message_carries_full_trace():
    async with _seeded_interactions() as (factory, workspace_id, user_id):
        orchestrator = AsyncMock()
        orchestrator._call_agent = AsyncMock(return_value="ok")
        host = _PersonaTickHost(orchestrator)

        await host._tick_persona_batch(factory=factory)

        orchestrator._call_agent.assert_awaited()
        call_args = orchestrator._call_agent.call_args
        assert call_args[0][0] == "persona"
        message = call_args.kwargs["message"]

        for letter in "ABCDE":
            assert f"resp-sentinel-{letter}" in message, (
                f"response_preview sentinel {letter} missing from trace — "
                "persona tick is still discarding response_preview"
            )
            assert f"plan-sentinel-{letter}" in message, (
                f"plan_summary sentinel {letter} missing from trace — "
                "persona tick is still discarding plan_summary"
            )
