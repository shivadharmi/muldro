"""Step 6C CF-4: checkpoint reaper for the durable LangGraph saver.

Three layers of proof:

* Unit — ``reap_thread`` gating: a real ``adelete_thread`` is called for a durable saver;
  a ``None`` saver, a saver WITHOUT ``adelete_thread`` (MemorySaver-shaped), an empty
  thread_id, and an ``adelete_thread`` that raises all no-op to ``False`` (never crash).
* Real-DB — ``sweep_decided_approval_checkpoints``: reaps only DECIDED approvals older than
  the retention window; a still-PENDING approval's thread is NEVER touched, and a
  recently-decided (inside-window) approval is left alone.
* Real-saver end-to-end — the primary reaper wired into ``AgentInvoker.call_agent_stream``:
  a NON-paused turn's checkpoints are reaped from the real ``checkpoints`` table (count → 0),
  a PAUSED turn's checkpoints SURVIVE (count > 0, so resume still works), and a NEGATIVE
  CONTROL proves that without the reap call the non-paused thread's rows remain.

No Docker/Anthropic dependency: skips (does not fail) when Postgres is unreachable, mirroring
``test_deep_gate_end_to_end.py`` / ``test_deep_gate_durable_resume_db.py``. Each test builds
its own engine bound to its own event loop (this repo's custom async-test hook runs every
test via a fresh ``asyncio.run``).
"""

from __future__ import annotations

import asyncio
import json
from contextlib import asynccontextmanager
from datetime import datetime, timedelta, timezone
from types import SimpleNamespace
from unittest.mock import AsyncMock, MagicMock, patch

import asyncpg
import pytest
from langchain_core.language_models.chat_models import BaseChatModel
from langchain_core.messages import AIMessage, AIMessageChunk, ToolMessage
from langchain_core.messages.tool import tool_call_chunk
from langchain_core.outputs import ChatGeneration, ChatGenerationChunk, ChatResult
from sqlalchemy import delete
from sqlalchemy.ext.asyncio import async_sessionmaker, create_async_engine
from sqlalchemy.pool import NullPool
from ulid import ULID

from src.config.settings import get_settings
from src.deep_runtime.checkpoint_reaper import (
    reap_thread,
    sweep_decided_approval_checkpoints,
)
from src.deep_runtime.checkpointer import build_async_postgres_saver
from src.models.approvals import Approval
from src.models.trust_state import TrustCeiling, TrustState
from src.models.users import User, Workspace
from src.orchestrator.agent_invoker import AgentInvoker
from src.orchestrator.agents import SubAgent
from tests.conftest import make_mock_settings

AGENT_INVOKER_MODULE = "src.orchestrator.agent_invoker"
TRUST_GATE_MODULE = "src.deep_runtime.middleware.trust_gate"
CAP_SCOPE_MODULE = "src.deep_runtime.middleware.capability_scope"
AGENT_BUILDER_MODULE = "src.deep_runtime.agent_builder"

TOOL_DEF = {
    "name": "send_email",
    "description": "Send an email on the user's behalf.",
    "input_schema": {"type": "object", "properties": {"to": {"type": "string"}}},
}
TOOL_ARGS = {"to": "vip@example.com"}


# ── (1) UNIT: reap_thread gating (no DB) ──────────────────────────────────────────


class _FakeSaver:
    """Records ``adelete_thread`` calls, standing in for the durable AsyncPostgresSaver."""

    def __init__(self) -> None:
        self.deleted: list[str] = []

    async def adelete_thread(self, thread_id: str) -> None:
        self.deleted.append(thread_id)


async def test_reap_thread_calls_adelete_and_returns_true():
    fake = _FakeSaver()
    assert await reap_thread(fake, "t1") is True
    assert fake.deleted == ["t1"]


async def test_reap_thread_none_saver_returns_false():
    assert await reap_thread(None, "t1") is False


async def test_reap_thread_saver_without_adelete_returns_false():
    # A bare object (MemorySaver-shaped: no adelete_thread) must no-op, never crash.
    assert await reap_thread(object(), "t1") is False


async def test_reap_thread_empty_thread_id_returns_false():
    fake = _FakeSaver()
    assert await reap_thread(fake, "") is False
    assert fake.deleted == []


async def test_reap_thread_swallows_adelete_error():
    class _Boom:
        async def adelete_thread(self, thread_id: str) -> None:  # noqa: ARG002
            raise RuntimeError("delete failed")

    assert await reap_thread(_Boom(), "t1") is False


async def test_sweep_none_saver_returns_zero():
    # No durable saver reachable → the sweep is a pure no-op (never queries the DB).
    assert await sweep_decided_approval_checkpoints(None, None, retention_hours=24) == 0


async def test_sweep_saver_without_adelete_returns_zero():
    assert await sweep_decided_approval_checkpoints(object(), None, retention_hours=24) == 0


# ── real-DB harness (shared by the sweep + primary-reaper proofs) ─────────────────


def _db_reachable() -> bool:
    """Best-effort raw connect to Postgres to decide whether to skip."""
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


_DB_OK = _db_reachable()
requires_db = pytest.mark.skipif(not _DB_OK, reason="Postgres not reachable")


@asynccontextmanager
async def _gate_env():
    """Yield ``(factory, user_id, workspace_id)`` with the FK parents seeded.

    Teardown deletes Approvals + TrustStates + TrustCeilings for the workspace, then the
    Workspace + User, then disposes the engine — all on this test's own loop.
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
                    email=f"reaper-{suffix}@example.com",
                    display_name="checkpoint-reaper-test",
                )
            )
            db.add(Workspace(workspace_id=workspace_id, name="reaper-ws", owner_user_id=user_id))
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


async def _seed_approval(
    factory,
    *,
    user_id: str,
    workspace_id: str,
    thread_id: str,
    status: str,
    decided_at: datetime | None,
) -> None:
    """Insert one Approval row (only the fields the sweep query reads matter)."""
    async with factory() as db:
        db.add(
            Approval(
                approval_id=f"apr_{ULID()}",
                user_id=user_id,
                workspace_id=workspace_id,
                execution_id=f"exec_{ULID()}",
                approval_type="tool:send_email",
                title="seed",
                thread_id=thread_id,
                status=status,
                decided_at=decided_at,
            )
        )
        await db.commit()


async def _count_checkpoints(thread_id: str) -> int:
    """Raw asyncpg count of ``checkpoints`` rows for one thread."""
    dsn = get_settings().database_url.replace("+asyncpg", "", 1)
    conn = await asyncpg.connect(dsn=dsn)
    try:
        return await conn.fetchval(
            "SELECT count(*) FROM checkpoints WHERE thread_id = $1", thread_id
        )
    finally:
        await conn.close()


async def _delete_checkpoint_rows(thread_id: str) -> None:
    """Raw asyncpg cleanup of the ``checkpoints*`` rows for one thread (teardown)."""
    dsn = get_settings().database_url.replace("+asyncpg", "", 1)
    conn = await asyncpg.connect(dsn=dsn)
    try:
        for tbl in ("checkpoint_writes", "checkpoint_blobs", "checkpoints"):
            try:
                await conn.execute(f"DELETE FROM {tbl} WHERE thread_id = $1", thread_id)
            except Exception:  # noqa: BLE001 - best-effort cleanup
                pass
    finally:
        await conn.close()


# ── (2) REAL-DB: retention sweep guards DECIDED-only, never PENDING ───────────────


@requires_db
async def test_sweep_reaps_only_decided_older_than_window():
    """Seed three approvals on one workspace: an OLD-decided (48h ago), a still-PENDING
    (no decided_at), and a RECENTLY-decided (1h ago, inside the 24h window). The sweep must
    reap ONLY the old-decided thread — never the pending one (the load-bearing guard), and
    never the recent one."""
    async with _gate_env() as (factory, user_id, workspace_id):
        now = datetime.now(timezone.utc)
        suffix = str(ULID())
        old_tid = f"old_decided_{suffix}"
        pending_tid = f"pending_{suffix}"
        recent_tid = f"recent_decided_{suffix}"

        await _seed_approval(
            factory,
            user_id=user_id,
            workspace_id=workspace_id,
            thread_id=old_tid,
            status="approved",
            decided_at=now - timedelta(hours=48),
        )
        await _seed_approval(
            factory,
            user_id=user_id,
            workspace_id=workspace_id,
            thread_id=pending_tid,
            status="pending",
            decided_at=None,
        )
        await _seed_approval(
            factory,
            user_id=user_id,
            workspace_id=workspace_id,
            thread_id=recent_tid,
            status="approved",
            decided_at=now - timedelta(hours=1),
        )

        fake = _FakeSaver()
        reaped = await sweep_decided_approval_checkpoints(fake, factory, retention_hours=24)

        deleted = set(fake.deleted)
        assert old_tid in deleted, "a decided-old approval's thread must be reaped"
        assert pending_tid not in deleted, (
            "a still-PENDING approval's thread must NEVER be reaped (the guard)"
        )
        assert recent_tid not in deleted, (
            "a recently-decided approval (inside the window) must not be reaped"
        )
        # The sweep is global; the surrounding DB is clean of decided-old approvals, so exactly
        # our one old-decided thread is reaped and the return value matches the actual deletes.
        assert reaped == 1, f"expected exactly one reaped thread, got {reaped}"
        assert reaped == len(deleted)


@requires_db
async def test_sweep_skips_thread_with_a_pending_approval_even_if_another_is_decided_old():
    """A single deep turn reuses ONE thread_id across tool calls, so two sequential writes
    produce two Approval rows sharing that thread_id. If write#1 is approved (decided, >24h
    ago) while write#2 is still PENDING on the SAME thread, the sweep must NOT reap that
    thread — the pending sibling's resume still needs the checkpoint. The guard is
    per-THREAD (``decided - pending``), not per-row: even though the shared thread has a
    decided-old approval, its pending sibling protects it."""
    async with _gate_env() as (factory, user_id, workspace_id):
        now = datetime.now(timezone.utc)
        shared_tid = f"shared_thread_{ULID()}"

        # write#1 on the shared thread: approved 48h ago (decided + old).
        await _seed_approval(
            factory,
            user_id=user_id,
            workspace_id=workspace_id,
            thread_id=shared_tid,
            status="approved",
            decided_at=now - timedelta(hours=48),
        )
        # write#2 on the SAME thread: still pending (no decided_at) — the protector.
        await _seed_approval(
            factory,
            user_id=user_id,
            workspace_id=workspace_id,
            thread_id=shared_tid,
            status="pending",
            decided_at=None,
        )

        fake = _FakeSaver()
        reaped = await sweep_decided_approval_checkpoints(fake, factory, retention_hours=24)

        assert shared_tid not in set(fake.deleted), (
            "a thread with a still-PENDING approval must NOT be reaped, even when a sibling "
            "approval on the same thread is decided-and-old (per-THREAD guard)"
        )
        assert reaped == 0, f"expected zero reaped threads (pending sibling protects), got {reaped}"


# ── real-saver end-to-end: scripted streaming model + wired AgentInvoker ──────────


class _ScriptedModel(BaseChatModel):
    """Fake streaming model: turn 1 calls ``send_email``, a follow-up turn answers."""

    @property
    def _llm_type(self) -> str:
        return "scripted-fake"

    def bind_tools(self, tools, **kwargs):  # noqa: ANN001, ANN003, ARG002
        return self

    def _script(self, messages):  # noqa: ANN001
        if any(isinstance(m, ToolMessage) for m in messages):
            return [AIMessageChunk(content=[{"type": "text", "text": "All done.", "index": 0}])]
        return [
            AIMessageChunk(
                content=[],
                tool_call_chunks=[
                    tool_call_chunk(
                        name="send_email",
                        args=json.dumps(TOOL_ARGS),
                        id="call_send_1",
                        index=0,
                    )
                ],
            )
        ]

    async def _astream(self, messages, stop=None, run_manager=None, **kwargs):  # noqa: ANN001, ANN003
        for ch in self._script(messages):
            yield ChatGenerationChunk(message=ch)

    async def _agenerate(self, messages, stop=None, run_manager=None, **kwargs):  # noqa: ANN001, ANN003
        merged = None
        async for gen in self._astream(messages):
            merged = gen.message if merged is None else merged + gen.message
        assert merged is not None
        msg = AIMessage(content=merged.content, tool_calls=list(merged.tool_calls))
        return ChatResult(generations=[ChatGeneration(message=msg)])

    def _generate(self, *a, **k):  # noqa: ANN002, ANN003
        raise NotImplementedError


def _make_invoker(*, factory, checkpointer, executed: list) -> AgentInvoker:
    """Build a real ``AgentInvoker`` wired for a real DB + fake model + fake dispatch.

    ``client=MagicMock()`` fails closed to ``risk_level="high"`` when awaited; combined with
    ``email.send`` being statically IRREVERSIBLE this forces approval on the gated path.
    """
    tool_executor = MagicMock()
    tool_executor.apply_cache_control_to_tools = lambda tools: tools
    tool_executor.get_tools_for_agent = AsyncMock(return_value=[TOOL_DEF])

    async def fake_execute(name, args, uid, ws):
        executed.append((name, args))
        return {"ok": True}

    tool_executor.execute_tool = fake_execute

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
        checkpointer_provider=lambda: checkpointer,
    )


# ── (3) REAL-SAVER: primary reaper on completion + pause-survives + negative control ──


@requires_db
async def test_non_paused_turn_reaps_its_checkpoints():
    """A direct (ungated) chat turn runs the write inline, completes, and — because it did
    NOT pause — its durable checkpoints are reaped: ``checkpoints`` count → 0."""
    async with _gate_env() as (factory, user_id, workspace_id):
        executed: list = []
        thread_id = f"chat_{ULID()}"
        saver, pool = await build_async_postgres_saver(get_settings().database_url)
        invoker = _make_invoker(factory=factory, checkpointer=saver, executed=executed)
        try:
            with (
                patch(f"{AGENT_INVOKER_MODULE}.generate_id", return_value=thread_id),
                patch(f"{AGENT_BUILDER_MODULE}.build_chat_model", return_value=_ScriptedModel()),
                patch(f"{CAP_SCOPE_MODULE}._is_in_scope", AsyncMock(return_value=True)),
                patch(
                    # 6C #1 fold: steer resolution at the deepest boundary (the SHARED
                    # _resolve_tool_def's ToolRegistry.get_tool) — write cap, enabled, high risk.
                    f"{TRUST_GATE_MODULE}.ToolRegistry",
                    return_value=SimpleNamespace(
                        get_tool=AsyncMock(
                            return_value=SimpleNamespace(
                                capability="email.send", enabled=True, risk_level="high"
                            )
                        )
                    ),
                ),
            ):
                frames = [
                    f
                    async for f in invoker.call_agent_stream(
                        "executor", "go", user_id, workspace_id=workspace_id
                    )
                ]

            assert not any(f.get("event") == "approval_needed" for f in frames), (
                f"a direct turn must not pause; frames={frames}"
            )
            assert any(f.get("event") == "agent_done" for f in frames), f"frames={frames}"
            assert executed == [("send_email", TOOL_ARGS)], f"executed={executed}"

            assert await _count_checkpoints(thread_id) == 0, (
                "a completed non-paused turn's checkpoints must be reaped"
            )
        finally:
            await pool.close()
            await _delete_checkpoint_rows(thread_id)


@requires_db
async def test_negative_control_without_reap_checkpoints_remain():
    """NEGATIVE CONTROL: with the reap call patched OUT, the same completed non-paused turn
    leaves its checkpoint rows behind (count > 0) — proving the reap in the prior test is what
    empties the table, not some incidental cleanup."""
    async with _gate_env() as (factory, user_id, workspace_id):
        executed: list = []
        thread_id = f"chat_{ULID()}"
        saver, pool = await build_async_postgres_saver(get_settings().database_url)
        invoker = _make_invoker(factory=factory, checkpointer=saver, executed=executed)
        try:
            with (
                patch(f"{AGENT_INVOKER_MODULE}.generate_id", return_value=thread_id),
                # THE BYPASS: neutralize the reap so the checkpoints are NOT deleted on completion.
                patch(f"{AGENT_INVOKER_MODULE}.reap_thread", AsyncMock(return_value=False)),
                patch(f"{AGENT_BUILDER_MODULE}.build_chat_model", return_value=_ScriptedModel()),
                patch(f"{CAP_SCOPE_MODULE}._is_in_scope", AsyncMock(return_value=True)),
                patch(
                    # 6C #1 fold: steer resolution at the deepest boundary (the SHARED
                    # _resolve_tool_def's ToolRegistry.get_tool) — write cap, enabled, high risk.
                    f"{TRUST_GATE_MODULE}.ToolRegistry",
                    return_value=SimpleNamespace(
                        get_tool=AsyncMock(
                            return_value=SimpleNamespace(
                                capability="email.send", enabled=True, risk_level="high"
                            )
                        )
                    ),
                ),
            ):
                frames = [
                    f
                    async for f in invoker.call_agent_stream(
                        "executor", "go", user_id, workspace_id=workspace_id
                    )
                ]

            assert any(f.get("event") == "agent_done" for f in frames), f"frames={frames}"
            remaining = await _count_checkpoints(thread_id)
            assert remaining > 0, (
                "without the reap call a completed turn's checkpoint rows must REMAIN "
                f"(negative control); count={remaining}"
            )
        finally:
            await pool.close()
            await _delete_checkpoint_rows(thread_id)


@requires_db
async def test_paused_turn_checkpoint_survives():
    """A turn that PAUSES on the approval gate must KEEP its checkpoint (else resume breaks).
    Forcing ``is_gated_source`` True makes even the direct seam pause; the reaper must NOT fire
    on that turn, so the ``checkpoints`` rows survive (count > 0)."""
    async with _gate_env() as (factory, user_id, workspace_id):
        executed: list = []
        saver, pool = await build_async_postgres_saver(get_settings().database_url)
        invoker = _make_invoker(factory=factory, checkpointer=saver, executed=executed)
        thread_id = None
        try:
            with (
                patch(f"{AGENT_BUILDER_MODULE}.build_chat_model", return_value=_ScriptedModel()),
                patch(f"{CAP_SCOPE_MODULE}._is_in_scope", AsyncMock(return_value=True)),
                patch(
                    # 6C #1 fold: steer resolution at the deepest boundary (the SHARED
                    # _resolve_tool_def's ToolRegistry.get_tool) — write cap, enabled, high risk.
                    f"{TRUST_GATE_MODULE}.ToolRegistry",
                    return_value=SimpleNamespace(
                        get_tool=AsyncMock(
                            return_value=SimpleNamespace(
                                capability="email.send", enabled=True, risk_level="high"
                            )
                        )
                    ),
                ),
                # Force the gate to treat even direct provenance as gated → the turn pauses.
                patch(f"{TRUST_GATE_MODULE}.is_gated_source", return_value=True),
            ):
                frames = [
                    f
                    async for f in invoker.call_agent_stream(
                        "executor", "go", user_id, workspace_id=workspace_id
                    )
                ]

            approval_frames = [f for f in frames if f.get("event") == "approval_needed"]
            assert len(approval_frames) == 1, f"the turn must pause; frames={frames}"
            assert executed == [], "the tool must NOT run while the turn is paused"
            thread_id = approval_frames[0]["thread_id"]
            assert thread_id, f"the pause frame must carry a thread_id; frame={approval_frames[0]}"

            assert await _count_checkpoints(thread_id) > 0, (
                "a paused turn's checkpoint must SURVIVE so the resume path can recover it"
            )
        finally:
            await pool.close()
            if thread_id:
                await _delete_checkpoint_rows(thread_id)
