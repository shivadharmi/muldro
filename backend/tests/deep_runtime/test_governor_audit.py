"""Step 7B1 P1: governor_audit middleware — the deep-path port of the legacy
``governor_pre_tool_hook`` (``src/orchestrator/hooks.py``).

Two flavours of test:

* Unit tests drive the interceptor DIRECTLY via ``mw.awrap_tool_call(request, handler)``
  (same pattern as ``test_jarvis_tool_dispatcher.py`` / ``test_trust_gate.py``) — no graph
  runtime, no real DB. The registry lookup is faked via a fake ``db_factory`` async CM whose
  ``ToolRegistry`` is patched at the module boundary.

* A forced-integration guard builds a REAL deep agent via
  ``AgentInvoker._build_deep_agent_for`` with a fake scripted streaming model that calls a
  DISABLED Jarvis tool, and asserts the streamed ``tool_result`` frame is ``blocked`` and the
  central dispatcher never really executed it. A permanent negative control patches
  ``make_governor_audit_middleware`` (in the invoker namespace) to a pass-through so the same
  disabled tool reaches the dispatcher — proving the guard has teeth.

No live Anthropic API, no real DB — everything is faked/patched.
"""

from __future__ import annotations

import json
import logging
from contextlib import asynccontextmanager
from types import SimpleNamespace
from unittest.mock import AsyncMock, MagicMock, patch

import pytest
from langchain.agents.middleware import AgentMiddleware, wrap_tool_call
from langchain_core.language_models.chat_models import BaseChatModel
from langchain_core.messages import AIMessage, AIMessageChunk, ToolMessage
from langchain_core.messages.tool import tool_call_chunk
from langchain_core.outputs import ChatGeneration, ChatGenerationChunk, ChatResult
from langgraph.checkpoint.memory import MemorySaver

from src.deep_runtime.middleware.governor_audit import make_governor_audit_middleware

MODULE = "src.deep_runtime.middleware.governor_audit"
AGENT_BUILDER_MODULE = "src.deep_runtime.agent_builder"
CAP_SCOPE_MODULE = "src.deep_runtime.middleware.capability_scope"
TRUST_GATE_MODULE = "src.deep_runtime.middleware.trust_gate"
INVOKER_MODULE = "src.orchestrator.agent_invoker"

AGENT_NAME = "executor"
WORKSPACE_ID = "ws_test"
USER_ID = "u_test"


# ── shared unit-test doubles ─────────────────────────────────────────────────


def _request(tool_name: str, args: dict | None = None, call_id: str = "call_123"):
    """Minimal ToolCallRequest stand-in: only ``.tool_call`` is read."""
    return SimpleNamespace(tool_call={"name": tool_name, "args": args or {}, "id": call_id})


def _hook(mw):
    """Extract the async wrap-tool-call hook bound on the middleware instance."""
    return mw.awrap_tool_call


def _fake_db_factory():
    """A db_factory whose yielded session is never really used (ToolRegistry patched)."""

    @asynccontextmanager
    async def _factory():
        yield SimpleNamespace(name="fake-db")

    return _factory


def _resolver(tool_def, *, ok: bool = True):
    """An injected ``resolve_tool_def`` spy returning ``(ok, tool_def)`` for every name.

    Models the per-turn SHARED ``_resolve_tool_def`` (6C #1): ``(True, <def>)`` known,
    ``(True, None)`` unknown, ``(False, None)`` lookup errored (fail-open for governor_audit).
    """
    spy = AsyncMock(name="resolve_tool_def", return_value=(ok, tool_def))
    return spy


@pytest.fixture
def handler():
    """AsyncMock standing in for the downstream (inner) tool chain."""
    h = AsyncMock(name="handler")
    h.return_value = ToolMessage(content="executed", tool_call_id="call_123")
    return h


# ── Unit (i): a deepagents built-in falls through — NO registry lookup ────────


async def test_builtin_falls_through_without_registry_lookup(handler):
    """A deepagents built-in (``task``) is framework scaffolding — passthrough, no lookup."""
    resolve = _resolver(SimpleNamespace(enabled=True, risk_level="low"))  # must never be called

    mw = make_governor_audit_middleware(
        agent_name=AGENT_NAME, workspace_id=WORKSPACE_ID, resolve_tool_def=resolve
    )
    result = await _hook(mw)(_request("task", {"description": "x"}, "call_bi"), handler)

    handler.assert_awaited_once()
    assert result is handler.return_value
    resolve.assert_not_awaited()


# ── Unit (ii): a DISABLED tool is BLOCKED → ToolMessage(status="error") ───────


async def test_disabled_tool_is_blocked(handler):
    """A tool whose registry def is ``enabled=False`` → blocked ToolMessage, handler NOT run."""
    tool_def = SimpleNamespace(enabled=False, risk_level="high")
    resolve = _resolver(tool_def)

    mw = make_governor_audit_middleware(
        agent_name=AGENT_NAME, workspace_id=WORKSPACE_ID, resolve_tool_def=resolve
    )
    result = await _hook(mw)(_request("disabled_tool", {}, "call_blk"), handler)

    handler.assert_not_awaited()
    assert isinstance(result, ToolMessage)
    assert result.status == "error"
    assert result.tool_call_id == "call_blk"
    assert result.name == "disabled_tool"
    payload = json.loads(result.content)
    assert payload["blocked"] is True
    assert "blocked" in result.content.lower()
    resolve.assert_awaited_once_with("disabled_tool")


# ── Unit (iii): an ENABLED tool audits + falls through ────────────────────────


async def test_enabled_tool_audits_and_falls_through(handler, caplog):
    """An enabled tool: handler runs once AND a ``tool_audit`` INFO record is emitted."""
    tool_def = SimpleNamespace(enabled=True, risk_level="low")
    resolve = _resolver(tool_def)

    mw = make_governor_audit_middleware(
        agent_name=AGENT_NAME, workspace_id=WORKSPACE_ID, resolve_tool_def=resolve
    )
    with caplog.at_level(logging.INFO, logger=MODULE):
        result = await _hook(mw)(_request("search_memories", {"query": "q"}, "call_ok"), handler)

    handler.assert_awaited_once()
    assert result is handler.return_value
    resolve.assert_awaited_once_with("search_memories")
    audit_records = [r for r in caplog.records if r.getMessage() == "tool_audit"]
    assert audit_records, "an enabled tool must emit a tool_audit log record"
    rec = audit_records[0]
    assert getattr(rec, "tool", None) == "search_memories"
    assert getattr(rec, "agent", None) == AGENT_NAME
    assert getattr(rec, "risk_level", None) == "low"


# ── Unit (iv): a missing tool_def is NOT blocked (allow, audit low-risk) ──────


async def test_missing_tool_def_is_allowed(handler):
    """Resolver returns ``(True, None)`` (unknown tool) → NOT blocked; the audit falls through."""
    resolve = _resolver(None)  # (True, None)

    mw = make_governor_audit_middleware(
        agent_name=AGENT_NAME, workspace_id=WORKSPACE_ID, resolve_tool_def=resolve
    )
    result = await _hook(mw)(_request("unknown_tool", {}, "call_unk"), handler)

    handler.assert_awaited_once()
    assert result is handler.return_value


# ── Unit (v): a registry-lookup failure fails OPEN (audit-only, never blocks) ─


async def test_lookup_failure_falls_through(handler):
    """A lookup error surfaces as ``(False, None)`` from the shared resolver and must NOT block
    (audit-only hook) — mirrors the legacy hook's ``except Exception: pass`` allow-through."""
    resolve = _resolver(None, ok=False)  # (False, None) — the fail-open contract

    mw = make_governor_audit_middleware(
        agent_name=AGENT_NAME, workspace_id=WORKSPACE_ID, resolve_tool_def=resolve
    )
    result = await _hook(mw)(_request("some_tool", {}, "call_err"), handler)

    handler.assert_awaited_once()
    assert result is handler.return_value


# ═══════════════════════════════════════════════════════════════════════════════
# Forced-integration guard: governor_audit is FIRST in the deep chain and blocks a
# DISABLED Jarvis tool before the central dispatcher ever executes it.
# ═══════════════════════════════════════════════════════════════════════════════

DISABLED_TOOL_DEF = {
    "name": "delete_everything",
    "description": "A dangerous, disabled tool.",
    "input_schema": {"type": "object", "properties": {"target": {"type": "string"}}},
}
DISABLED_TOOL_ARGS = {"target": "prod"}


class _ScriptedModel(BaseChatModel):
    """Fake streaming model: turn 1 calls the disabled tool, a resumed turn answers."""

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
                        name="delete_everything",
                        args=json.dumps(DISABLED_TOOL_ARGS),
                        id="call_del_1",
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


def _make_invoker(*, executed: list):
    """Build a minimal real ``AgentInvoker`` wired for a fake DB + fake model + fake dispatch.

    ``services=None`` → ``redis=None`` so the write-lock falls through; the direct-chat
    ``authorization_source`` keeps the trust_gate dormant. The only registry lookup that
    decides the outcome is governor_audit's, which is patched per-test.
    """
    from src.orchestrator.agent_invoker import AgentInvoker
    from src.orchestrator.agents import SubAgent

    tool_executor = MagicMock()

    async def fake_execute(name, args, uid, ws):
        executed.append((name, args))
        return {"ok": True}

    tool_executor.execute_tool = fake_execute

    agent = SubAgent(
        name=AGENT_NAME, prompt="p", model_tier="sonnet", capability_scope={"system.delete"}
    )

    from tests.conftest import make_mock_settings

    return AgentInvoker(
        settings=make_mock_settings(runtime="deep"),
        client=MagicMock(),
        services=None,
        budget=MagicMock(),
        circuit_breaker=MagicMock(),
        db_factory_provider=lambda: _fake_db_factory(),
        tool_executor=tool_executor,
        context=MagicMock(),
        agents={AGENT_NAME: agent},
        checkpointer_provider=lambda: MemorySaver(),
    )


async def _drive_disabled_tool(invoker):
    """Build a deep agent via ``_build_deep_agent_for`` (direct chat) that calls the disabled
    tool and return the streamed frames. capability_scope is forced in-scope so governor_audit
    is the ONLY thing that can block; governor_audit's registry resolves ``enabled=False``.
    """
    from src.deep_runtime.prompt_bridge import build_system_message
    from src.deep_runtime.stream_adapter import stream_deep_agent_events

    agent = invoker._agents[AGENT_NAME]
    thread_id = "chat_governor_guard"
    disabled_def = SimpleNamespace(enabled=False, risk_level="high", capability="system.delete")
    reg = MagicMock(name="registry")
    reg.get_tool = AsyncMock(return_value=disabled_def)

    # 6C #1 fold: governor_audit no longer does its OWN lookup — it consumes the per-turn
    # SHARED ``_resolve_tool_def`` (which lives in trust_gate), so patch the registry THERE.
    with (
        patch(f"{AGENT_BUILDER_MODULE}.build_chat_model", return_value=_ScriptedModel()),
        patch(f"{CAP_SCOPE_MODULE}._is_in_scope", AsyncMock(return_value=True)),
        patch(f"{TRUST_GATE_MODULE}.ToolRegistry", return_value=reg),
    ):
        deep_agent = await invoker._build_deep_agent_for(
            agent,
            [DISABLED_TOOL_DEF],
            user_id=USER_ID,
            workspace_id=WORKSPACE_ID,
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
                agent_name=AGENT_NAME,
                model="claude-sonnet-5",
                durability="sync",
            )
        ]
    return frames


async def test_integration_disabled_tool_blocked_by_governor_audit():
    """LOAD-BEARING: governor_audit is FIRST in the deep chain; a DISABLED tool call is blocked
    (tool_result ``blocked=True``, content mentions blocked) and the dispatcher never runs it."""
    executed: list = []
    invoker = _make_invoker(executed=executed)
    frames = await _drive_disabled_tool(invoker)

    tool_results = [f for f in frames if f["event"] == "tool_result"]
    assert tool_results, f"expected a tool_result frame; frames={frames}"
    blocked = [f for f in tool_results if f.get("blocked") is True]
    assert blocked, f"the disabled tool must produce a blocked tool_result; frames={frames}"
    assert "blocked" in blocked[0]["result"].lower(), f"result={blocked[0]['result']}"
    assert executed == [], f"a blocked tool must NEVER reach the dispatcher; executed={executed}"


def _passthrough_governor_audit_factory(
    *, agent_name, workspace_id, resolve_tool_def
) -> AgentMiddleware:  # noqa: ARG001
    """Stand-in factory returning a no-op audit middleware (the negative control)."""

    @wrap_tool_call
    async def _noop(request, handler):
        return await handler(request)

    return _noop


async def test_negative_control_without_governor_audit_disabled_tool_reaches_dispatcher():
    """NEGATIVE CONTROL (teeth): with ``make_governor_audit_middleware`` replaced by a
    pass-through in the invoker, the SAME disabled tool is no longer blocked — it reaches the
    dispatcher and executes. Proves the block in the positive guard genuinely depends on
    governor_audit being wired FIRST into ``extra_middleware``."""
    executed: list = []
    invoker = _make_invoker(executed=executed)

    with patch(
        f"{INVOKER_MODULE}.make_governor_audit_middleware",
        side_effect=_passthrough_governor_audit_factory,
    ):
        frames = await _drive_disabled_tool(invoker)

    assert executed == [("delete_everything", DISABLED_TOOL_ARGS)], (
        f"without governor_audit the disabled tool must reach the dispatcher; executed={executed}"
    )
    blocked = [f for f in frames if f["event"] == "tool_result" and f.get("blocked") is True]
    assert not blocked, f"without governor_audit nothing should block the tool; frames={frames}"
