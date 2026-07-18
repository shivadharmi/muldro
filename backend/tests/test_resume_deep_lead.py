"""P2.2a: ``AgentInvoker.resume_deep_lead`` re-enters a paused CHAT single-lead turn.

The synthetic chat lead is NOT registered in ``self._agents`` and its scope is
plan-derived, so ``resume_deep_lead`` rebuilds it from the ``lead_scope`` persisted on the
Approval (via ``_make_lead``), rebuilds with ``authorization_source=direct_user_request``
(trust_gate dormant), and ALWAYS re-installs the action-time ``permission_gate``
FAIL-CLOSED so a REJECT genuinely skips the write and an APPROVE fires it exactly once.

Two layers of test:

* Unit (fake session, no LangGraph/model): guards (tenant isolation, A6, already-decided,
  invalid decision, graceful deny), fail-closed resume-mode coercion, decision_type/
  decision_reason persistence, and the rebuilt lead's plan-bounded scope.

* Real-DB + MemorySaver (skips when Postgres is unreachable): the MANDATORY invariant —
  pause a real turn via ``stream_deep_lead(permission_mode="ask")`` then resume; a REJECT
  records ZERO tool executions + a rejection ToolMessage, an APPROVE fires the write EXACTLY
  once. This is the proof the gate is genuinely re-installed on resume (without it, reject
  would fail-OPEN).
"""

from __future__ import annotations

import asyncio
import json
from contextlib import asynccontextmanager
from types import SimpleNamespace
from unittest.mock import AsyncMock, MagicMock, patch

import pytest
from langchain_core.language_models.chat_models import BaseChatModel
from langchain_core.messages import AIMessage, AIMessageChunk, ToolMessage
from langchain_core.messages.tool import tool_call_chunk
from langchain_core.outputs import ChatGeneration, ChatGenerationChunk, ChatResult
from langgraph.checkpoint.memory import MemorySaver
from langgraph.types import Command
from sqlalchemy import delete
from sqlalchemy.ext.asyncio import async_sessionmaker, create_async_engine
from sqlalchemy.pool import NullPool
from ulid import ULID

from src.config.settings import get_settings
from src.deep_runtime.authorization import AuthorizationSource
from src.deep_runtime.thread_identity import make_thread_id
from src.models.approvals import Approval
from src.models.trust_state import TrustCeiling, TrustState
from src.models.users import User, Workspace
from src.orchestrator.agent_invoker import AgentInvoker
from src.orchestrator.lead_builder import _make_lead
from tests.conftest import make_mock_settings

INVOKER_MODULE = "src.orchestrator.agent_invoker"
TRUST_GATE_MODULE = "src.deep_runtime.middleware.trust_gate"
CAP_SCOPE_MODULE = "src.deep_runtime.middleware.capability_scope"
AGENT_BUILDER_MODULE = "src.deep_runtime.agent_builder"

_UNSET = object()


# ── unit-level fakes (no DB, no LangGraph) ───────────────────────────────────────


def _fake_lead_approval(
    *,
    lead_scope=("email.send",),
    thread_id=_UNSET,
    permission_mode="ask",
    workspace_id="ws",
    status="pending",
    context_block="",
):
    """A SimpleNamespace Approval carrying chat single-lead ``artifact_refs``.

    ``thread_id=_UNSET`` defaults to a workspace-bound thread id (so the A6 round-trip
    passes); pass ``thread_id=None`` to OMIT it (malformed) or a literal to force a value.
    ``lead_scope=None`` omits the scope (malformed).
    """
    refs: dict = {"agent_name": "lead", "chat": True, "context_block": context_block}
    if thread_id is _UNSET:
        thread_id = make_thread_id(workspace_id)
    if thread_id is not None:
        refs["thread_id"] = thread_id
    if lead_scope is not None:
        refs["lead_scope"] = list(lead_scope)
    if permission_mode is not None:
        refs["permission_mode"] = permission_mode
    return SimpleNamespace(
        workspace_id=workspace_id,
        artifact_refs=refs,
        status=status,
        decided_at=None,
        approved_by=None,
        decision_reason=None,
    )


def _make_lead_invoker(approval):
    """A real AgentInvoker whose db_factory yields a fake session resolving *approval*.

    ``agents={}`` deliberately — the synthetic lead is NOT registered; resume_deep_lead must
    rebuild it from the persisted scope, never look it up.
    """
    tool_executor = MagicMock()
    tool_executor.apply_cache_control_to_tools = lambda tools: tools
    tool_executor.get_tools_for_agent = AsyncMock(return_value=[])

    context = MagicMock()
    context.assemble_context = AsyncMock(return_value="")

    fake_db = MagicMock(name="fake-db")
    fake_db.get = AsyncMock(return_value=approval)
    fake_db.commit = AsyncMock()
    # I1 atomic flip: resume_deep_lead consumes the pending approval via a conditional
    # UPDATE (``_cas_flip_pending``). rowcount=1 = THIS resume won the flip (the default
    # happy path); a lost-race test overrides this to rowcount=0.
    fake_db.execute = AsyncMock(return_value=SimpleNamespace(rowcount=1))

    @asynccontextmanager
    async def _db_factory():
        yield fake_db

    inv = AgentInvoker(
        settings=make_mock_settings(runtime="deep", cheap_mode=False),
        client=MagicMock(),
        services=None,
        budget=MagicMock(),
        circuit_breaker=MagicMock(),
        db_factory_provider=lambda: _db_factory,
        tool_executor=tool_executor,
        context=context,
        agents={},
    )
    return inv, fake_db


async def _empty_stream(*args, **kwargs):
    return
    yield  # pragma: no cover - makes this an async generator


def _stream_recorder(recorded: dict):
    async def _fake_stream(*args, **kwargs):
        recorded["args"] = args
        recorded["kwargs"] = kwargs
        yield {"event": "agent_done", "agent": "lead", "text": "ok", "tools_called": None}

    return _fake_stream


async def _drive(inv, *, decision, reason=None, user_id="u", workspace_id="ws"):
    return [
        f
        async for f in inv.resume_deep_lead(
            approval_id="apr_x",
            decision=decision,
            reason=reason,
            user_id=user_id,
            workspace_id=workspace_id,
        )
    ]


# ── unit: invalid decision ───────────────────────────────────────────────────────


async def test_invalid_decision_yields_error_and_never_streams():
    approval = _fake_lead_approval()
    inv, fake_db = _make_lead_invoker(approval)
    with patch(f"{INVOKER_MODULE}.stream_deep_agent_events") as mock_stream:
        frames = await _drive(inv, decision="maybe")
    assert any(f["event"] == "error" for f in frames)
    mock_stream.assert_not_called()
    fake_db.commit.assert_not_awaited()
    assert approval.status == "pending"


# ── unit: tenant isolation + status guards (shared helper) ───────────────────────


async def test_cross_tenant_is_not_found_and_not_mutated():
    approval = _fake_lead_approval(workspace_id="ws_victim")
    inv, fake_db = _make_lead_invoker(approval)
    with patch(f"{INVOKER_MODULE}.stream_deep_agent_events") as mock_stream:
        frames = await _drive(inv, decision="approve", workspace_id="ws_attacker")
    assert any(f["event"] == "error" and f.get("message") == "approval not found" for f in frames)
    mock_stream.assert_not_called()
    assert approval.status == "pending"
    fake_db.commit.assert_not_awaited()


async def test_a6_thread_workspace_mismatch_is_not_found():
    # Passes the IDOR guard (approval.workspace_id == caller ws) but the thread_id embeds a
    # DIFFERENT workspace → A6 refuses with the same generic not-found envelope.
    approval = _fake_lead_approval(workspace_id="ws", thread_id=make_thread_id("ws_other"))
    inv, fake_db = _make_lead_invoker(approval)
    with patch(f"{INVOKER_MODULE}.stream_deep_agent_events") as mock_stream:
        frames = await _drive(inv, decision="approve", workspace_id="ws")
    assert any(f["event"] == "error" and f.get("message") == "approval not found" for f in frames)
    mock_stream.assert_not_called()
    fake_db.commit.assert_not_awaited()


async def test_already_decided_is_not_pending():
    approval = _fake_lead_approval(status="approved")
    inv, fake_db = _make_lead_invoker(approval)
    with patch(f"{INVOKER_MODULE}.stream_deep_agent_events") as mock_stream:
        frames = await _drive(inv, decision="approve")
    assert any(f["event"] == "error" and f.get("message") == "approval not pending" for f in frames)
    mock_stream.assert_not_called()
    fake_db.commit.assert_not_awaited()


# ── unit: graceful deny (fail-closed on malformed rebuild inputs) ────────────────


async def test_missing_lead_scope_denies_and_stays_pending():
    approval = _fake_lead_approval(lead_scope=None)  # thread_id present, NO scope
    inv, fake_db = _make_lead_invoker(approval)
    with patch(f"{INVOKER_MODULE}.stream_deep_agent_events") as mock_stream:
        frames = await _drive(inv, decision="approve")
    assert any(
        f["event"] == "error" and f.get("message") == "approval not resumable" for f in frames
    )
    mock_stream.assert_not_called()
    # A missing scope must DENY (never fall back to a broad scope) and stay pending.
    assert approval.status == "pending"
    fake_db.commit.assert_not_awaited()


async def test_missing_thread_id_denies_and_stays_pending():
    approval = _fake_lead_approval(thread_id=None)  # scope present, NO thread_id
    inv, fake_db = _make_lead_invoker(approval)
    with patch(f"{INVOKER_MODULE}.stream_deep_agent_events") as mock_stream:
        frames = await _drive(inv, decision="approve")
    # A missing thread_id is refused (by the shared A6 guard) — an error, NO exception, and
    # the approval is not consumed.
    assert any(f["event"] == "error" for f in frames)
    mock_stream.assert_not_called()
    assert approval.status == "pending"
    fake_db.commit.assert_not_awaited()


# ── unit: fail-closed resume-mode coercion (THE invariant, at build time) ─────────


async def _capture_build(approval, *, decision="approve", reason=None):
    inv, fake_db = _make_lead_invoker(approval)
    build_spy = AsyncMock(return_value=MagicMock())
    inv._build_deep_agent_for = build_spy
    with patch(f"{INVOKER_MODULE}.stream_deep_agent_events", _empty_stream):
        frames = await _drive(inv, decision=decision, reason=reason)
    assert not any(f.get("event") == "error" for f in frames), f"frames={frames}"
    return inv, fake_db, build_spy


async def test_resume_always_installs_gate_ask_preserved():
    approval = _fake_lead_approval(permission_mode="ask")
    _, _, build_spy = await _capture_build(approval)
    build_spy.assert_awaited_once()
    assert build_spy.await_args.kwargs["permission_mode"] == "ask"


async def test_resume_auto_mode_preserved():
    approval = _fake_lead_approval(permission_mode="auto")
    _, _, build_spy = await _capture_build(approval)
    assert build_spy.await_args.kwargs["permission_mode"] == "auto"


async def test_resume_missing_mode_coerces_to_ask_fail_closed():
    # No permission_mode persisted → the gate MUST still be installed (coerced to "ask").
    approval = _fake_lead_approval(permission_mode=None)
    _, _, build_spy = await _capture_build(approval)
    assert build_spy.await_args.kwargs["permission_mode"] == "ask"


async def test_resume_bypass_persisted_coerces_to_ask_fail_closed():
    # A pending approval proves the first pass interrupted, so even a stale "bypass" is
    # coerced to "ask" — the gate is never left inactive on resume (fail-closed).
    approval = _fake_lead_approval(permission_mode="bypass")
    _, _, build_spy = await _capture_build(approval)
    assert build_spy.await_args.kwargs["permission_mode"] == "ask"


async def test_resume_rebuilds_direct_user_request_and_write_lock():
    approval = _fake_lead_approval()
    _, _, build_spy = await _capture_build(approval)
    kwargs = build_spy.await_args.kwargs
    assert kwargs["authorization_source"] == AuthorizationSource.DIRECT_USER_REQUEST
    assert kwargs["require_write_lock"] is True


async def test_resume_rebuilds_lead_with_persisted_plan_scope():
    approval = _fake_lead_approval(lead_scope=["email.send", "calendar.read"])
    _, _, build_spy = await _capture_build(approval)
    lead_arg = build_spy.await_args.args[0]
    # offered-tools ⊆ enforced-scope reproduced: the rebuilt lead's scope IS the persisted set.
    assert lead_arg.capability_scope == frozenset({"email.send", "calendar.read"})
    assert lead_arg.name == "lead"


async def test_resume_applies_presenter_voice_augmentation():
    """The RESUMED lead is the reply-producing lead (it emits the post-decision confirmation),
    so it MUST carry PRESENTER_VOICE via is_reply_lead=True — parity with stream_deep_lead.
    Guards the PRESENTER_VOICE surface-drop regression class."""
    from src.orchestrator import agent_invoker as _ai

    approval = _fake_lead_approval()
    inv, _ = _make_lead_invoker(approval)
    inv._build_deep_agent_for = AsyncMock(return_value=MagicMock())
    with (
        patch(f"{INVOKER_MODULE}.stream_deep_agent_events", _empty_stream),
        patch.object(
            _ai,
            "_augment_system_blocks_for_inline",
            wraps=_ai._augment_system_blocks_for_inline,
        ) as augment_spy,
    ):
        await _drive(inv, decision="approve")
    augment_spy.assert_called_once()
    assert augment_spy.call_args.kwargs.get("is_reply_lead") is True


# ── unit: decision_type stamping + decision_reason persistence ────────────────────


async def test_approve_without_reason_stamps_approved():
    approval = _fake_lead_approval()
    _, fake_db, _ = await _capture_build(approval, decision="approve", reason=None)
    assert approval.status == "approved"
    assert approval.artifact_refs["decision_type"] == "approved"
    fake_db.commit.assert_awaited()


async def test_approve_with_reason_stamps_modified():
    approval = _fake_lead_approval()
    _, _, _ = await _capture_build(approval, decision="approve", reason="use the other address")
    assert approval.artifact_refs["decision_type"] == "modified"
    assert approval.decision_reason == "use the other address"


async def test_reject_persists_reason_and_no_decision_type():
    approval = _fake_lead_approval()
    _, _, _ = await _capture_build(approval, decision="reject", reason="no thanks")
    assert approval.status == "rejected"
    assert approval.decision_reason == "no thanks"
    # decision_type is stamped only on approve (mirrors routes_approvals A-7).
    assert "decision_type" not in approval.artifact_refs


# ── unit: I1 atomic-flip lost-race (double resume) ───────────────────────────────


async def test_lost_cas_race_yields_not_pending_and_never_streams():
    """I1: two concurrent resumes both pass the advisory read-side pending check, but the
    conditional-UPDATE CAS lets only ONE win. The loser (``rowcount 0``) yields 'approval
    not pending' and NEVER rebuilds/streams the agent — so the paused write replays exactly
    once, never twice."""
    approval = _fake_lead_approval()
    inv, fake_db = _make_lead_invoker(approval)
    # This resume LOST the race: the conditional UPDATE matched 0 rows (a concurrent resume
    # already consumed the pending approval).
    fake_db.execute = AsyncMock(return_value=SimpleNamespace(rowcount=0))
    build_spy = AsyncMock(return_value=MagicMock())
    inv._build_deep_agent_for = build_spy
    with patch(f"{INVOKER_MODULE}.stream_deep_agent_events", _empty_stream):
        frames = await _drive(inv, decision="approve")

    assert any(
        f["event"] == "error" and f.get("message") == "approval not pending" for f in frames
    ), f"frames={frames}"
    # The agent is NEVER rebuilt (the CAS aborts before _build_deep_agent_for) → no stream.
    build_spy.assert_not_awaited()
    fake_db.execute.assert_awaited_once()


async def test_resume_streams_command_and_thread_id():
    approval = _fake_lead_approval()
    thread_id = approval.artifact_refs["thread_id"]
    inv, _ = _make_lead_invoker(approval)
    inv._build_deep_agent_for = AsyncMock(return_value=MagicMock())
    recorded: dict = {}
    with patch(f"{INVOKER_MODULE}.stream_deep_agent_events", _stream_recorder(recorded)):
        frames = await _drive(inv, decision="approve")
    assert any(f["event"] == "agent_done" for f in frames)
    resume_cmd = recorded["args"][1]
    assert isinstance(resume_cmd, Command)
    assert resume_cmd.resume == "approve"
    assert recorded["args"][2]["configurable"]["thread_id"] == thread_id
    assert recorded["kwargs"]["agent_name"] == "lead"
    assert recorded["kwargs"]["durability"] == "sync"


# ── real-DB + MemorySaver: MANDATORY reject-doesn't-fire / approve-fires-once ─────


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
    except Exception:  # pragma: no cover - environment-dependent
        return False


_DB_REACHABLE = _db_reachable()

TOOL_DEF = {
    "name": "send_email",
    "description": "Send an email on the user's behalf.",
    "input_schema": {"type": "object", "properties": {"to": {"type": "string"}}},
}
TOOL_ARGS = {"to": "vip@example.com"}


class _ScriptedModel(BaseChatModel):
    """Turn 1 calls ``send_email``; the resumed turn (sees a prior ToolMessage) answers."""

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
                        name="send_email", args=json.dumps(TOOL_ARGS), id="call_send_1", index=0
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


@asynccontextmanager
async def _gate_env():
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
                    email=f"lead-{suffix}@example.com",
                    display_name="resume-lead-test",
                )
            )
            db.add(Workspace(workspace_id=workspace_id, name="lead-ws", owner_user_id=user_id))
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


class _FakeRedis:
    """Minimal in-memory async Redis satisfying ``acquire_write_lock`` (SET NX EX + release
    Lua eval). ``require_write_lock=True`` forces the write through the lock, so an approve
    that fires the write proves the lock genuinely acquired (not fail-closed)."""

    def __init__(self):
        self._store: dict = {}

    async def set(self, key, val, nx=False, ex=None):  # noqa: ANN001, ARG002
        if nx and key in self._store:
            return None
        self._store[key] = val
        return True

    async def eval(self, _script, _numkeys, key, token):  # noqa: ANN001
        if self._store.get(key) == token:
            self._store.pop(key, None)
            return 1
        return 0


def _make_real_invoker(*, factory, checkpointer, executed: list) -> AgentInvoker:
    tool_executor = MagicMock()
    tool_executor.apply_cache_control_to_tools = lambda tools: tools
    tool_executor.get_tools_for_agent = AsyncMock(return_value=[TOOL_DEF])

    async def fake_execute(name, args, uid, ws):
        executed.append((name, args))
        return {"ok": True}

    tool_executor.execute_tool = fake_execute

    context = MagicMock()
    context.assemble_context = AsyncMock(return_value="")

    # services.extras carries the fake redis so the (fail-closed) write lock can acquire —
    # otherwise require_write_lock=True refuses the approved write (redis unavailable).
    services = SimpleNamespace(extras={"redis": _FakeRedis()}, vector_store=None)

    return AgentInvoker(
        settings=make_mock_settings(runtime="deep", cheap_mode=False),
        client=MagicMock(),
        services=services,
        budget=MagicMock(),
        circuit_breaker=MagicMock(),
        db_factory_provider=lambda: factory,
        tool_executor=tool_executor,
        context=context,
        agents={},  # the synthetic lead is NOT registered
        checkpointer_provider=lambda: checkpointer,
    )


async def _pause_lead_then_resume(*, decision: str, reason: str | None = None):
    """Pause a real single-lead turn on an ``ask`` write, then resume with *decision*.

    Returns ``(executed, frames2, approval_row)``. A shared MemorySaver spans the pause and
    the resume build so the paused thread is genuinely re-entered.
    """
    async with _gate_env() as (factory, user_id, workspace_id):
        executed: list = []
        saver = MemorySaver()
        invoker = _make_real_invoker(factory=factory, checkpointer=saver, executed=executed)
        lead = _make_lead(frozenset({"email.send"}), False)

        with (
            patch(f"{AGENT_BUILDER_MODULE}.build_chat_model", return_value=_ScriptedModel()),
            patch(f"{CAP_SCOPE_MODULE}._is_in_scope", AsyncMock(return_value=True)),
            patch(
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
            frames1 = [
                f
                async for f in invoker.stream_deep_lead(
                    lead,
                    message="go",
                    context_block="",
                    user_id=user_id,
                    workspace_id=workspace_id,
                    permission_mode="ask",
                )
            ]
            approval_frames = [f for f in frames1 if f["event"] == "approval_needed"]
            assert len(approval_frames) == 1, f"expected one pause; frames1={frames1}"
            assert executed == [], "tool must NOT run while the turn is paused"
            approval_id = approval_frames[0]["approval_id"]

            # The persisted Approval carries the chat single-lead provenance the resume needs.
            async with factory() as db:
                row = await db.get(Approval, approval_id)
                assert row.status == "pending"
                assert row.artifact_refs["lead_scope"] == ["email.send"]
                assert row.artifact_refs["permission_mode"] == "ask"
                assert row.artifact_refs["chat"] is True

            frames2 = [
                f
                async for f in invoker.resume_deep_lead(
                    approval_id=approval_id,
                    decision=decision,
                    reason=reason,
                    user_id=user_id,
                    workspace_id=workspace_id,
                )
            ]

        async with factory() as db:
            approval_row = await db.get(Approval, approval_id)
        return executed, frames2, approval_row


@pytest.mark.skipif(not _DB_REACHABLE, reason="Postgres not reachable")
async def test_lead_resume_reject_does_not_fire_write():
    """THE fail-closed invariant: a REJECT resume must NOT execute the write (0 recorded
    calls) and must surface a rejection ToolMessage quoting the reason. Proof the gate is
    genuinely re-installed on resume — without it, reject would fail-OPEN."""
    executed, frames2, row = await _pause_lead_then_resume(decision="reject", reason="not now")

    assert executed == [], f"a rejected chat-lead write must NEVER execute; executed={executed}"
    rejection_frames = [
        f for f in frames2 if f["event"] == "tool_result" and f.get("blocked") is True
    ]
    assert rejection_frames, f"expected a blocked rejection tool_result; frames2={frames2}"
    assert any(json.loads(f["result"]).get("rejected") is True for f in rejection_frames)
    # The user's reason is quoted back to the model (not a bare flag).
    assert any(json.loads(f["result"]).get("error") == "not now" for f in rejection_frames)
    assert row.status == "rejected"
    assert row.decision_reason == "not now"


@pytest.mark.skipif(not _DB_REACHABLE, reason="Postgres not reachable")
async def test_lead_resume_approve_fires_write_exactly_once():
    """An APPROVE resume executes the write EXACTLY once and marks the Approval approved."""
    executed, frames2, row = await _pause_lead_then_resume(decision="approve")

    assert executed == [("send_email", TOOL_ARGS)], (
        f"approve must fire the write exactly once; executed={executed}"
    )
    assert any(f["event"] == "tool_result" for f in frames2), f"frames2={frames2}"
    assert any(f["event"] == "agent_done" for f in frames2), f"frames2={frames2}"
    assert row.status == "approved"
    assert row.artifact_refs.get("decision_type") == "approved"
