"""P2.1: permission_gate middleware — action-time confirmation for the deep chat lead.

The permission gate is a SECOND ``wrap_tool_call`` interceptor, installed immediately
AFTER ``trust_gate`` on the chat single-lead path. It is AUTH-SOURCE-INDEPENDENT (it
never consults ``authorization_source`` / ``is_gated_source``) and decides purely on
``permission_mode`` × risk (the Claude-Code ``bypass`` / ``ask`` / ``auto`` model).

Two flavours of test, mirroring ``test_trust_gate.py``:

* Non-interrupt branches (builtin / read / lookup-fail / auto-safe passthrough) are
  driven DIRECTLY via ``mw.awrap_tool_call(request, handler)`` — no graph runtime needed.
* Interrupt branches (ask-interrupts-every-write / auto-interrupts-iff-risky / reject)
  build a real ``create_deep_agent`` with a fake scripted streaming model + a real
  ``echo`` tool, and resume with ``Command(resume=...)``.

No live Anthropic API. The idempotent-persist obligation (M3) is proven BOTH with mocks
(get-or-create reuse + IntegrityError re-select) AND against real Postgres (the gate body
run TWICE for the same key yields exactly ONE Approval row).
"""

from __future__ import annotations

import asyncio
import json
from contextlib import asynccontextmanager
from types import SimpleNamespace
from unittest.mock import AsyncMock, MagicMock, patch

import pytest
from deepagents import create_deep_agent
from langchain_core.language_models.chat_models import BaseChatModel
from langchain_core.messages import AIMessage, AIMessageChunk, ToolMessage
from langchain_core.messages.tool import tool_call_chunk
from langchain_core.outputs import ChatGeneration, ChatGenerationChunk, ChatResult
from langchain_core.tools import tool
from langgraph.checkpoint.memory import MemorySaver
from langgraph.types import Command

from src.deep_runtime.middleware.permission_gate import (
    _persist_permission_approval,
    make_permission_gate_middleware,
    permission_should_interrupt,
)
from src.services.risk_assessor import RiskAssessment

MODULE = "src.deep_runtime.middleware.permission_gate"
# ``create_approval`` executes inside the shared ``trust_gate._get_or_create_approval`` helper
# (A2 dedup), so patch it in its DEFINING module, not ``permission_gate``.
TRUST_GATE_MODULE = "src.deep_runtime.middleware.trust_gate"
USER_ID = "u_test"
WORKSPACE_ID = "ws_test"
THREAD_ID = "chat_thread_1"
LEAD_SCOPE = frozenset({"email.send", "calendar.create"})


# ── RiskAssessment fixtures ──────────────────────────────────────────────────


def _risk(*, risk_level="low", reversible=True, blast_radius="self") -> RiskAssessment:
    return RiskAssessment(
        risk_level=risk_level, reasoning="r", reversible=reversible, blast_radius=blast_radius
    )


SAFE = _risk(risk_level="low", reversible=True, blast_radius="self")
IRREVERSIBLE = _risk(risk_level="low", reversible=False, blast_radius="self")
EXTERNAL_SINGLE = _risk(risk_level="low", reversible=True, blast_radius="external_single")
HIGH_ONLY = _risk(risk_level="high", reversible=True, blast_radius="self")
HIGH_ALL = _risk(risk_level="high", reversible=False, blast_radius="external_single")


# ── shared test doubles (mirrors test_trust_gate.py) ─────────────────────────


def _request(tool_name: str, args: dict | None = None, call_id: str = "call_123"):
    return SimpleNamespace(tool_call={"name": tool_name, "args": args or {}, "id": call_id})


def _hook(mw):
    return mw.awrap_tool_call


def _persist_db_factory(existing=None):
    """A db_factory whose session backs the find/persist blocks.

    ``.execute(...).scalars().first()`` resolves to *existing* (default ``None`` so the
    idempotent get-or-create takes the create branch); ``.commit`` is an AsyncMock.
    """

    @asynccontextmanager
    async def _factory():
        db = MagicMock(name="persist-db")
        result = MagicMock(name="execute-result")
        result.scalars.return_value.first.return_value = existing
        db.execute = AsyncMock(return_value=result)
        db.commit = AsyncMock()
        db.add = MagicMock()
        yield db

    return _factory


def _make_echo(calls: list[str]):
    @tool
    def echo(text: str) -> str:
        """Echo the input text back (side-effecting write tool for the gate proof)."""
        calls.append(text)
        return f"echo: {text}"

    return echo


class _ScriptedModel(BaseChatModel):
    """Fake streaming model: turn 1 calls ``echo``, the resumed turn answers."""

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
                        name="echo",
                        args=json.dumps({"text": "hello"}),
                        id="call_echo",
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


def _find_interrupt(items):
    for mode, payload in items:
        if mode == "updates" and isinstance(payload, dict) and "__interrupt__" in payload:
            return payload["__interrupt__"][0]
    return None


async def _drive(agent, graph_input, cfg, *, durability: str | None = None):
    items = []
    kwargs = {"stream_mode": ["messages", "updates"]}
    if durability is not None:
        kwargs["durability"] = durability
    async for mode, payload in agent.astream(graph_input, config=cfg, **kwargs):
        items.append((mode, payload))
    return items


async def _final_ai_texts(agent, cfg) -> list[str]:
    state = await agent.aget_state(cfg)
    msgs = state.values.get("messages", [])
    return [
        (m.text if hasattr(m, "text") else str(m.content))
        for m in msgs
        if isinstance(m, AIMessage) and not m.tool_calls and m.content
    ]


async def _error_tool_messages(agent, cfg) -> list[ToolMessage]:
    state = await agent.aget_state(cfg)
    msgs = state.values.get("messages", [])
    return [m for m in msgs if isinstance(m, ToolMessage) and m.status == "error"]


def _gate(
    *,
    permission_mode: str,
    resolve_capability,
    assess_risk,
    db_factory,
    thread_id: str = THREAD_ID,
    lead_scope=LEAD_SCOPE,
    context_block: str = "",
    user_message: str = "",
):
    return make_permission_gate_middleware(
        permission_mode=permission_mode,
        workspace_id=WORKSPACE_ID,
        user_id=USER_ID,
        thread_id=thread_id,
        agent_name="lead",
        db_factory=db_factory,
        assess_risk=assess_risk,
        resolve_capability=resolve_capability,
        context_block=context_block,
        lead_scope=lead_scope,
        user_message=user_message,
    )


@pytest.fixture
def handler():
    h = AsyncMock(name="handler")
    h.return_value = ToolMessage(content="executed", tool_call_id="call_123")
    return h


# ── permission_should_interrupt: exhaustive over mode × risk ─────────────────


def test_bypass_never_interrupts_even_high():
    assert permission_should_interrupt("bypass", SAFE) is False
    assert permission_should_interrupt("bypass", HIGH_ALL) is False
    assert permission_should_interrupt("bypass", None) is False


def test_ask_always_interrupts():
    assert permission_should_interrupt("ask", SAFE) is True
    assert permission_should_interrupt("ask", None) is True
    assert permission_should_interrupt("ask", HIGH_ALL) is True


def test_auto_reversible_internal_low_does_not_interrupt():
    assert permission_should_interrupt("auto", SAFE) is False


def test_auto_irreversible_interrupts():
    assert permission_should_interrupt("auto", IRREVERSIBLE) is True


def test_auto_external_blast_radius_interrupts():
    assert permission_should_interrupt("auto", EXTERNAL_SINGLE) is True
    assert permission_should_interrupt("auto", _risk(blast_radius="external_multiple")) is True
    assert permission_should_interrupt("auto", _risk(blast_radius="public")) is True


def test_auto_high_risk_interrupts():
    assert permission_should_interrupt("auto", HIGH_ONLY) is True


def test_auto_none_assessment_fails_closed():
    """``auto`` requires a non-None assessment; a None assessment cannot be classified,
    so the gate fails CLOSED (require approval) rather than silently auto-executing."""
    assert permission_should_interrupt("auto", None) is True


def test_unknown_mode_fails_closed():
    assert permission_should_interrupt("weird", SAFE) is True


# ── gate: non-interrupt branches (direct hook) ───────────────────────────────


async def test_builtin_falls_through(handler):
    """A deepagents built-in (write_todos) is never gated — no lookup, no risk."""
    assess_risk = AsyncMock(name="assess_risk")
    resolve_capability = AsyncMock(name="resolve_capability")

    mw = _gate(
        permission_mode="ask",
        resolve_capability=resolve_capability,
        assess_risk=assess_risk,
        db_factory=_persist_db_factory(),
    )
    result = await _hook(mw)(_request("write_todos", {"todos": []}, "c1"), handler)

    handler.assert_awaited_once()
    assert result is handler.return_value
    resolve_capability.assert_not_awaited()
    assess_risk.assert_not_awaited()


async def test_read_capability_never_gates(handler):
    """A tool resolving to a READ capability executes without risk/approval — reads never gate."""
    assess_risk = AsyncMock(name="assess_risk")
    resolve_capability = AsyncMock(return_value=(True, "email.read"))

    mw = _gate(
        permission_mode="ask",
        resolve_capability=resolve_capability,
        assess_risk=assess_risk,
        db_factory=_persist_db_factory(),
    )
    result = await _hook(mw)(_request("read_email", {}, "c1"), handler)

    handler.assert_awaited_once()
    assert result is handler.return_value
    assess_risk.assert_not_awaited()
    resolve_capability.assert_awaited_once_with("read_email")


async def test_none_capability_falls_through(handler):
    """A lookup that SUCCEEDS but yields no capability (True, None) falls through (cannot
    happen on the scoped chat path — capability_scope already denied such tools)."""
    assess_risk = AsyncMock(name="assess_risk")
    resolve_capability = AsyncMock(return_value=(True, None))

    mw = _gate(
        permission_mode="ask",
        resolve_capability=resolve_capability,
        assess_risk=assess_risk,
        db_factory=_persist_db_factory(),
    )
    await _hook(mw)(_request("mystery_tool", {}, "c1"), handler)

    handler.assert_awaited_once()
    assess_risk.assert_not_awaited()


async def test_capability_lookup_error_fails_closed(handler):
    """A capability-lookup ERROR (False, None) on a write must fail CLOSED: the gate returns
    a ToolMessage(status='error', blocked=True) and never runs the tool or assesses risk."""
    assess_risk = AsyncMock(name="assess_risk")
    resolve_capability = AsyncMock(return_value=(False, None))

    mw = _gate(
        permission_mode="ask",
        resolve_capability=resolve_capability,
        assess_risk=assess_risk,
        db_factory=_persist_db_factory(),
    )
    result = await _hook(mw)(_request("send_email", {}, "c1"), handler)

    handler.assert_not_awaited()
    assess_risk.assert_not_awaited()
    assert isinstance(result, ToolMessage)
    assert result.status == "error"
    assert result.tool_call_id == "c1"
    payload = json.loads(result.content)
    assert payload.get("blocked") is True


async def test_auto_safe_write_passthrough_no_interrupt(handler):
    """auto mode + a reversible-internal-low write → NO interrupt, NO approval persisted;
    the tool executes inline after a single risk assessment."""
    assess_risk = AsyncMock(return_value=SAFE)
    resolve_capability = AsyncMock(return_value=(True, "email.draft"))
    create_approval_mock = AsyncMock()

    mw = _gate(
        permission_mode="auto",
        resolve_capability=resolve_capability,
        assess_risk=assess_risk,
        db_factory=_persist_db_factory(),
    )
    with patch(f"{TRUST_GATE_MODULE}.create_approval", create_approval_mock):
        result = await _hook(mw)(_request("draft_email", {}, "c1"), handler)

    handler.assert_awaited_once()
    assert result is handler.return_value
    assess_risk.assert_awaited_once()
    create_approval_mock.assert_not_called()


async def test_bypass_passes_through_without_assessing_or_persisting(handler):
    """bypass mode is self-defending: even if the gate were (mis)installed for bypass, a write
    passes straight through with NO capability resolve, NO risk assessment, NO approval — the
    guard short-circuits before any gate work (keeps the docstring's no-op contract true)."""
    assess_risk = AsyncMock(name="assess_risk")
    resolve_capability = AsyncMock(name="resolve_capability")
    create_approval_mock = AsyncMock()

    mw = _gate(
        permission_mode="bypass",
        resolve_capability=resolve_capability,
        assess_risk=assess_risk,
        db_factory=_persist_db_factory(),
    )
    with patch(f"{TRUST_GATE_MODULE}.create_approval", create_approval_mock):
        result = await _hook(mw)(_request("send_email", {}, "c1"), handler)

    handler.assert_awaited_once()
    assert result is handler.return_value
    resolve_capability.assert_not_awaited()  # short-circuits BEFORE capability resolve
    assess_risk.assert_not_awaited()
    create_approval_mock.assert_not_called()


# ── gate: interrupt branches (scripted deep agent) ───────────────────────────


async def test_ask_interrupts_every_write_without_assessing_risk():
    """ask mode gates EVERY write unconditionally — no risk classifier is called (risk_level
    is 'n/a'), the tool is held until Command(resume='approve'), then runs exactly once.
    The persisted Approval carries the full chat-permission artifact_refs."""
    calls: list[str] = []
    echo = _make_echo(calls)

    assess_risk = AsyncMock(name="assess_risk")  # must NEVER be awaited in ask mode
    resolve_capability = AsyncMock(return_value=(True, "email.send"))
    captured: dict = {}

    async def fake_create_approval(db, **kwargs):
        captured.update(kwargs)
        return SimpleNamespace(approval_id="apr_ask")

    thread_id = "t-ask-write"
    mw = _gate(
        permission_mode="ask",
        resolve_capability=resolve_capability,
        assess_risk=assess_risk,
        db_factory=_persist_db_factory(),
        thread_id=thread_id,
        lead_scope=frozenset({"email.send", "calendar.create"}),
        context_block="CTX",
        user_message="book me a flight",
    )
    agent = create_deep_agent(
        model=_ScriptedModel(),
        tools=[echo],
        middleware=[mw],
        checkpointer=MemorySaver(),
        system_prompt="t",
    )
    cfg = {"configurable": {"thread_id": thread_id}}

    with patch(f"{TRUST_GATE_MODULE}.create_approval", side_effect=fake_create_approval):
        items = await _drive(
            agent,
            {"messages": [{"role": "user", "content": "go"}]},
            cfg,
            durability="sync",
        )
        intr = _find_interrupt(items)
        assert intr is not None, "ask mode must pause on an __interrupt__ update"
        assert intr.value["approval_id"] == "apr_ask"
        assert intr.value["thread_id"] == thread_id
        assert intr.value["capability"] == "email.send"
        assert intr.value["risk_level"] == "n/a"
        assert calls == []
        assess_risk.assert_not_awaited()

        # artifact_refs carry the idempotency key + chat-permission provenance.
        refs = captured["artifact_refs"]
        assert refs["thread_id"] == thread_id
        assert refs["tool_call_id"] == "call_echo"
        assert refs["tool_name"] == "echo"
        assert refs["capability"] == "email.send"
        assert refs["reversible"] is True  # ask mode has no assessment → default True
        assert refs["blast_radius"] == "self"  # ask mode default
        assert refs["permission_mode"] == "ask"
        assert refs["chat"] is True
        assert refs["lead_scope"] == ["calendar.create", "email.send"]  # sorted
        assert refs["context_block"] == "CTX"
        # A1: the ORIGINAL user message is persisted so an approved resume can fire the learner.
        assert refs["user_message"] == "book me a flight"
        assert captured["approval_type"] == "tool:echo"
        assert captured["risk_level"] == "n/a"

        await _drive(agent, Command(resume="approve"), cfg)

    assert calls == ["hello"]
    assert await _final_ai_texts(agent, cfg)
    assess_risk.assert_not_awaited()


async def test_auto_risky_write_interrupts_then_approve_executes():
    """auto mode + a risky write (high / irreversible / external) → interrupt; the tool
    runs only after Command(resume='approve')."""
    calls: list[str] = []
    echo = _make_echo(calls)

    assess_risk = AsyncMock(return_value=HIGH_ALL)
    resolve_capability = AsyncMock(return_value=(True, "email.send"))

    async def fake_create_approval(db, **kwargs):
        return SimpleNamespace(approval_id="apr_auto")

    thread_id = "t-auto-risky"
    mw = _gate(
        permission_mode="auto",
        resolve_capability=resolve_capability,
        assess_risk=assess_risk,
        db_factory=_persist_db_factory(),
        thread_id=thread_id,
    )
    agent = create_deep_agent(
        model=_ScriptedModel(),
        tools=[echo],
        middleware=[mw],
        checkpointer=MemorySaver(),
        system_prompt="t",
    )
    cfg = {"configurable": {"thread_id": thread_id}}

    with patch(f"{TRUST_GATE_MODULE}.create_approval", side_effect=fake_create_approval):
        items = await _drive(
            agent,
            {"messages": [{"role": "user", "content": "go"}]},
            cfg,
            durability="sync",
        )
        intr = _find_interrupt(items)
        assert intr is not None, "auto mode must pause on a risky write"
        assert intr.value["approval_id"] == "apr_auto"
        assert intr.value["risk_level"] == "high"
        assert calls == []
        assess_risk.assert_awaited()

        await _drive(agent, Command(resume="approve"), cfg)

    assert calls == ["hello"]
    assert await _final_ai_texts(agent, cfg)


async def test_reject_default_reason_blocks_and_quotes_default():
    """Command(resume='reject') on a first-pass approval (no persisted decision_reason)
    must NOT run the tool and yields a rejection ToolMessage carrying the clear default
    reason (a bare {'rejected': true} would make a real model confabulate)."""
    calls: list[str] = []
    echo = _make_echo(calls)

    assess_risk = AsyncMock(return_value=HIGH_ALL)
    resolve_capability = AsyncMock(return_value=(True, "email.send"))

    async def fake_create_approval(db, **kwargs):
        return SimpleNamespace(approval_id="apr_rej")

    thread_id = "t-reject-default"
    mw = _gate(
        permission_mode="auto",
        resolve_capability=resolve_capability,
        assess_risk=assess_risk,
        db_factory=_persist_db_factory(),  # existing=None → re-fetch also None → default reason
        thread_id=thread_id,
    )
    agent = create_deep_agent(
        model=_ScriptedModel(),
        tools=[echo],
        middleware=[mw],
        checkpointer=MemorySaver(),
        system_prompt="t",
    )
    cfg = {"configurable": {"thread_id": thread_id}}

    with patch(f"{TRUST_GATE_MODULE}.create_approval", side_effect=fake_create_approval):
        items = await _drive(
            agent,
            {"messages": [{"role": "user", "content": "go"}]},
            cfg,
            durability="sync",
        )
        assert _find_interrupt(items) is not None
        assert calls == []

        await _drive(agent, Command(resume="reject"), cfg)

    assert calls == []
    errs = await _error_tool_messages(agent, cfg)
    assert errs, "a rejection ToolMessage must be recorded"
    payloads = [json.loads(m.content) for m in errs]
    assert any(p.get("rejected") is True for p in payloads)
    # The reason must be a NON-EMPTY quotable string (the default), never a bare flag.
    assert any(isinstance(p.get("error"), str) and p["error"] for p in payloads)
    assert any("declined" in p.get("error", "") for p in payloads)


async def test_reject_replay_quotes_persisted_decision_reason():
    """When the Approval already exists (the CF-2 replay short-circuit), the gate skips
    re-assessment/persist and, on reject, quotes the persisted ``decision_reason`` verbatim
    so the model can relay the user's actual words."""
    calls: list[str] = []
    echo = _make_echo(calls)

    assess_risk = AsyncMock(name="assess_risk")  # CF-2 skips assessment → never awaited
    resolve_capability = AsyncMock(return_value=(True, "email.send"))
    create_approval_mock = AsyncMock()  # CF-2 skips persist → never called

    existing = SimpleNamespace(
        approval_id="apr_existing",
        artifact_refs={"capability": "email.send"},
        risk_level="high",
        decision_reason="Do not email investors without my review.",
    )

    thread_id = "t-reject-replay"
    mw = _gate(
        permission_mode="ask",
        resolve_capability=resolve_capability,
        assess_risk=assess_risk,
        db_factory=_persist_db_factory(existing=existing),
        thread_id=thread_id,
    )
    agent = create_deep_agent(
        model=_ScriptedModel(),
        tools=[echo],
        middleware=[mw],
        checkpointer=MemorySaver(),
        system_prompt="t",
    )
    cfg = {"configurable": {"thread_id": thread_id}}

    with patch(f"{TRUST_GATE_MODULE}.create_approval", create_approval_mock):
        items = await _drive(
            agent,
            {"messages": [{"role": "user", "content": "go"}]},
            cfg,
            durability="sync",
        )
        intr = _find_interrupt(items)
        assert intr is not None
        assert intr.value["approval_id"] == "apr_existing"
        assert calls == []

        await _drive(agent, Command(resume="reject"), cfg)

    assert calls == []
    assess_risk.assert_not_awaited()
    create_approval_mock.assert_not_called()
    errs = await _error_tool_messages(agent, cfg)
    payloads = [json.loads(m.content) for m in errs]
    assert any(p.get("error") == "Do not email investors without my review." for p in payloads)


# ── M3: replay-safe idempotent persist (mock) ────────────────────────────────


async def test_persist_is_idempotent_reuses_existing():
    """When an Approval already exists for (workspace_id, thread_id, tool_call_id), the
    persist step reuses its id and does NOT create a duplicate — the replay-safe
    get-or-create (no status filter)."""
    create_approval_mock = AsyncMock()
    existing = SimpleNamespace(approval_id="apr_existing")

    with patch(f"{TRUST_GATE_MODULE}.create_approval", create_approval_mock):
        approval_id = await _persist_permission_approval(
            name="echo",
            capability="email.send",
            assessment=HIGH_ALL,
            risk_level="high",
            workspace_id=WORKSPACE_ID,
            user_id=USER_ID,
            thread_id=THREAD_ID,
            tool_call_id="call_echo",
            agent_name="lead",
            db_factory=_persist_db_factory(existing=existing),
            context_block="",
            permission_mode="auto",
            lead_scope=frozenset({"email.send"}),
        )

    assert approval_id == "apr_existing"
    create_approval_mock.assert_not_called()


async def test_persist_reselects_on_integrity_error():
    """SELECT-miss → create → commit raises IntegrityError (lost the race) → rollback →
    re-SELECT finds the winner's row → return ITS id, no duplicate."""
    from sqlalchemy.exc import IntegrityError

    winner = SimpleNamespace(approval_id="apr_winner")

    @asynccontextmanager
    async def _factory():
        db = MagicMock(name="race-db")
        miss = MagicMock()
        miss.scalars.return_value.first.return_value = None
        found = MagicMock()
        found.scalars.return_value.first.return_value = winner
        db.execute = AsyncMock(side_effect=[miss, found])
        db.commit = AsyncMock(side_effect=IntegrityError("dup", None, Exception("dup")))
        db.rollback = AsyncMock()
        db.add = MagicMock()
        yield db

    create_approval_mock = AsyncMock(return_value=SimpleNamespace(approval_id="apr_loser"))
    with patch(f"{TRUST_GATE_MODULE}.create_approval", create_approval_mock):
        approval_id = await _persist_permission_approval(
            name="echo",
            capability="email.send",
            assessment=HIGH_ALL,
            risk_level="high",
            workspace_id=WORKSPACE_ID,
            user_id=USER_ID,
            thread_id=THREAD_ID,
            tool_call_id="call_echo",
            agent_name="lead",
            db_factory=_factory,
            context_block="",
            permission_mode="auto",
            lead_scope=frozenset({"email.send"}),
        )

    assert approval_id == "apr_winner"
    create_approval_mock.assert_awaited_once()


async def test_persist_ask_mode_defaults_reversible_and_blast_radius():
    """In ask mode the assessment is None; persisted artifact_refs must default reversible=True
    and blast_radius='self', and carry the chat-permission provenance."""
    captured: dict = {}

    async def fake_create_approval(db, **kwargs):
        captured.update(kwargs)
        return SimpleNamespace(approval_id="apr_ask")

    with patch(f"{TRUST_GATE_MODULE}.create_approval", side_effect=fake_create_approval):
        approval_id = await _persist_permission_approval(
            name="echo",
            capability="email.send",
            assessment=None,
            risk_level="n/a",
            workspace_id=WORKSPACE_ID,
            user_id=USER_ID,
            thread_id=THREAD_ID,
            tool_call_id="call_echo",
            agent_name="lead",
            db_factory=_persist_db_factory(),
            context_block="X" * 20000,
            permission_mode="ask",
            lead_scope=frozenset({"calendar.create", "email.send"}),
        )

    assert approval_id == "apr_ask"
    refs = captured["artifact_refs"]
    assert refs["reversible"] is True
    assert refs["blast_radius"] == "self"
    assert refs["permission_mode"] == "ask"
    assert refs["chat"] is True
    assert refs["lead_scope"] == ["calendar.create", "email.send"]
    # context_block is capped (bounded artifact row).
    assert len(refs["context_block"]) <= 8000
    assert captured["risk_level"] == "n/a"


# ── M3: replay-safe idempotent persist (real Postgres) ───────────────────────


def _db_reachable() -> bool:
    """Best-effort raw connect to Postgres to decide whether to skip (mirrors the DB gate
    tests). Own throwaway loop, never touching the app's process-wide cached engine."""
    import asyncpg

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
    except Exception:  # pragma: no cover - environment-dependent
        return False


@asynccontextmanager
async def _persist_env():
    """Yield ``(factory, user_id, workspace_id)`` with FK parents seeded; teardown deletes
    Approvals then the Workspace + User and disposes the engine (own loop)."""
    from sqlalchemy import delete
    from sqlalchemy.ext.asyncio import async_sessionmaker, create_async_engine
    from sqlalchemy.pool import NullPool
    from ulid import ULID

    from src.config.settings import get_settings
    from src.models.approvals import Approval
    from src.models.users import User, Workspace

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
                    email=f"perm-gate-{suffix}@example.com",
                    display_name="perm-gate-test",
                )
            )
            db.add(Workspace(workspace_id=workspace_id, name="perm-ws", owner_user_id=user_id))
            await db.commit()
        yield factory, user_id, workspace_id
    finally:
        try:
            async with factory() as db:
                await db.execute(delete(Approval).where(Approval.workspace_id == workspace_id))
                await db.execute(delete(Workspace).where(Workspace.workspace_id == workspace_id))
                await db.execute(delete(User).where(User.user_id == user_id))
                await db.commit()
        except Exception:  # pragma: no cover - teardown best-effort
            pass
        await engine.dispose()


@pytest.mark.skipif(not _db_reachable(), reason="Postgres not reachable")
async def test_persist_twice_yields_exactly_one_row_real_db():
    """MANDATORY (M3): the gate body replays on resume, so the persist runs TWICE for the
    same (workspace_id, thread_id, tool_call_id). Against real Postgres — fenced by the
    partial-unique ``uq_approvals_thread_tool_call`` index — this must leave EXACTLY ONE
    Approval row, and both calls must return the SAME approval_id."""
    from sqlalchemy import func, select

    from src.models.approvals import Approval

    async with _persist_env() as (factory, user_id, workspace_id):
        thread_id = "chat_thread_realdb"
        tool_call_id = "call_realdb_1"

        async def _run() -> str:
            return await _persist_permission_approval(
                name="send_email",
                capability="email.send",
                assessment=HIGH_ALL,
                risk_level="high",
                workspace_id=workspace_id,
                user_id=user_id,
                thread_id=thread_id,
                tool_call_id=tool_call_id,
                agent_name="lead",
                db_factory=factory,
                context_block="",
                permission_mode="auto",
                lead_scope=frozenset({"email.send"}),
            )

        # The replay: run the persist body twice, then a third time for good measure.
        id1 = await _run()
        id2 = await _run()
        id3 = await _run()

        assert id1 == id2 == id3, "every replay must return the same approval_id"

        async with factory() as db:
            count = await db.scalar(
                select(func.count())
                .select_from(Approval)
                .where(
                    Approval.workspace_id == workspace_id,
                    Approval.thread_id == thread_id,
                    Approval.tool_call_id == tool_call_id,
                )
            )
        assert count == 1, f"exactly one Approval row must exist, found {count}"
