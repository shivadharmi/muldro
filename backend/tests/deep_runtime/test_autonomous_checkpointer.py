"""Step 10C P2: durable ``AsyncPostgresSaver`` on the autonomous deep-step seam.

P1a wired ``AgentInvoker.run_autonomous_deep_step`` to build/stream through a
``checkpointer_provider`` (falling back to ``MemorySaver`` when none is injected). P2
wires a REAL worker-side ``AsyncPostgresSaver`` into that provider. These tests inject a
real saver via ``checkpointer_provider=lambda: <saver>`` and prove, against real Postgres:

1. **Durability persists to Postgres** — after a ``run_autonomous_deep_step`` for a write,
   a checkpoint row EXISTS for the run's ws-bound ``thread_id`` and the blobs are non-pickle
   (``type='msgpack'``, never a pickle ``b'\\x80'`` stream).
2. **ws-bound thread_id (A6)** — the minted ``thread_id`` satisfies
   ``workspace_of_thread_id(thread_id) == run.workspace_id``.
3. **Same-thread resume (checkpointer primitive)** — a deep agent built via the invoker's
   REAL ``_build_deep_agent_for`` with a FIXED ``thread_id`` + the real saver + a
   ledger-guarded write, killed mid-tool-call, RESUMES on the same thread via
   ``ainvoke(None, cfg, durability="sync")`` and the external effect fires EXACTLY ONCE.
4. **A6 refusal** — an empty ``workspace_id`` (LOW-1 fail-closed) AND a cross-ws mismatched
   ``thread_id`` are both refused with the error dict; the deep agent is NEVER built.

Guarded (skip when Postgres is unreachable), NullPool, seeded User→Workspace FK chain,
UUID/ULID-suffixed ids, teardown cascades tool defs + idempotency_ledger via the workspace
FK ``ON DELETE CASCADE`` and deletes the thread's checkpoint rows. Reuses P1a's ``_FakeModel``
+ the ``build_chat_model`` monkeypatch to drive the deep agent offline.
"""

from __future__ import annotations

import asyncio
import json
import pickle
from collections.abc import AsyncIterator
from contextlib import asynccontextmanager
from typing import Any
from unittest.mock import AsyncMock, MagicMock, patch

import asyncpg
import pytest
from langchain_core.callbacks import AsyncCallbackManagerForLLMRun
from langchain_core.language_models.chat_models import BaseChatModel
from langchain_core.messages import AIMessage, AIMessageChunk, ToolMessage
from langchain_core.messages.tool import tool_call_chunk
from langchain_core.outputs import ChatGeneration, ChatGenerationChunk, ChatResult

from src.deep_runtime.authorization import AuthorizationSource
from src.deep_runtime.thread_identity import make_thread_id, workspace_of_thread_id
from src.orchestrator.agent_invoker import AgentInvoker
from src.orchestrator.agents import SubAgent, ThinkingConfig
from src.services.idempotency import (
    IdempotencyContext,
    IdempotencyLedger,
    make_idempotent_execute_tool_fn,
)
from tests.conftest import make_mock_settings

INVOKER_MODULE = "src.orchestrator.agent_invoker"
BUILD_CHAT_MODEL = "src.deep_runtime.agent_builder.build_chat_model"
WRITE_TOOL = "send_email"


# ─────────────────────────── shared test doubles ────────────────────────────


class _FakeModel(BaseChatModel):
    """Deterministic no-API model: turn 1 emits a tool_call to ``tool_name``; the
    resumed turn (after it sees a ToolMessage) emits a final ``done`` text chunk.
    Copied from ``test_autonomous_deep_step_build.py`` (P1a)."""

    def __init__(self, tool_name: str, args: dict, **kw: Any) -> None:
        super().__init__(**kw)
        object.__setattr__(self, "_tool_name", tool_name)
        object.__setattr__(self, "_args", args)

    @property
    def _llm_type(self) -> str:
        return "fake"

    def bind_tools(self, tools, **kwargs):  # noqa: ARG002
        return self

    def _script(self, messages):
        if any(isinstance(m, ToolMessage) for m in messages):
            return [AIMessageChunk(content=[{"type": "text", "text": "done", "index": 0}])]
        return [
            AIMessageChunk(
                content=[],
                tool_call_chunks=[
                    tool_call_chunk(
                        name=self._tool_name,
                        args=json.dumps(self._args),
                        id="call_step_1",
                        index=0,
                    )
                ],
            )
        ]

    async def _astream(
        self, messages, stop=None, run_manager=None, **kwargs
    ) -> AsyncIterator[ChatGenerationChunk]:
        for ch in self._script(messages):
            yield ChatGenerationChunk(message=ch)

    async def _agenerate(
        self,
        messages,
        stop=None,
        run_manager: AsyncCallbackManagerForLLMRun | None = None,
        **kwargs,
    ) -> ChatResult:
        merged: AIMessageChunk | None = None
        async for gen in self._astream(messages):
            merged = gen.message if merged is None else merged + gen.message
        assert merged is not None
        msg = AIMessage(content=merged.content, tool_calls=list(merged.tool_calls))
        return ChatResult(generations=[ChatGeneration(message=msg)])

    def _generate(self, *a: Any, **k: Any) -> ChatResult:
        raise NotImplementedError


def _executor_agent() -> SubAgent:
    return SubAgent(
        name="executor",
        prompt="exec",
        model_tier="haiku",
        capability_scope={"email.send", "email.read"},
        thinking=ThinkingConfig(enabled=False),
    )


def _write_tool_shell() -> dict:
    return {
        "name": WRITE_TOOL,
        "description": "send an email",
        "input_schema": {
            "type": "object",
            "properties": {"to": {"type": "string"}, "subject": {"type": "string"}},
        },
    }


# ─────────────────────────── real-Postgres harness ──────────────────────────


def _db_reachable() -> bool:
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


_DB_OK = _db_reachable()


def _psycopg_dsn() -> str:
    from src.config.settings import get_settings

    return get_settings().database_url.replace("+asyncpg", "", 1)


def _sqla_dsn() -> str:
    """The SQLAlchemy async DSN the durable saver builder accepts (it strips +asyncpg)."""
    from src.config.settings import get_settings

    return get_settings().database_url


@asynccontextmanager
async def _db_env():
    """Real-DB env: NullPool engine + User→Workspace seed. Yields
    ``(factory, workspace_id, user_id, threads)`` where ``threads`` is a set the test
    populates with checkpoint thread_ids; teardown deletes those checkpoint rows and
    cascades tool defs + idempotency_ledger via the workspace FK ON DELETE CASCADE."""
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
    threads: set[str] = set()
    try:
        async with factory() as db:
            db.add(User(user_id=user_id, email=f"10c-{suffix}@example.com", display_name="10c"))
            db.add(Workspace(workspace_id=workspace_id, name="10c-ws", owner_user_id=user_id))
            await db.commit()
        yield factory, workspace_id, user_id, threads
    finally:
        for tid in threads:
            await _delete_checkpoints(tid)
        try:
            async with factory() as db:
                await db.execute(delete(Workspace).where(Workspace.workspace_id == workspace_id))
                await db.execute(delete(User).where(User.user_id == user_id))
                await db.commit()
        except Exception:  # pragma: no cover
            pass
        await engine.dispose()


async def _seed_tool(factory, workspace_id, *, name, capability, requires_approval):
    from ulid import ULID

    from src.models.tool_definitions import ToolBackend, ToolDefinition

    async with factory() as db:
        db.add(
            ToolDefinition(
                tool_id=f"tool_{ULID()}",
                workspace_id=workspace_id,
                name=name,
                description=f"seed {name}",
                capability=capability,
                requires_approval=requires_approval,
                backend=ToolBackend.INTERNAL_MCP,
                enabled=True,
            )
        )
        await db.commit()


def _make_invoker(factory, *, checkpointer, effects: list[str]) -> AgentInvoker:
    """AgentInvoker wired to the real ``factory`` + a real (injected) checkpointer and a
    fake tool executor that records each external effect into ``effects``."""

    class _FakeExecutor:
        async def execute_tool(self, name, args, user_id, workspace_id):  # noqa: ARG002
            effects.append(name)
            return {"status": "sent", "tool": name}

    return AgentInvoker(
        settings=make_mock_settings(runtime="deep"),
        client=MagicMock(),
        services=None,
        budget=MagicMock(),
        circuit_breaker=MagicMock(),
        db_factory_provider=lambda: factory,
        tool_executor=_FakeExecutor(),
        context=MagicMock(),
        agents={"executor": _executor_agent()},
        checkpointer_provider=lambda: checkpointer,
    )


async def _checkpoint_rows(thread_id: str) -> int:
    conn = await asyncpg.connect(dsn=_psycopg_dsn())
    try:
        return await conn.fetchval(
            "SELECT count(*) FROM checkpoints WHERE thread_id = $1", thread_id
        )
    finally:
        await conn.close()


async def _checkpoints_not_pickle(thread_id: str) -> bool:
    """Read the raw checkpoint blobs and prove none is a pickle stream (a pickle payload
    starts with the PROTO opcode ``b'\\x80'``). Returns False when there are no rows."""
    conn = await asyncpg.connect(dsn=_psycopg_dsn())
    try:
        rows = await conn.fetch(
            "SELECT type, blob FROM checkpoint_blobs WHERE thread_id = $1", thread_id
        )
        if not rows:
            return False
        pickle_proto = pickle.PROTO  # b'\x80'
        any_pickle = any(r["blob"] and bytes(r["blob"][:1]) == pickle_proto for r in rows)
        return not any_pickle
    finally:
        await conn.close()


async def _blob_types(thread_id: str) -> set[str]:
    conn = await asyncpg.connect(dsn=_psycopg_dsn())
    try:
        rows = await conn.fetch("SELECT type FROM checkpoint_blobs WHERE thread_id = $1", thread_id)
        return {r["type"] for r in rows}
    finally:
        await conn.close()


async def _delete_checkpoints(thread_id: str) -> None:
    conn = await asyncpg.connect(dsn=_psycopg_dsn())
    try:
        for tbl in ("checkpoint_blobs", "checkpoint_writes", "checkpoints"):
            try:
                await conn.execute(f"DELETE FROM {tbl} WHERE thread_id = $1", thread_id)
            except Exception:  # noqa: BLE001 - table may not exist
                pass
    finally:
        await conn.close()


def _capture_thread_id(store: dict) -> Any:
    """A ``make_thread_id`` spy: mints the REAL ws-bound thread_id, records it, returns it.
    Patched into the invoker module so the seam mints a genuinely ws-bound thread AND the
    test can read the exact value back (Test 1/2 need it to query Postgres)."""

    def _spy(workspace_id: str) -> str:
        tid = make_thread_id(workspace_id)
        store["thread_id"] = tid
        return tid

    return _spy


# ═══════════ Test 1 + 2 — durable persist + ws-bound thread (real saver) ═══════════


@pytest.mark.skipif(not _DB_OK, reason="Postgres not reachable")
async def test_autonomous_step_persists_checkpoint_to_postgres():
    """A ``run_autonomous_deep_step`` write, driven through a REAL injected
    ``AsyncPostgresSaver``, leaves a checkpoint row in Postgres for the run's ws-bound
    ``thread_id`` (Test 1), the persisted blobs are non-pickle msgpack (Test 1), and the
    ``thread_id`` embeds the run's workspace (Test 2, A6)."""
    from src.deep_runtime.checkpointer import build_async_postgres_saver

    async with _db_env() as (factory, ws, uid, threads):
        await _seed_tool(
            factory, ws, name=WRITE_TOOL, capability="email.send", requires_approval=True
        )
        effects: list[str] = []
        saver = None
        pool = None
        captured: dict[str, str] = {}
        try:
            saver, pool = await build_async_postgres_saver(_sqla_dsn())
            inv = _make_invoker(factory, checkpointer=saver, effects=effects)

            with (
                patch(BUILD_CHAT_MODEL, lambda _a: _FakeModel(WRITE_TOOL, {"to": "f@x.com"})),
                patch(f"{INVOKER_MODULE}.make_thread_id", _capture_thread_id(captured)),
                # Step 10C P5: run_autonomous_deep_step now reaps its per-step thread on
                # completion (reap-on-completion). That would delete the very checkpoint this
                # DURABILITY test asserts persisted, so we no-op the reap for THIS test only —
                # the durability claim is unchanged (durability="sync" still wrote a real
                # Postgres checkpoint); we only stop the new reap from deleting the evidence.
                # Reap-on-completion itself is proved in test_autonomous_reaper.py.
                patch(f"{INVOKER_MODULE}.reap_thread", AsyncMock(return_value=False)),
            ):
                out = await inv.run_autonomous_deep_step(
                    executor=_executor_agent(),
                    tools=[_write_tool_shell()],
                    message="send it",
                    context_block="",
                    user_id=uid,
                    workspace_id=ws,
                    run_id="run_persist",
                    step_id="s1",
                    pre_approved_capabilities=frozenset({"email.send"}),
                )

            assert out["status"] == "completed"
            assert effects == [WRITE_TOOL]  # the write actually executed once

            thread_id = captured["thread_id"]
            threads.add(thread_id)

            # Test 2 (A6): the minted thread embeds the run's workspace.
            assert workspace_of_thread_id(thread_id) == ws

            # Test 1: a checkpoint row persisted to Postgres for this thread ...
            assert await _checkpoint_rows(thread_id) >= 1
            # ... and the blobs are non-pickle msgpack (never a pickle b'\x80' stream).
            assert await _checkpoints_not_pickle(thread_id) is True
            assert "msgpack" in await _blob_types(thread_id)
        finally:
            if pool is not None:
                await pool.close()


# ═══════════ Test 3 — same-thread resume continues (real saver, real build) ═══════════


@pytest.mark.skipif(not _DB_OK, reason="Postgres not reachable")
async def test_same_thread_resume_fires_write_exactly_once():
    """The checkpointer resume primitive through the invoker's REAL ``_build_deep_agent_for``:
    build a deep agent with a FIXED ``thread_id`` + the real saver + a ledger-guarded write,
    invoke pass 1 that CRASHES mid-tool-call (after the effect + ledger record, before the
    tool-node checkpoint), then ``ainvoke(None, cfg, durability='sync')`` on the SAME thread
    resumes and the external effect fired EXACTLY ONCE (the ledger dedups the mandatory
    at-least-once replay)."""
    from src.deep_runtime.checkpointer import build_async_postgres_saver

    async with _db_env() as (factory, ws, uid, threads):
        await _seed_tool(
            factory, ws, name=WRITE_TOOL, capability="email.send", requires_approval=True
        )
        run_id = "run_resume"
        thread_id = make_thread_id(ws)
        threads.add(thread_id)
        effects: list[str] = []
        crashed: list[int] = []

        def _crash_ledger_execute_tool():
            """A ledger-wrapped ``execute_tool`` that fires the effect + records the ledger,
            then raises ONCE on the write (one-shot ``crashed`` sentinel) to model a process
            kill after the record but before the tool-node checkpoint commits. Built FRESH per
            pass (fresh ledger + ordinal counter) so it faithfully models a restart."""

            async def _effect(name, args, *, user_id, workspace_id):  # noqa: ARG001
                effects.append(name)
                return {"status": "sent", "tool": name}

            idem_fn = make_idempotent_execute_tool_fn(
                _effect,
                IdempotencyContext(
                    ledger=IdempotencyLedger(factory),
                    run_id=run_id,
                    step_id="s1",
                    workspace_id=ws,
                    db_factory=factory,
                ),
            )

            async def _adapter(name, args, user_id, workspace_id):  # dispatcher positional contract
                result = await idem_fn(name, args, user_id=user_id, workspace_id=workspace_id)
                if name == WRITE_TOOL and not crashed:
                    crashed.append(1)
                    raise RuntimeError("simulated process kill after effect+record")
                return result

            return _adapter

        cfg = {"configurable": {"thread_id": thread_id}}
        saver = None
        pool = None
        try:
            saver, pool = await build_async_postgres_saver(_sqla_dsn())
            inv = _make_invoker(factory, checkpointer=saver, effects=effects)

            async def _build():
                return await inv._build_deep_agent_for(
                    _executor_agent(),
                    [_write_tool_shell()],
                    user_id=uid,
                    workspace_id=ws,
                    thread_id=thread_id,
                    authorization_source=AuthorizationSource.AUTONOMOUS,
                    system_prompt="exec",
                    execute_tool=_crash_ledger_execute_tool(),
                    pre_approved_capabilities=frozenset({"email.send"}),
                )

            with patch(BUILD_CHAT_MODEL, lambda _a: _FakeModel(WRITE_TOOL, {"to": "f@x.com"})):
                # Pass 1: real deep agent, expect the mid-tool-call crash.
                agent1 = await _build()
                with pytest.raises(RuntimeError, match="simulated process kill"):
                    await agent1.ainvoke(
                        {"messages": [{"role": "user", "content": "send it"}]},
                        cfg,
                        durability="sync",
                    )

                # Pass 2: FRESH agent (simulated restart), resume the SAME thread_id.
                agent2 = await _build()
                result = await agent2.ainvoke(None, cfg, durability="sync")

            assert isinstance(result, dict)
            # The external effect fired EXACTLY ONCE across the crash + resume (ledger dedup).
            assert effects == [WRITE_TOOL], f"double-fire: {effects}"
            assert crashed == [1]  # the crash fired exactly once (pass 1 only)

            from sqlalchemy import select

            from src.models.idempotency_ledger import IdempotencyLedgerEntry

            async with factory() as db:
                rows = (
                    (
                        await db.execute(
                            select(IdempotencyLedgerEntry).where(
                                IdempotencyLedgerEntry.workspace_id == ws
                            )
                        )
                    )
                    .scalars()
                    .all()
                )
            assert len(rows) == 1
            assert rows[0].status == "completed"
        finally:
            if pool is not None:
                await pool.close()


# ═══════════════════ Test 4 — A6 refusal (empty ws + cross-ws) ═══════════════════


async def test_run_autonomous_deep_step_refuses_empty_workspace():
    """LOW-1 fail-closed: an empty ``workspace_id`` is refused BEFORE any deep build — the
    A6 round-trip degenerates to ``"" == ""`` for an empty tenant, so this explicit check is
    the tenant fail-closed CLAUDE.md requires. No Postgres needed (refused up front)."""
    inv = _make_invoker(MagicMock(), checkpointer=None, effects=[])
    inv._build_deep_agent_for = AsyncMock(return_value=object())

    out = await inv.run_autonomous_deep_step(
        executor=_executor_agent(),
        tools=[_write_tool_shell()],
        message="send it",
        context_block="",
        user_id="u",
        workspace_id="",  # empty tenant → refused before any build/write
        run_id="run_1",
        step_id="s1",
        pre_approved_capabilities=frozenset({"email.send"}),
    )

    assert out["status"] == "error"
    assert out["errors"] == ["missing workspace_id"]
    inv._build_deep_agent_for.assert_not_awaited()


async def test_run_autonomous_deep_step_refuses_cross_ws_thread():
    """A6 guard with teeth: patch ``make_thread_id`` to mint a thread embedding a DIFFERENT
    workspace → ``workspace_of_thread_id(thread_id) != workspace_id`` → the step is refused
    with the ws-mismatch error dict and the deep agent is NEVER built. Proves the guard is a
    real tenant fence, not a self-satisfying no-op (the seam normally mints its own ws thread,
    which always matches)."""

    def _cross_ws_make_thread_id(_workspace_id: str) -> str:
        return make_thread_id("ws_someone_else")  # embeds a DIFFERENT tenant

    inv = _make_invoker(MagicMock(), checkpointer=None, effects=[])
    inv._build_deep_agent_for = AsyncMock(return_value=object())

    async def _done_stream(*a, **k):
        yield {"event": "agent_done", "agent": "executor", "text": "done", "tools_called": []}

    with (
        patch(f"{INVOKER_MODULE}.make_thread_id", _cross_ws_make_thread_id),
        patch(f"{INVOKER_MODULE}.stream_deep_agent_events", _done_stream),
    ):
        out = await inv.run_autonomous_deep_step(
            executor=_executor_agent(),
            tools=[_write_tool_shell()],
            message="send it",
            context_block="",
            user_id="u",
            workspace_id="ws_mine",  # != the cross-ws thread the patched minter returns
            run_id="run_1",
            step_id="s1",
            pre_approved_capabilities=frozenset({"email.send"}),
        )

    assert out["status"] == "error"
    assert out["errors"] == ["workspace thread mismatch"]
    inv._build_deep_agent_for.assert_not_awaited()  # refused before any deep build
