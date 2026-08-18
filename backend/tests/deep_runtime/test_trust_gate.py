"""Step 6B: trust_gate middleware — THE ONE approval gate on the deep chat runtime.

The gate is a ``wrap_tool_call`` interceptor placed BETWEEN capability_scope (outer)
and muldro_tool_dispatcher (inner). By the time it runs, capability_scope has already
authorized the tool, so the gate never re-checks scope.

Two flavours of test:

* Non-interrupt branches (short-circuit / builtin / read / auto-execute) are driven
  DIRECTLY via ``mw.awrap_tool_call(request, handler)`` — no graph runtime needed
  (``interrupt()`` is never reached on those paths). This mirrors
  ``test_capability_scope.py`` / ``test_muldro_tool_dispatcher.py``.

* Interrupt branches (irreversible-write approve / reject) require the LangGraph
  runtime, so they build a real ``create_deep_agent`` with a fake scripted streaming
  model + a real ``echo`` tool (same pattern as
  ``spikes/deep_stream/interrupt_resume_stream_proof.py``). The interrupt surfaces as
  an ``("updates", {"__interrupt__": (Interrupt(value=...),)})`` stream item — it does
  NOT raise — and is resumed with ``Command(resume=...)``.

No live Anthropic API, no real DB — everything is faked/patched.
"""

from __future__ import annotations

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

from src.deep_runtime.middleware.approval_persistence import _find_existing_approval
from src.deep_runtime.middleware.trust_gate import (
    _decide_and_maybe_persist,
    make_trust_gate_middleware,
)
from src.services.risk_assessor import RiskAssessment

MODULE = "src.deep_runtime.middleware.trust_gate"
# ``create_approval`` executes inside the shared ``approval_persistence._get_or_create_approval``
# helper (A2 dedup), so patch it in its DEFINING module, not ``trust_gate``.
APPROVAL_PERSISTENCE_MODULE = "src.deep_runtime.middleware.approval_persistence"
USER_ID = "u_test"
WORKSPACE_ID = "ws_test"
THREAD_ID = "chat_thread_1"


# ── shared test doubles ──────────────────────────────────────────────────────


def _request(tool_name: str, args: dict | None = None, call_id: str = "call_123"):
    """Minimal ToolCallRequest stand-in: only ``.tool_call`` is read."""
    return SimpleNamespace(tool_call={"name": tool_name, "args": args or {}, "id": call_id})


def _hook(mw):
    """Extract the async wrap-tool-call hook bound on the middleware instance."""
    return mw.awrap_tool_call


def _sentinel_db_factory():
    """A db_factory whose yielded session is never really used (ToolRegistry patched)."""

    @asynccontextmanager
    async def _factory():
        yield SimpleNamespace(name="fake-db")

    return _factory


def _persist_db_factory(existing=None):
    """A db_factory whose session backs the decide/persist block.

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
    """A real side-effecting tool that records each invocation into *calls*."""

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
    """Return the first ``Interrupt`` object from any ``updates`` stream item."""
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
    authorization_source: str,
    db_factory,
    assess_risk,
    thread_id: str = THREAD_ID,
    presence: str = "present",
):
    """Build the gate under test.

    ``presence`` defaults to ``"present"`` HERE, unlike the factory's own fail-safe
    ``"absent"`` default: every test in this file below the PREPARE section exercises the
    LIVE approval path (a human is answering), and spelling that out in one place keeps
    those bodies byte-identical to before presence branched. The PREPARE tests pass
    ``presence="absent"`` explicitly.
    """
    return make_trust_gate_middleware(
        authorization_source=authorization_source,
        workspace_id=WORKSPACE_ID,
        user_id=USER_ID,
        thread_id=thread_id,
        agent_name="executor",
        db_factory=db_factory,
        assess_risk=assess_risk,
        presence=presence,
    )


@pytest.fixture
def handler():
    h = AsyncMock(name="handler")
    h.return_value = ToolMessage(content="executed", tool_call_id="call_123")
    return h


# ── Test 1: direct_user_request short-circuits — NO db, NO risk ──────────────


async def test_direct_user_request_short_circuits(handler):
    """The DORMANT path: a direct chat turn executes ungated with zero gate work."""
    assess_risk = AsyncMock(name="assess_risk")
    db_factory = MagicMock(name="db_factory")  # plain Mock — must never be called

    mw = _gate(
        authorization_source="direct_user_request",
        db_factory=db_factory,
        assess_risk=assess_risk,
    )
    result = await _hook(mw)(_request("send_email", {"to": "x"}, "c1"), handler)

    handler.assert_awaited_once()
    assert result is handler.return_value
    assess_risk.assert_not_awaited()
    db_factory.assert_not_called()


# ── Test 2: deepagents built-in falls through — NO db, NO risk ───────────────


async def test_builtin_falls_through(handler):
    """A deepagents built-in (write_todos) falls through even on a gated source."""
    assess_risk = AsyncMock(name="assess_risk")
    db_factory = MagicMock(name="db_factory")

    mw = _gate(authorization_source="autonomous", db_factory=db_factory, assess_risk=assess_risk)
    result = await _hook(mw)(_request("write_todos", {"todos": []}, "c1"), handler)

    handler.assert_awaited_once()
    assert result is handler.return_value
    db_factory.assert_not_called()
    assess_risk.assert_not_awaited()


# ── Test 3: gated READ capability falls through before risk assessment ───────


async def test_gated_read_capability_falls_through(handler):
    """A gated tool that resolves to a READ capability executes without risk/approval."""
    assess_risk = AsyncMock(name="assess_risk")

    tool_obj = SimpleNamespace(capability="email.read")
    registry = AsyncMock()
    registry.get_tool = AsyncMock(return_value=tool_obj)

    mw = _gate(
        authorization_source="autonomous",
        db_factory=_sentinel_db_factory(),
        assess_risk=assess_risk,
    )
    with patch(f"{MODULE}.ToolRegistry", return_value=registry):
        result = await _hook(mw)(_request("read_email", {}, "c1"), handler)

    handler.assert_awaited_once()
    assert result is handler.return_value
    assess_risk.assert_not_awaited()
    registry.get_tool.assert_awaited_once_with("read_email")


# ── Test 4: gated IRREVERSIBLE write forces interrupt; approve executes once ──


async def test_gated_irreversible_write_forces_interrupt_then_approve_executes():
    """email.send is irreversible → approval forced EVEN THOUGH the trust matrix said
    auto_execute_silent; the tool runs only after Command(resume='approve')."""
    calls: list[str] = []
    echo = _make_echo(calls)

    assess_risk = AsyncMock(
        return_value=RiskAssessment(
            risk_level="high",
            reasoning="sends external email",
            reversible=False,
            blast_radius="external_single",
        )
    )
    captured: dict = {}

    async def fake_create_approval(db, **kwargs):
        captured.update(kwargs)
        return SimpleNamespace(approval_id="apr_test")

    fake_te = MagicMock()
    # trust matrix alone would AUTO-EXECUTE — the irreversible override must win.
    fake_te.evaluate = AsyncMock(
        return_value=SimpleNamespace(decision="auto_execute_silent", justification="matrix says go")
    )

    thread_id = "t-approve-4"
    mw = _gate(
        authorization_source="autonomous",
        db_factory=_persist_db_factory(),
        assess_risk=assess_risk,
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

    with (
        patch(f"{MODULE}._resolve_capability", AsyncMock(return_value=(True, "email.send"))),
        patch(f"{MODULE}.TrustEngine", return_value=fake_te),
        patch(f"{APPROVAL_PERSISTENCE_MODULE}.create_approval", side_effect=fake_create_approval),
    ):
        items = await _drive(
            agent,
            {"messages": [{"role": "user", "content": "go"}]},
            cfg,
            durability="sync",
        )
        intr = _find_interrupt(items)
        assert intr is not None, "turn-1 must pause on an __interrupt__ update"
        assert intr.value["approval_id"] == "apr_test"
        assert intr.value["thread_id"] == thread_id
        assert intr.value["capability"] == "email.send"
        assert intr.value["risk_level"] == "high"
        # The underlying tool must NOT have run while paused.
        assert calls == []

        # artifact_refs on the persisted approval carry the idempotency key + provenance.
        refs = captured["artifact_refs"]
        assert refs["thread_id"] == thread_id
        assert refs["tool_call_id"] == "call_echo"
        assert refs["tool_name"] == "echo"
        assert refs["capability"] == "email.send"
        assert captured["approval_type"] == "tool:echo"

        await _drive(agent, Command(resume="approve"), cfg)

    # After approve the tool runs exactly once and a final AI message is produced.
    assert calls == ["hello"]
    assert await _final_ai_texts(agent, cfg)


# ── Test 5: reject blocks — tool never runs, rejection ToolMessage produced ───


async def test_gated_write_reject_blocks():
    """Command(resume='reject') must NOT run the tool and yields a rejection ToolMessage."""
    calls: list[str] = []
    echo = _make_echo(calls)

    assess_risk = AsyncMock(
        return_value=RiskAssessment(
            risk_level="high",
            reasoning="sends external email",
            reversible=False,
            blast_radius="external_single",
        )
    )

    async def fake_create_approval(db, **kwargs):
        return SimpleNamespace(approval_id="apr_test")

    fake_te = MagicMock()
    fake_te.evaluate = AsyncMock(
        return_value=SimpleNamespace(decision="approval_required", justification="risky")
    )

    thread_id = "t-reject-5"
    mw = _gate(
        authorization_source="autonomous",
        db_factory=_persist_db_factory(),
        assess_risk=assess_risk,
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

    with (
        patch(f"{MODULE}._resolve_capability", AsyncMock(return_value=(True, "email.send"))),
        patch(f"{MODULE}.TrustEngine", return_value=fake_te),
        patch(f"{APPROVAL_PERSISTENCE_MODULE}.create_approval", side_effect=fake_create_approval),
    ):
        items = await _drive(
            agent,
            {"messages": [{"role": "user", "content": "go"}]},
            cfg,
            durability="sync",
        )
        assert _find_interrupt(items) is not None
        assert calls == []

        await _drive(agent, Command(resume="reject"), cfg)

    # Tool must NOT have run, and a rejection ToolMessage(status="error") is present.
    assert calls == []
    errs = await _error_tool_messages(agent, cfg)
    assert errs, "a rejection ToolMessage must be recorded"
    assert any(json.loads(m.content).get("rejected") is True for m in errs)


# ── Test 6: reversible-internal write auto-executes (no interrupt, no approval) ─


async def test_auto_execute_when_trusted_and_reversible(handler):
    """A reversible-internal write (email.draft) with a matrix auto-execute decision runs
    inline — no irreversible override, no interrupt, no approval persisted."""
    assess_risk = AsyncMock(
        return_value=RiskAssessment(
            risk_level="low", reasoning="local draft", reversible=True, blast_radius="internal"
        )
    )
    fake_te = MagicMock()
    fake_te.evaluate = AsyncMock(
        return_value=SimpleNamespace(decision="auto_execute_silent", justification="trusted")
    )
    create_approval_mock = AsyncMock()

    mw = _gate(
        authorization_source="autonomous",
        db_factory=_persist_db_factory(),
        assess_risk=assess_risk,
    )
    with (
        patch(f"{MODULE}._resolve_capability", AsyncMock(return_value=(True, "email.draft"))),
        patch(f"{MODULE}.TrustEngine", return_value=fake_te),
        patch(f"{APPROVAL_PERSISTENCE_MODULE}.create_approval", create_approval_mock),
    ):
        result = await _hook(mw)(_request("draft_email", {}, "c1"), handler)

    handler.assert_awaited_once()
    assert result is handler.return_value
    assess_risk.assert_awaited_once()
    create_approval_mock.assert_not_called()


# ── Test 7: idempotent get-or-create reuses an existing approval (CRITICAL FACT 2) ─


async def test_approval_persistence_is_idempotent_reuses_existing():
    """When an approval row already exists for (workspace_id, thread_id, tool_call_id),
    the decide/persist step reuses its id and does NOT create a duplicate — proving the
    replay-safe get-or-create (no status filter)."""
    risk = RiskAssessment(
        risk_level="high", reasoning="x", reversible=False, blast_radius="external_single"
    )
    fake_te = MagicMock()
    fake_te.evaluate = AsyncMock(
        return_value=SimpleNamespace(decision="auto_execute_silent", justification="j")
    )
    create_approval_mock = AsyncMock()
    existing = SimpleNamespace(approval_id="apr_existing")

    with (
        patch(f"{MODULE}.TrustEngine", return_value=fake_te),
        patch(f"{APPROVAL_PERSISTENCE_MODULE}.create_approval", create_approval_mock),
    ):
        require_approval, approval_id = await _decide_and_maybe_persist(
            name="echo",
            capability="email.send",
            risk=risk,
            workspace_id=WORKSPACE_ID,
            user_id=USER_ID,
            thread_id=THREAD_ID,
            tool_call_id="call_echo",
            agent_name="executor",
            db_factory=_persist_db_factory(existing=existing),
        )

    assert require_approval is True
    assert approval_id == "apr_existing"
    create_approval_mock.assert_not_called()


# ── Test 8: capability-lookup error fails CLOSED (blocks, no ungated execution) ─


async def test_capability_lookup_error_fails_closed(handler):
    """A capability-lookup ERROR on a gated write must fail CLOSED: the gate returns a
    ToolMessage(status='error', blocked=True) and never runs the tool or assesses risk —
    it must NOT fall through to ungated execution (mirrors capability_scope's deny)."""
    assess_risk = AsyncMock(name="assess_risk")

    mw = _gate(
        authorization_source="autonomous",
        db_factory=_sentinel_db_factory(),
        assess_risk=assess_risk,
    )
    # ToolRegistry construction raises → _resolve_capability's except → (False, None).
    with patch(f"{MODULE}.ToolRegistry", side_effect=RuntimeError("db down")):
        result = await _hook(mw)(_request("send_email", {}, "c1"), handler)

    handler.assert_not_awaited()
    assess_risk.assert_not_awaited()
    assert isinstance(result, ToolMessage)
    assert result.status == "error"
    assert result.tool_call_id == "c1"
    payload = json.loads(result.content)
    assert payload.get("blocked") is True


# ── Test 9: an unexpected PolicyDecision value fails CLOSED to approval ───────


async def test_unexpected_decision_requires_approval_fail_closed():
    """A reversible-internal write (no irreversible override) whose trust decision is an
    unexpected value ('blocked') must still require approval — the auto-execute allowlist
    fails closed (only the two explicit auto-execute verdicts skip approval)."""
    risk = RiskAssessment(risk_level="low", reasoning="x", reversible=True, blast_radius="internal")
    fake_te = MagicMock()
    fake_te.evaluate = AsyncMock(
        return_value=SimpleNamespace(decision="blocked", justification="j")
    )
    create_approval_mock = AsyncMock(return_value=SimpleNamespace(approval_id="apr_new"))

    with (
        patch(f"{MODULE}.TrustEngine", return_value=fake_te),
        patch(f"{APPROVAL_PERSISTENCE_MODULE}.create_approval", create_approval_mock),
    ):
        require_approval, approval_id = await _decide_and_maybe_persist(
            name="draft_email",
            capability="email.draft",
            risk=risk,
            workspace_id=WORKSPACE_ID,
            user_id=USER_ID,
            thread_id=THREAD_ID,
            tool_call_id="call_x",
            agent_name="executor",
            db_factory=_persist_db_factory(),
        )

    assert require_approval is True
    assert approval_id == "apr_new"
    create_approval_mock.assert_awaited_once()


# ── Test 10: lost the create race → IntegrityError → rollback → re-SELECT finds winner ─


async def test_get_or_create_reselects_on_integrity_error():
    """SELECT-miss -> create -> commit raises IntegrityError (lost the race) -> rollback ->
    re-SELECT finds the row the winner committed -> return ITS id, no duplicate."""
    from sqlalchemy.exc import IntegrityError

    risk = RiskAssessment(
        risk_level="high", reasoning="x", reversible=False, blast_radius="external_single"
    )
    fake_te = MagicMock()
    fake_te.evaluate = AsyncMock(
        return_value=SimpleNamespace(decision="auto_execute_silent", justification="j")
    )
    winner = SimpleNamespace(approval_id="apr_winner")

    @asynccontextmanager
    async def _factory():
        db = MagicMock(name="race-db")
        miss = MagicMock()
        miss.scalars.return_value.first.return_value = None
        found = MagicMock()
        found.scalars.return_value.first.return_value = winner
        db.execute = AsyncMock(side_effect=[miss, found])  # 1st SELECT miss, re-SELECT found
        db.commit = AsyncMock(side_effect=IntegrityError("dup", None, Exception("dup")))
        db.rollback = AsyncMock()
        db.add = MagicMock()
        yield db

    create_approval_mock = AsyncMock(return_value=SimpleNamespace(approval_id="apr_loser"))
    with (
        patch(f"{MODULE}.TrustEngine", return_value=fake_te),
        patch(f"{APPROVAL_PERSISTENCE_MODULE}.create_approval", create_approval_mock),
    ):
        require_approval, approval_id = await _decide_and_maybe_persist(
            name="echo",
            capability="email.send",
            risk=risk,
            workspace_id=WORKSPACE_ID,
            user_id=USER_ID,
            thread_id=THREAD_ID,
            tool_call_id="call_echo",
            agent_name="executor",
            db_factory=_factory,
        )
    assert require_approval is True
    assert approval_id == "apr_winner"  # the committed winner, NOT apr_loser
    create_approval_mock.assert_awaited_once()


# ── Test 11: CF-2 _find_existing_approval reads the persisted row by columns ──────


async def test_find_existing_approval_returns_matching_row():
    """CF-2: ``_find_existing_approval`` returns the Approval keyed on the promoted COLUMNS
    (workspace_id, thread_id, tool_call_id) — the replay-detection SELECT."""
    existing = SimpleNamespace(approval_id="apr_replay", risk_level="high", artifact_refs={})
    result = await _find_existing_approval(
        WORKSPACE_ID, THREAD_ID, "call_echo", _persist_db_factory(existing=existing)
    )
    assert result is existing


async def test_find_existing_approval_returns_none_when_absent():
    """On the FIRST pass (no prior row) it returns None, so the gate falls through to
    assess + persist — the CF-2 skip only fires on the replay."""
    result = await _find_existing_approval(
        WORKSPACE_ID, THREAD_ID, "call_echo", _persist_db_factory(existing=None)
    )
    assert result is None


# ── PREPARE: a CONFIRM verdict with nobody on the turn stages the write ──────


def _exploding_interrupt(payload):
    """A stand-in for ``interrupt()`` that makes an accidental suspend LOUD.

    An autonomous turn has nobody to answer, so reaching ``interrupt()`` at all is the exact
    failure this branch exists to prevent — in production it is a run that hangs forever,
    which a silent mock would hide.
    """
    raise AssertionError(f"interrupt() must NOT be called on an absent turn: {payload!r}")


def _approval_required_engine():
    te = MagicMock()
    te.evaluate = AsyncMock(
        return_value=SimpleNamespace(decision="approval_required", justification="risky")
    )
    return te


_PREPARE_RISK = RiskAssessment(
    risk_level="high",
    reasoning="sends external email",
    reversible=False,
    blast_radius="external_single",
)


async def test_an_absent_turn_prepares_instead_of_interrupting(handler):
    """An autonomous, unattended turn whose trust verdict demands approval: the Approval is
    recorded and the gate returns a SUCCESS ToolMessage naming it — the tool never runs and
    the run never suspends."""
    assess_risk = AsyncMock(return_value=_PREPARE_RISK)

    async def fake_create_approval(db, **kwargs):
        return SimpleNamespace(approval_id="apr_prepared")

    mw = _gate(
        authorization_source="autonomous",
        db_factory=_persist_db_factory(),
        assess_risk=assess_risk,
        presence="absent",
    )
    with (
        patch(f"{MODULE}._resolve_capability", AsyncMock(return_value=(True, "email.send"))),
        patch(f"{MODULE}.TrustEngine", return_value=_approval_required_engine()),
        patch(f"{MODULE}.interrupt", _exploding_interrupt),
        patch(f"{APPROVAL_PERSISTENCE_MODULE}.create_approval", side_effect=fake_create_approval),
    ):
        result = await _hook(mw)(_request("echo", {"text": "hi"}, "c1"), handler)

    handler.assert_not_awaited()
    assert isinstance(result, ToolMessage)
    # LOAD-BEARING: "error" would map to the frozen `blocked` SSE frame and stop the lead.
    assert result.status == "success"
    assert result.tool_call_id == "c1"
    payload = json.loads(result.content)
    assert payload["prepared"] is True
    assert payload["approval_id"] == "apr_prepared"
    assert payload["capability"] == "email.send"


async def test_a_present_turn_still_interrupts(handler):
    """The UNCHANGED live path: a present turn suspends, and an approve verdict reaches the
    handler. This is the regression guard for the whole pre-existing approval feature."""
    assess_risk = AsyncMock(return_value=_PREPARE_RISK)
    seen: list[dict] = []

    def fake_interrupt(payload):
        seen.append(payload)
        return "approve"

    async def fake_create_approval(db, **kwargs):
        return SimpleNamespace(approval_id="apr_live")

    mw = _gate(
        authorization_source="autonomous",
        db_factory=_persist_db_factory(),
        assess_risk=assess_risk,
        presence="present",
    )
    with (
        patch(f"{MODULE}._resolve_capability", AsyncMock(return_value=(True, "email.send"))),
        patch(f"{MODULE}.TrustEngine", return_value=_approval_required_engine()),
        patch(f"{MODULE}.interrupt", fake_interrupt),
        patch(f"{APPROVAL_PERSISTENCE_MODULE}.create_approval", side_effect=fake_create_approval),
    ):
        result = await _hook(mw)(_request("echo", {"text": "hi"}, "c1"), handler)

    assert seen, "a present turn MUST still call interrupt()"
    assert seen[0]["approval_id"] == "apr_live"
    handler.assert_awaited_once()
    assert result is handler.return_value


async def test_a_prepared_write_does_not_stop_the_turn(handler):
    """A prepared write is STAGED, not failed: the very next tool call in the same turn still
    reaches the dispatcher. This is what ``status="success"`` buys."""
    caps = {"echo": "email.send", "read_email": "email.read"}

    async def _resolve(name, workspace_id, db_factory):
        return (True, caps[name])

    async def fake_create_approval(db, **kwargs):
        return SimpleNamespace(approval_id="apr_prepared")

    mw = _gate(
        authorization_source="autonomous",
        db_factory=_persist_db_factory(),
        assess_risk=AsyncMock(return_value=_PREPARE_RISK),
        presence="absent",
    )
    with (
        patch(f"{MODULE}._resolve_capability", _resolve),
        patch(f"{MODULE}.TrustEngine", return_value=_approval_required_engine()),
        patch(f"{MODULE}.interrupt", _exploding_interrupt),
        patch(f"{APPROVAL_PERSISTENCE_MODULE}.create_approval", side_effect=fake_create_approval),
    ):
        staged = await _hook(mw)(_request("echo", {"text": "hi"}, "c1"), handler)
        handler.assert_not_awaited()
        followup = await _hook(mw)(_request("read_email", {}, "c2"), handler)

    assert json.loads(staged.content)["prepared"] is True
    handler.assert_awaited_once()
    assert followup is handler.return_value


async def test_a_prepared_approval_is_typed():
    """The PREPARED record: typed ``prepared_action`` with a TTL and flagged ``prepared``, with
    the acting agent's capability_scope snapshot intact (confirmation checks against THAT)."""
    captured: dict = {}

    async def fake_create_approval(db, **kwargs):
        captured.update(kwargs)
        return SimpleNamespace(approval_id="apr_prepared")

    with (
        patch(f"{MODULE}.TrustEngine", return_value=_approval_required_engine()),
        patch(f"{APPROVAL_PERSISTENCE_MODULE}.create_approval", side_effect=fake_create_approval),
    ):
        require_approval, approval_id = await _decide_and_maybe_persist(
            name="echo",
            capability="email.send",
            risk=_PREPARE_RISK,
            workspace_id=WORKSPACE_ID,
            user_id=USER_ID,
            thread_id=THREAD_ID,
            tool_call_id="call_echo",
            agent_name="executor",
            db_factory=_persist_db_factory(),
            agent_capability_scope=frozenset({"calendar.create", "email.send"}),
            presence="absent",
        )

    assert require_approval is True
    assert approval_id == "apr_prepared"
    assert captured["approval_type"] == "prepared_action"
    assert captured["expires_at"] is not None
    refs = captured["artifact_refs"]
    assert refs["prepared"] is True
    assert refs["presence"] == "absent"
    assert refs["capability_scope"] == ["calendar.create", "email.send"]


async def test_a_live_approval_keeps_its_tool_type():
    """The live (present) path is untouched: the approval keeps its ``tool:<name>`` type and
    default TTL, and no ``prepared`` marker appears."""
    captured: dict = {}

    async def fake_create_approval(db, **kwargs):
        captured.update(kwargs)
        return SimpleNamespace(approval_id="apr_live")

    with (
        patch(f"{MODULE}.TrustEngine", return_value=_approval_required_engine()),
        patch(f"{APPROVAL_PERSISTENCE_MODULE}.create_approval", side_effect=fake_create_approval),
    ):
        await _decide_and_maybe_persist(
            name="echo",
            capability="email.send",
            risk=_PREPARE_RISK,
            workspace_id=WORKSPACE_ID,
            user_id=USER_ID,
            thread_id=THREAD_ID,
            tool_call_id="call_echo",
            agent_name="executor",
            db_factory=_persist_db_factory(),
            agent_capability_scope=frozenset({"email.send"}),
            presence="present",
        )

    assert captured["approval_type"] == "tool:echo"
    assert captured["expires_at"] is None
    refs = captured["artifact_refs"]
    assert "prepared" not in refs
    assert refs["presence"] == "present"
