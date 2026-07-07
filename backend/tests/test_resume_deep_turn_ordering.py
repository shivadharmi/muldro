"""Step 6C CF-5: ``resume_deep_turn`` must validate the rebuild inputs BEFORE it
consumes (flips status + commits) the Approval.

Regression guard for a commit-ordering bug: a malformed approval (missing
``thread_id``/``agent_name`` in ``artifact_refs``, or naming an unknown agent) used to
be flipped to approved/rejected and committed BEFORE the rebuild inputs were validated,
then errored — permanently stranding it (``status != "pending"`` means it can never be
re-resumed). The fix reorders the checks so a malformed approval stays ``pending`` and
re-resumable.

Real-DB test: skips (does not fail) when Postgres is unreachable, mirroring
``tests/test_deep_gate_end_to_end.py`` (whose real-DB idiom this reuses). Each test
builds its own NullPool engine bound to its own loop and disposes it in a ``finally``.
"""

from __future__ import annotations

import asyncio
from contextlib import asynccontextmanager
from unittest.mock import AsyncMock, MagicMock

import pytest
from sqlalchemy import delete
from sqlalchemy.ext.asyncio import async_sessionmaker, create_async_engine
from sqlalchemy.pool import NullPool
from ulid import ULID

from src.config.settings import get_settings
from src.models.approvals import Approval
from src.models.trust_state import TrustCeiling, TrustState
from src.models.users import User, Workspace
from src.orchestrator.agent_invoker import AgentInvoker
from src.orchestrator.agents import SubAgent
from tests.conftest import make_mock_settings


def _db_reachable() -> bool:
    """Best-effort raw connect to Postgres to decide whether to skip.

    Mirrors ``tests/test_deep_gate_end_to_end.py``: a raw asyncpg connect on its own
    throwaway loop, never touching the app's process-wide cached engine.
    """
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
    except Exception:  # pragma: no cover - environment-dependent
        return False


pytestmark = pytest.mark.skipif(not _db_reachable(), reason="Postgres not reachable")


@asynccontextmanager
async def _gate_env():
    """Yield ``(factory, user_id, workspace_id)`` with the FK parents seeded.

    Teardown deletes Approvals + TrustStates + TrustCeilings for the workspace, then
    the Workspace + User, then disposes the engine — all on this test's own loop.
    """
    engine = create_async_engine(get_settings().database_url, poolclass=NullPool)
    factory = async_sessionmaker(engine, expire_on_commit=False)
    suffix = str(ULID())
    user_id = f"usr_{suffix}"
    workspace_id = f"ws_{suffix}"
    try:
        async with factory() as db:
            db.add(
                User(
                    user_id=user_id,
                    email=f"cf5-{suffix}@example.com",
                    display_name="cf5-ordering-test",
                )
            )
            db.add(Workspace(workspace_id=workspace_id, name="cf5-ws", owner_user_id=user_id))
            await db.commit()
        yield factory, user_id, workspace_id
    finally:
        try:
            async with factory() as db:
                await db.execute(delete(Approval).where(Approval.workspace_id == workspace_id))
                await db.execute(delete(TrustState).where(TrustState.workspace_id == workspace_id))
                await db.execute(
                    delete(TrustCeiling).where(TrustCeiling.workspace_id == workspace_id)
                )
                await db.execute(delete(Workspace).where(Workspace.workspace_id == workspace_id))
                await db.execute(delete(User).where(User.user_id == user_id))
                await db.commit()
        except Exception:  # pragma: no cover - teardown best-effort
            pass
        await engine.dispose()


def _make_invoker(*, factory) -> AgentInvoker:
    """A minimal real ``AgentInvoker`` exposing ``resume_deep_turn`` over a real DB.

    Only ``db_factory_provider`` and ``agents`` matter for these tests — a malformed
    approval must error out (and stay pending) INSIDE the db block, before any rebuild,
    so the model/tool machinery is never reached.
    """
    tool_executor = MagicMock()
    tool_executor.apply_cache_control_to_tools = lambda tools: tools
    tool_executor.get_tools_for_agent = AsyncMock(return_value=[])

    context = MagicMock()
    context.assemble_context = AsyncMock(return_value="")

    agent = SubAgent(
        name="executor", prompt="p", model_tier="sonnet", capability_scope={"email.send"}
    )

    return AgentInvoker(
        settings=make_mock_settings(runtime="deep"),
        client=MagicMock(),
        services=None,
        budget=MagicMock(),
        circuit_breaker=MagicMock(),
        db_factory_provider=lambda: factory,
        tool_executor=tool_executor,
        context=context,
        agents={"executor": agent},
        checkpointer_provider=lambda: MagicMock(),
    )


def _seed_pending_approval(*, approval_id: str, user_id: str, workspace_id: str, refs: dict):
    return Approval(
        approval_id=approval_id,
        user_id=user_id,
        workspace_id=workspace_id,
        execution_id=f"exec_{ULID()}",
        approval_type="send_email",
        title="CF-5 malformed approval",
        artifact_refs=refs,
        status="pending",
    )


async def test_malformed_missing_thread_id_stays_pending():
    """An approval whose ``artifact_refs`` has ``tool_call_id`` but NO ``thread_id``
    must yield an error frame AND stay ``pending`` (re-resumable) — not be consumed."""
    async with _gate_env() as (factory, user_id, workspace_id):
        approval_id = f"apr_{ULID()}"
        async with factory() as db:
            db.add(
                _seed_pending_approval(
                    approval_id=approval_id,
                    user_id=user_id,
                    workspace_id=workspace_id,
                    # MALFORMED: has tool_call_id + agent_name but NO thread_id.
                    refs={"tool_call_id": "call_send_1", "agent_name": "executor"},
                )
            )
            await db.commit()

        invoker = _make_invoker(factory=factory)
        frames = [
            f
            async for f in invoker.resume_deep_turn(
                approval_id=approval_id,
                decision="approve",
                user_id=user_id,
                workspace_id=workspace_id,
            )
        ]

        assert any(f.get("event") == "error" for f in frames), f"frames={frames}"
        # CF-5: the malformed approval must NOT have been consumed — still pending.
        async with factory() as db2:
            refreshed = await db2.get(Approval, approval_id)
            assert refreshed.status == "pending", (
                f"malformed approval was consumed; status={refreshed.status}"
            )


async def test_unknown_agent_stays_pending():
    """An approval that carries ``thread_id`` + an ``agent_name`` naming a NON-existent
    agent must yield an error frame AND stay ``pending`` — not be consumed."""
    async with _gate_env() as (factory, user_id, workspace_id):
        approval_id = f"apr_{ULID()}"
        thread_id = f"chat_{ULID()}"
        async with factory() as db:
            db.add(
                _seed_pending_approval(
                    approval_id=approval_id,
                    user_id=user_id,
                    workspace_id=workspace_id,
                    # Well-formed refs but the agent does not exist in this invoker.
                    refs={"thread_id": thread_id, "agent_name": "nonexistent_agent"},
                )
            )
            await db.commit()

        invoker = _make_invoker(factory=factory)
        frames = [
            f
            async for f in invoker.resume_deep_turn(
                approval_id=approval_id,
                decision="approve",
                user_id=user_id,
                workspace_id=workspace_id,
            )
        ]

        assert any(f.get("event") == "error" for f in frames), f"frames={frames}"
        async with factory() as db2:
            refreshed = await db2.get(Approval, approval_id)
            assert refreshed.status == "pending", (
                f"unknown-agent approval was consumed; status={refreshed.status}"
            )
