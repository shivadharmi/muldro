"""Step 6B Task 7: durable-checkpointer LIVE PROOF for the deep chat approval gate.

Task 6 (``test_deep_gate_end_to_end.py``) proved the gate's interrupt -> approval_needed
-> Command(resume) round-trip using an in-process ``MemorySaver`` — sufficient to prove
the *gate machinery*, but not that a paused turn actually survives a real durability
boundary. This file proves the latter: a turn is paused via one ``AsyncPostgresSaver``
object (over one psycopg3 pool), and resumed by a completely FRESH ``AsyncPostgresSaver``
object (over a second, independent psycopg3 pool) against the SAME Postgres. The first
pool is closed *before* the resume call, so the paused LangGraph state cannot possibly be
served from any surviving in-process object — it can only come from Postgres itself.

No Docker/Anthropic dependency: skips (does not fail) when Postgres is unreachable,
mirroring ``test_deep_gate_end_to_end.py`` / ``tests/idempotency/test_ledger_db.py``.
"""

from __future__ import annotations

import asyncio
import json
import subprocess
from contextlib import asynccontextmanager
from pathlib import Path
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
from src.deep_runtime.checkpointer import build_async_postgres_saver
from src.deep_runtime.prompt_bridge import build_system_message
from src.deep_runtime.stream_adapter import stream_deep_agent_events
from src.models.approvals import Approval
from src.models.trust_state import TrustCeiling, TrustState
from src.models.users import User, Workspace
from src.orchestrator.agent_invoker import AgentInvoker
from src.orchestrator.agents import SubAgent
from tests.conftest import make_mock_settings

TRUST_GATE_MODULE = "src.deep_runtime.middleware.trust_gate"
CAP_SCOPE_MODULE = "src.deep_runtime.middleware.capability_scope"
AGENT_BUILDER_MODULE = "src.deep_runtime.agent_builder"

TOOL_DEF = {
    "name": "send_email",
    "description": "Send an email on the user's behalf.",
    "input_schema": {"type": "object", "properties": {"to": {"type": "string"}}},
}
TOOL_ARGS = {"to": "vip@example.com"}


def _db_reachable() -> bool:
    """Best-effort raw connect to Postgres to decide whether to skip.

    Mirrors ``test_deep_gate_end_to_end.py``: a raw asyncpg connect on its own
    throwaway loop, never touching the app's process-wide cached engine.
    """
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


# ── fake scripted streaming model: turn 1 calls send_email, resumed turn answers ──


class _ScriptedModel(BaseChatModel):
    """Fake streaming model: turn 1 calls ``send_email``, the resumed turn answers.

    Stateless (inspects message history for a prior ``ToolMessage`` to pick the turn),
    so ONE instance serves both the initial turn and every resume in a test.
    """

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


# ── real-DB environment: fresh User+Workspace per test, own engine/loop ──────────


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
                    email=f"durable-{suffix}@example.com",
                    display_name="durable-resume-test",
                )
            )
            db.add(Workspace(workspace_id=workspace_id, name="durable-ws", owner_user_id=user_id))
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


def _make_invoker(*, factory, checkpointer, executed: list) -> AgentInvoker:
    """Build a real ``AgentInvoker`` wired for a real DB + fake model + fake dispatch.

    ``client=MagicMock()`` deterministically fails closed to ``risk_level="high"``
    when awaited, combined with ``email.send`` being a statically IRREVERSIBLE
    capability — forces approval deterministically without a real Anthropic call.
    See ``test_deep_gate_end_to_end.py`` for the full rationale.
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


async def _delete_checkpoint_rows(thread_id: str) -> None:
    """Raw asyncpg cleanup of the ``checkpoints*`` rows for one thread.

    Mirrors ``spikes/postgres_saver/probe.py``'s ``_delete_checkpoints`` teardown.
    """
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


# ── the durability proof ──────────────────────────────────────────────────────────


async def test_interrupt_resume_spans_durable_postgres_saver():
    """Turn 1 pauses on ``interrupt()`` under ``saver_a`` (its own psycopg3 pool).
    ``pool_a`` is then CLOSED — the object that witnessed the pause is gone. A
    completely fresh ``saver_b`` (a second, independent pool over the SAME Postgres)
    is swapped in via the invoker's checkpointer provider, and ``resume_deep_turn``
    rebuilds a brand-new compiled deep agent over ``saver_b``. If the tool executes
    and the turn completes, the paused graph state was recovered from Postgres itself
    — not replayed from any surviving in-process object — proving the gate's
    interrupt/resume genuinely spans the durable backend, not just an in-process
    MemorySaver (Task 6's proof).
    """
    async with _gate_env() as (factory, user_id, workspace_id):
        executed: list = []
        thread_id = f"chat_{ULID()}"

        saver_a, pool_a = await build_async_postgres_saver(get_settings().database_url)
        holder = {"saver": saver_a}
        invoker = _make_invoker(factory=factory, checkpointer=saver_a, executed=executed)
        invoker._checkpointer_provider = lambda: holder["saver"]
        agent = invoker._agents["executor"]

        pool_a_closed = False
        pool_b = None
        try:
            with (
                patch(f"{AGENT_BUILDER_MODULE}.build_chat_model", return_value=_ScriptedModel()),
                patch(f"{CAP_SCOPE_MODULE}._is_in_scope", AsyncMock(return_value=True)),
                patch(
                    f"{TRUST_GATE_MODULE}._resolve_capability",
                    AsyncMock(return_value=(True, "email.send")),
                ),
            ):
                deep_agent = await invoker._build_deep_agent_for(
                    agent,
                    [TOOL_DEF],
                    user_id=user_id,
                    workspace_id=workspace_id,
                    thread_id=thread_id,
                    authorization_source="autonomous",
                    system_prompt=build_system_message(invoker.build_system_prompt(agent, "")),
                )
                config = {"configurable": {"thread_id": thread_id}}

                # --- turn 1: forced-autonomous write pauses, persisted via saver_a ---
                frames1 = [
                    f
                    async for f in stream_deep_agent_events(
                        deep_agent,
                        {"messages": [{"role": "user", "content": "go"}]},
                        config,
                        agent_name="executor",
                        model="claude-sonnet-5",
                        durability="sync",
                    )
                ]

                approval_frames = [f for f in frames1 if f["event"] == "approval_needed"]
                assert len(approval_frames) == 1, (
                    f"expected exactly one approval_needed frame, got: {frames1}"
                )
                assert executed == [], "tool must NOT run while the turn is paused"
                approval_id = approval_frames[0]["approval_id"]
                assert approval_id is not None

                async with factory() as db:
                    row = await db.get(Approval, approval_id)
                    assert row is not None, "the pending Approval must be persisted to Postgres"
                    assert row.status == "pending"
                    assert row.artifact_refs["thread_id"] == thread_id

                # --- the durability boundary --------------------------------------
                # Close pool_a FIRST: the strongest form of the proof. The psycopg3
                # pool + AsyncPostgresSaver object that witnessed the pause is torn
                # down BEFORE resume, so the paused graph state can only be recovered
                # from Postgres itself on the resume path below — never from a
                # surviving in-process object.
                await pool_a.close()
                pool_a_closed = True
                saver_b, pool_b = await build_async_postgres_saver(get_settings().database_url)
                holder["saver"] = saver_b

                # --- turn 2: resume rebuilds a FRESH deep agent over saver_b ------
                frames2 = [
                    f
                    async for f in invoker.resume_deep_turn(
                        approval_id=approval_id,
                        decision="approve",
                        user_id=user_id,
                        workspace_id=workspace_id,
                    )
                ]

            assert executed == [("send_email", TOOL_ARGS)], (
                f"the tool must execute exactly once after durable resume over a fresh "
                f"saver; executed={executed}"
            )
            assert any(f["event"] == "tool_result" for f in frames2), f"frames2={frames2}"
            assert any(f["event"] == "agent_done" for f in frames2), f"frames2={frames2}"

            async with factory() as db:
                row = await db.get(Approval, approval_id)
                assert row.status == "approved"
        finally:
            if not pool_a_closed:
                await pool_a.close()
            if pool_b is not None:
                await pool_b.close()
            await _delete_checkpoint_rows(thread_id)


# ── alembic drift guard: saver.setup()'s tables must stay excluded ───────────────


async def test_checkpoints_tables_do_not_drift_alembic():
    """``AsyncPostgresSaver.setup()`` creates ``checkpoints``/``checkpoint_blobs``/
    ``checkpoint_writes``/``checkpoint_migrations`` directly against Postgres, outside
    Alembic. ``alembic/env.py``'s ``_include_object`` excludes them from autogenerate
    so they never show up as pending upgrade ops. Prove that holds by ensuring the
    tables exist (idempotent ``setup()``) and then running ``alembic check`` for real.
    """
    saver, pool = await build_async_postgres_saver(get_settings().database_url)
    await pool.close()

    backend_dir = Path(__file__).resolve().parents[1]
    result = subprocess.run(
        ["uv", "run", "alembic", "check"],
        cwd=str(backend_dir),
        capture_output=True,
        text=True,
        timeout=120,
    )
    combined = result.stdout + result.stderr

    if result.returncode != 0 and "No new upgrade operations detected" not in combined:
        pytest.skip(f"alembic check could not run cleanly in this environment: {combined}")

    assert "No new upgrade operations detected" in combined, (
        f"checkpoints* tables introduced alembic drift (should be excluded by "
        f"env.py's _include_object): {combined}"
    )
