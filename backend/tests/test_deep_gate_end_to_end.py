"""Step 6B Task 6: LOAD-BEARING end-to-end proof of the deep chat approval gate.

Drives the WHOLE machinery — capability_scope -> trust_gate -> jarvis_tool_dispatcher,
``AgentInvoker._build_deep_agent_for`` / ``resume_deep_turn``, ``TrustEngine``,
``create_approval`` — against a REAL Postgres DB and a fake scripted streaming model
(no Anthropic API). ``authorization_source`` is FORCED to ``"autonomous"``, provenance
that never occurs on live chat (the seam always passes ``direct_user_request`` — see
``AgentInvoker.call_agent_stream``); this is the only way to exercise the gate's
interrupt/resume path end-to-end since it is dormant-by-design on direct chat.

No Docker/Anthropic dependency: skips (does not fail) when Postgres is unreachable,
mirroring ``tests/idempotency/test_ledger_db.py``. Each test builds its own engine
bound to its own event loop (this repo's custom async-test hook runs every test via a
fresh ``asyncio.run``) and disposes it in a ``finally``.
"""

from __future__ import annotations

import asyncio
import json
from contextlib import asynccontextmanager
from unittest.mock import AsyncMock, MagicMock, patch

import pytest
from langchain_core.language_models.chat_models import BaseChatModel
from langchain_core.messages import AIMessage, AIMessageChunk, ToolMessage
from langchain_core.messages.tool import tool_call_chunk
from langchain_core.outputs import ChatGeneration, ChatGenerationChunk, ChatResult
from langgraph.checkpoint.memory import MemorySaver
from sqlalchemy import delete, func, select
from sqlalchemy.ext.asyncio import async_sessionmaker, create_async_engine
from sqlalchemy.pool import NullPool
from ulid import ULID

from src.config.settings import get_settings
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

    Mirrors ``tests/idempotency/test_ledger_db.py``: a raw asyncpg connect on its own
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
                    email=f"gate-{suffix}@example.com",
                    display_name="gate-e2e-test",
                )
            )
            db.add(Workspace(workspace_id=workspace_id, name="gate-e2e-ws", owner_user_id=user_id))
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
    when awaited (``client.messages.create(...)`` returns a non-awaitable MagicMock,
    so ``await`` raises inside ``assess_risk`` and it falls back to high risk) —
    combined with ``email.send`` being a statically IRREVERSIBLE capability
    (``is_write_verification_required`` short-circuits True regardless of risk), this
    forces approval deterministically without needing a real Anthropic call.
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


async def _approval_count(factory, *, workspace_id: str, thread_id: str) -> int:
    async with factory() as db:
        result = await db.execute(
            select(func.count(Approval.approval_id)).where(
                Approval.workspace_id == workspace_id,
                Approval.artifact_refs.op("@>")({"thread_id": thread_id}),
            )
        )
        return result.scalar_one()


# ── (A) + (B): forced-autonomous pauses, persists, then approve executes exactly once ──


async def test_forced_autonomous_pauses_persists_then_approve_executes_idempotently():
    """The load-bearing proof: FORCED authorization_source="autonomous" (never occurs
    on live chat — the seam always passes direct_user_request) proves the whole gate:
    turn-1 pauses on interrupt() + persists a pending Approval; Command(resume="approve")
    executes the tool exactly once and marks the Approval approved; the replayed gate
    body does NOT create a duplicate Approval row (idempotent get-or-create)."""
    async with _gate_env() as (factory, user_id, workspace_id):
        executed: list = []
        checkpointer = MemorySaver()
        invoker = _make_invoker(factory=factory, checkpointer=checkpointer, executed=executed)
        agent = invoker._agents["executor"]
        thread_id = f"chat_{ULID()}"

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

            # --- (A) turn-1: forced-autonomous write must PAUSE (interrupt) ---
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
            assert not any(f["event"] == "error" for f in frames1), f"unexpected error: {frames1}"
            assert not any(f["event"] == "agent_done" for f in frames1), (
                f"must not complete while paused: {frames1}"
            )
            assert executed == [], "tool must NOT run while the turn is paused"

            approval_id = approval_frames[0]["approval_id"]
            assert approval_id is not None
            assert approval_frames[0]["thread_id"] == thread_id

            async with factory() as db:
                row = await db.get(Approval, approval_id)
                assert row is not None, "the pending Approval must be persisted to Postgres"
                assert row.status == "pending"
                assert row.artifact_refs["thread_id"] == thread_id
                assert row.artifact_refs["tool_call_id"]

            # --- (B) resume(approve): tool executes exactly once + no duplicate row ---
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
            f"tool must execute exactly once after approval; executed={executed}"
        )
        assert any(f["event"] == "tool_result" for f in frames2), f"frames2={frames2}"
        assert any(f["event"] == "agent_done" for f in frames2), f"frames2={frames2}"

        async with factory() as db:
            row = await db.get(Approval, approval_id)
            assert row.status == "approved"

        # Idempotency guard: the replayed gate body must NOT have created a duplicate.
        count = await _approval_count(factory, workspace_id=workspace_id, thread_id=thread_id)
        assert count == 1, f"expected exactly one Approval row for this thread, got {count}"


# ── (C) reject blocks: fresh thread, resume(reject) never executes the tool ──────


async def test_forced_autonomous_reject_blocks_the_tool():
    """A fresh forced-autonomous turn pauses; Command(resume="reject") must NOT run
    the tool and must mark the Approval rejected."""
    async with _gate_env() as (factory, user_id, workspace_id):
        executed: list = []
        checkpointer = MemorySaver()
        invoker = _make_invoker(factory=factory, checkpointer=checkpointer, executed=executed)
        agent = invoker._agents["executor"]
        thread_id = f"chat_{ULID()}"

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
            assert len(approval_frames) == 1, f"frames1={frames1}"
            approval_id = approval_frames[0]["approval_id"]
            assert executed == []

            frames2 = [
                f
                async for f in invoker.resume_deep_turn(
                    approval_id=approval_id,
                    decision="reject",
                    user_id=user_id,
                    workspace_id=workspace_id,
                )
            ]

        assert executed == [], f"a rejected tool must never execute; executed={executed}"
        rejection_frames = [
            f for f in frames2 if f["event"] == "tool_result" and f.get("blocked") is True
        ]
        assert rejection_frames, f"expected a blocked/rejected tool_result frame; frames2={frames2}"
        assert any(json.loads(f["result"]).get("rejected") is True for f in rejection_frames), (
            f"rejection payload not found; frames={rejection_frames}"
        )

        async with factory() as db:
            row = await db.get(Approval, approval_id)
            assert row.status == "rejected"


# ── (D) direct control stays ungated ──────────────────────────────────────────────


async def test_direct_user_request_stays_ungated():
    """authorization_source="direct_user_request" must NEVER pause: the tool executes
    immediately in the same turn and no Approval row is ever created."""
    async with _gate_env() as (factory, user_id, workspace_id):
        executed: list = []
        checkpointer = MemorySaver()
        invoker = _make_invoker(factory=factory, checkpointer=checkpointer, executed=executed)
        agent = invoker._agents["executor"]
        thread_id = f"chat_{ULID()}"

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
                authorization_source="direct_user_request",
                system_prompt=build_system_message(invoker.build_system_prompt(agent, "")),
            )
            config = {"configurable": {"thread_id": thread_id}}

            frames = [
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

        assert not any(f["event"] == "approval_needed" for f in frames), f"frames={frames}"
        assert executed == [("send_email", TOOL_ARGS)], (
            f"direct control must execute immediately (ungated); executed={executed}"
        )
        assert any(f["event"] == "agent_done" for f in frames), f"frames={frames}"

        count = await _approval_count(factory, workspace_id=workspace_id, thread_id=thread_id)
        assert count == 0, "direct_user_request must never persist an Approval row"


# ── (E) NEGATIVE CONTROL: prove the guard has teeth ───────────────────────────────


async def test_negative_control_bypassed_gate_check_does_not_pause():
    """If the ``is_gated_source`` short-circuit check were bypassed (forced to always
    say "not gated"), a forced-autonomous turn would NOT pause and the write would run
    ungated — proving assertion (A) actually depends on that check, i.e. the guard has
    teeth. Confirms the machinery in this file can genuinely fail."""
    async with _gate_env() as (factory, user_id, workspace_id):
        executed: list = []
        checkpointer = MemorySaver()
        invoker = _make_invoker(factory=factory, checkpointer=checkpointer, executed=executed)
        agent = invoker._agents["executor"]
        thread_id = f"chat_{ULID()}"

        with (
            patch(f"{AGENT_BUILDER_MODULE}.build_chat_model", return_value=_ScriptedModel()),
            patch(f"{CAP_SCOPE_MODULE}._is_in_scope", AsyncMock(return_value=True)),
            patch(
                f"{TRUST_GATE_MODULE}._resolve_capability",
                AsyncMock(return_value=(True, "email.send")),
            ),
            # THE BYPASS: force the dormancy check to always say "not gated", exactly
            # as if authorization_source were direct_user_request even though we pass
            # the gated literal "autonomous" below.
            patch(f"{TRUST_GATE_MODULE}.is_gated_source", return_value=False),
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

            frames = [
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

        assert not any(f["event"] == "approval_needed" for f in frames), (
            f"with is_gated_source bypassed the turn must NOT pause; frames={frames}"
        )
        assert executed == [("send_email", TOOL_ARGS)], (
            f"with the gate bypassed the tool runs ungated; executed={executed}"
        )

        count = await _approval_count(factory, workspace_id=workspace_id, thread_id=thread_id)
        assert count == 0, "a bypassed gate must never persist an Approval row"
