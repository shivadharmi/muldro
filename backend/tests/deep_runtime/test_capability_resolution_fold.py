"""Step 7B1 P2 (6C #1): the triple capability-resolution fold.

governor_audit + trust_gate + write_lock each need a per-tool registry classification.
BEFORE the fold each opened its OWN DB session and did its OWN ``ToolRegistry.get_tool`` —
THREE lookups + THREE sessions per gated write. This suite proves the fold: a SINGLE
per-turn memoized resolver (``_resolve_tool_def``, shared+cached in
``AgentInvoker._build_deep_agent_for``) is consumed by all three, so a tool is resolved
EXACTLY ONCE even though three middlewares classify it — while each middleware keeps its
OWN fail-on-lookup-error behavior:

* trust_gate  — FAILS CLOSED (blocks the gated write) on ``(False, None)``;
* write_lock  — FAILS OPEN (no lock, falls through) on ``(False, None)``;
* governor_audit — FAILS OPEN (allows + audits low-risk) on ``(False, None)``.

Two flavours of test:

* The count proof drives a REAL gated WRITE through the real
  ``AgentInvoker._build_deep_agent_for`` chain (scripted streaming model + fake dispatch),
  and spies on ``trust_gate.ToolRegistry.get_tool`` — asserting it fires EXACTLY ONCE for the
  single write even though governor_audit + trust_gate + write_lock all classify it. Reverting
  the invoker's memoization (the NEGATIVE CONTROL) makes this fire 3× and the assertion fail.

* The fail-behavior proof builds ONE shared resolver whose underlying ``_resolve_tool_def``
  ERRORS, then drives each of the three middlewares (each over its own projection of that same
  resolver) and asserts the three distinct behaviors — trust_gate blocks, write_lock does not
  lock, governor_audit allows. trust_gate's fail-closed short-circuits BEFORE write_lock (its
  inner) can run, so the three cannot be observed in one composed chain — hence per-middleware.

No live Anthropic API, no real DB — everything is faked/patched.
"""

from __future__ import annotations

import json
from contextlib import asynccontextmanager
from types import SimpleNamespace
from unittest.mock import AsyncMock, MagicMock, patch

from langchain_core.language_models.chat_models import BaseChatModel
from langchain_core.messages import AIMessage, AIMessageChunk, ToolMessage
from langchain_core.messages.tool import tool_call_chunk
from langchain_core.outputs import ChatGeneration, ChatGenerationChunk, ChatResult
from langgraph.checkpoint.memory import MemorySaver

from src.deep_runtime.middleware.governor_audit import make_governor_audit_middleware
from src.deep_runtime.middleware.trust_gate import _resolve_tool_def, make_trust_gate_middleware
from src.deep_runtime.middleware.write_lock import make_write_lock_middleware
from src.services.risk_assessor import RiskAssessment

TRUST_GATE_MODULE = "src.deep_runtime.middleware.trust_gate"
AGENT_BUILDER_MODULE = "src.deep_runtime.agent_builder"
CAP_SCOPE_MODULE = "src.deep_runtime.middleware.capability_scope"
RISK_MODULE = "src.services.risk_assessor"

AGENT_NAME = "executor"
WORKSPACE_ID = "ws_test"
USER_ID = "u_test"
THREAD_ID = "chat_fold_1"


# ── shared doubles ───────────────────────────────────────────────────────────


def _request(name: str, args: dict | None = None, call_id: str = "call_1"):
    """Minimal ToolCallRequest stand-in: only ``.tool_call`` is read."""
    return SimpleNamespace(tool_call={"name": name, "args": args or {}, "id": call_id})


def _fake_db_factory():
    """A db_factory whose yielded session is never really used (ToolRegistry patched)."""

    @asynccontextmanager
    async def _factory():
        yield SimpleNamespace(name="fake-db")

    return _factory


def _persist_db_factory(existing=None):
    """A db_factory whose session backs the approval find/persist block.

    ``.execute(...).scalars().first()`` resolves to *existing* (default ``None`` so the
    write auto-executes without a pending row); ``.commit`` is an AsyncMock.
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


class _FakeRedis:
    """Minimal Redis for ``acquire_write_lock``: records set (acquire) + eval (release)."""

    def __init__(self):
        self.calls: list = []

    async def set(self, k, v, nx=None, ex=None):  # noqa: ANN001
        self.calls.append(("set", k))
        return True

    async def eval(self, *a):  # noqa: ANN002
        self.calls.append(("eval",))
        return 1


# ═══════════════════════════════════════════════════════════════════════════════
# Count proof: a gated WRITE resolves its ToolDef EXACTLY ONCE across all three
# middlewares (real ``_build_deep_agent_for`` chain).
# ═══════════════════════════════════════════════════════════════════════════════

WRITE_TOOL_DEF = {
    "name": "draft_email",
    "description": "Draft an email (reversible-internal write).",
    "input_schema": {"type": "object", "properties": {"to": {"type": "string"}}},
}
WRITE_TOOL_ARGS = {"to": "founder@example.com"}


class _WriteScriptedModel(BaseChatModel):
    """Fake streaming model: turn 1 calls the write tool, a resumed turn answers."""

    @property
    def _llm_type(self) -> str:
        return "scripted-fake"

    def bind_tools(self, tools, **kwargs):  # noqa: ANN001, ANN003, ARG002
        return self

    def _script(self, messages):  # noqa: ANN001
        if any(isinstance(m, ToolMessage) for m in messages):
            return [AIMessageChunk(content=[{"type": "text", "text": "Drafted.", "index": 0}])]
        return [
            AIMessageChunk(
                content=[],
                tool_call_chunks=[
                    tool_call_chunk(
                        name="draft_email",
                        args=json.dumps(WRITE_TOOL_ARGS),
                        id="call_draft_1",
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


def _make_write_invoker(*, executed: list, redis):
    """A minimal real ``AgentInvoker`` wired for a gated auto-executing write.

    ``services.extras['redis']`` is the fake so write_lock engages (the real accessor the
    seam uses — ``_build_deep_agent_for`` sources redis from ``services.extras``, not a typed
    field); the db_factory backs the approval find (returns None → no pending row);
    ``execute_tool`` records what reaches the dispatcher.
    """
    from src.orchestrator.agent_invoker import AgentInvoker
    from src.orchestrator.agents import SubAgent
    from tests.conftest import make_mock_settings

    tool_executor = MagicMock()

    async def fake_execute(name, args, uid, ws):
        executed.append((name, args))
        return {"ok": True}

    tool_executor.execute_tool = fake_execute

    agent = SubAgent(
        name=AGENT_NAME, prompt="p", model_tier="sonnet", capability_scope={"email.draft"}
    )

    return AgentInvoker(
        settings=make_mock_settings(runtime="deep"),
        client=MagicMock(),
        services=SimpleNamespace(extras={"redis": redis}),
        budget=MagicMock(),
        circuit_breaker=MagicMock(),
        db_factory_provider=lambda: _persist_db_factory(),
        tool_executor=tool_executor,
        context=MagicMock(),
        agents={AGENT_NAME: agent},
        checkpointer_provider=lambda: MemorySaver(),
    )


async def _drive_gated_write(invoker, registry):
    """Build a deep agent via ``_build_deep_agent_for`` on a GATED (autonomous) source and
    stream a single write that AUTO-EXECUTES (trust matrix auto_execute + reversible-internal
    risk, so no interrupt). ``registry.get_tool`` is the spy the count assertion reads."""
    from src.deep_runtime.authorization import AuthorizationSource
    from src.deep_runtime.prompt_bridge import build_system_message
    from src.deep_runtime.stream_adapter import stream_deep_agent_events

    agent = invoker._agents[AGENT_NAME]
    thread_id = "chat_fold_write"

    fake_te = MagicMock()
    fake_te.evaluate = AsyncMock(
        return_value=SimpleNamespace(decision="auto_execute_silent", justification="trusted")
    )
    reversible_risk = RiskAssessment(
        risk_level="low", reasoning="local draft", reversible=True, blast_radius="internal"
    )

    with (
        patch(f"{AGENT_BUILDER_MODULE}.build_chat_model", return_value=_WriteScriptedModel()),
        patch(f"{CAP_SCOPE_MODULE}._is_in_scope", AsyncMock(return_value=True)),
        patch(f"{TRUST_GATE_MODULE}.ToolRegistry", return_value=registry),
        patch(f"{TRUST_GATE_MODULE}.TrustEngine", return_value=fake_te),
        patch(f"{RISK_MODULE}.get_or_assess_risk", AsyncMock(return_value=reversible_risk)),
    ):
        deep_agent = await invoker._build_deep_agent_for(
            agent,
            [WRITE_TOOL_DEF],
            user_id=USER_ID,
            workspace_id=WORKSPACE_ID,
            thread_id=thread_id,
            authorization_source=AuthorizationSource.AUTONOMOUS,
            system_prompt=build_system_message(invoker.build_system_prompt(agent, "")),
        )
        config = {"configurable": {"thread_id": thread_id}}
        frames = [
            f
            async for f in stream_deep_agent_events(
                deep_agent,
                {"messages": [{"role": "user", "content": "draft it"}]},
                config,
                agent_name=AGENT_NAME,
                model="claude-sonnet-5",
                durability="sync",
            )
        ]
    return frames


async def test_gated_write_resolves_tool_def_exactly_once():
    """LOAD-BEARING (the fold): a single gated write flows through governor_audit → trust_gate
    → write_lock, yet the underlying ``ToolRegistry.get_tool`` fires EXACTLY ONCE — the per-turn
    shared+memoized ``_resolve_tool_def`` serves all three consumers from one lookup / one
    session. Reverting the invoker's memoization (negative control) makes this 3."""
    executed: list = []
    redis = _FakeRedis()
    tool_def = SimpleNamespace(enabled=True, risk_level="low", capability="email.draft")
    registry = MagicMock(name="registry")
    registry.get_tool = AsyncMock(return_value=tool_def)

    invoker = _make_write_invoker(executed=executed, redis=redis)
    frames = await _drive_gated_write(invoker, registry)

    # The write auto-executed (no interrupt) and reached the central dispatcher exactly once.
    assert executed == [("draft_email", WRITE_TOOL_ARGS)], f"frames={frames}"
    assert not any(f.get("event") == "approval_needed" for f in frames), f"frames={frames}"

    # THE FOLD: three middlewares classified the SAME tool, but only ONE registry lookup ran.
    assert registry.get_tool.await_count == 1, (
        f"expected exactly ONE shared lookup for the gated write; "
        f"get_tool fired {registry.get_tool.await_count}× — the fold regressed"
    )

    # write_lock DID engage on the write (proof it consumed the shared resolver + saw a write).
    assert ("set", "lock:write:ws_test:email.draft") in redis.calls, f"redis={redis.calls}"
    assert ("eval",) in redis.calls, f"redis={redis.calls}"


# ═══════════════════════════════════════════════════════════════════════════════
# Fail-behavior proof: ONE shared resolver whose _resolve_tool_def ERRORS drives
# THREE distinct middleware behaviors — trust_gate CLOSED, write_lock/governor OPEN.
# ═══════════════════════════════════════════════════════════════════════════════


def _build_erroring_shared_resolver():
    """Replicate ``_build_deep_agent_for``'s shared cache over an ERRORING ``_resolve_tool_def``
    (its ``ToolRegistry`` construction raises → ``(False, None)``). Returns the three
    projections the invoker derives: ``(shared, gate_cap, resolve_cap)``."""
    db_factory = _fake_db_factory()
    cache: dict[str, tuple[bool, object]] = {}

    async def shared(name: str) -> tuple[bool, object]:
        if name not in cache:
            cache[name] = await _resolve_tool_def(name, WORKSPACE_ID, db_factory)
        return cache[name]

    async def gate_cap(name: str) -> tuple[bool, object]:  # trust_gate projection
        ok, td = await shared(name)
        return (ok, getattr(td, "capability", None) if td else None)

    async def resolve_cap(name: str):  # write_lock projection
        ok, td = await shared(name)
        return getattr(td, "capability", None) if (ok and td) else None

    return shared, gate_cap, resolve_cap


async def test_lookup_error_drives_three_distinct_fail_behaviors():
    """The SAME erroring shared resolver → three DIFFERENT fail policies, one per middleware.

    (a) trust_gate FAILS CLOSED  — a gated write is BLOCKED (never executes ungated);
    (b) write_lock FAILS OPEN    — no lock acquired, handler runs (best-effort fence);
    (c) governor_audit FAILS OPEN — tool allowed + audited (an audit hook never blocks).
    """
    shared, gate_cap, resolve_cap = _build_erroring_shared_resolver()

    async def handler(request):  # a passthrough downstream
        return ToolMessage(content="ran", tool_call_id=request.tool_call["id"], name="send_email")

    # The single failure point: ToolRegistry construction raises inside _resolve_tool_def.
    with patch(f"{TRUST_GATE_MODULE}.ToolRegistry", side_effect=RuntimeError("db down")):
        # (a) trust_gate — FAIL CLOSED. A gated source + lookup error must BLOCK the write.
        trust_gate = make_trust_gate_middleware(
            authorization_source="autonomous",
            workspace_id=WORKSPACE_ID,
            user_id=USER_ID,
            thread_id=THREAD_ID,
            agent_name=AGENT_NAME,
            db_factory=_persist_db_factory(),
            assess_risk=AsyncMock(name="assess_risk"),
            resolve_capability=gate_cap,
        )
        tg_handler = AsyncMock(name="tg_handler")
        tg_result = await trust_gate.awrap_tool_call(_request("send_email", {}, "c_a"), tg_handler)

        tg_handler.assert_not_awaited()  # never executed ungated
        assert isinstance(tg_result, ToolMessage)
        assert tg_result.status == "error"
        assert json.loads(tg_result.content).get("blocked") is True

        # (b) write_lock — FAIL OPEN. cap resolves to None → NO lock, handler runs.
        redis = _FakeRedis()
        write_lock = make_write_lock_middleware(
            workspace_id=WORKSPACE_ID, redis=redis, resolve_capability=resolve_cap
        )
        wl_result = await write_lock.awrap_tool_call(_request("send_email", {}, "c_b"), handler)

        assert wl_result.content == "ran"  # handler ran (fell through)
        assert redis.calls == []  # NEVER locked on a lookup error

        # (c) governor_audit — FAIL OPEN. (False, None) → not blocked → audited + handler runs.
        governor_audit = make_governor_audit_middleware(
            agent_name=AGENT_NAME, workspace_id=WORKSPACE_ID, resolve_tool_def=shared
        )
        ga_result = await governor_audit.awrap_tool_call(_request("send_email", {}, "c_c"), handler)

        assert ga_result.content == "ran"  # allowed through despite the lookup error


async def test_erroring_resolver_is_memoized_across_the_three():
    """The shared resolver memoizes even the ERROR result: three consumers of the SAME tool
    name trigger the underlying ``_resolve_tool_def`` (ToolRegistry) only ONCE."""
    shared, gate_cap, resolve_cap = _build_erroring_shared_resolver()

    registry_ctor = MagicMock(side_effect=RuntimeError("db down"))
    with patch(f"{TRUST_GATE_MODULE}.ToolRegistry", registry_ctor):
        r1 = await shared("send_email")
        r2 = await gate_cap("send_email")
        r3 = await resolve_cap("send_email")

    assert r1 == (False, None)
    assert r2 == (False, None)
    assert r3 is None
    # ToolRegistry constructed once → the errored lookup was cached, not repeated 3×.
    assert registry_ctor.call_count == 1
