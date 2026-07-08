"""Unit tests for deep_runtime.delegates (Step 7B2 P2).

Offline. Prove that ``build_read_only_delegate`` yields a deepagents
``CompiledSubAgent`` (``{"name","description","runnable"}``) whose OWN
capability-scope guard + central tool dispatcher are baked into its compiled
graph (build method A from the Phase-0 spike). No live API calls: a scripted
streaming ``BaseChatModel`` fake drives the child, and the capability_scope
registry lookup is stubbed offline via a name->capability map — mirroring
``spikes/deep_delegate/subagent_gated_probe.py`` but replicated locally so no
import-time global monkeypatch leaks across the suite.
"""

from __future__ import annotations

import json
from collections.abc import AsyncIterator
from contextlib import asynccontextmanager
from types import SimpleNamespace
from typing import Any
from unittest.mock import AsyncMock, patch

from langchain_core.callbacks import AsyncCallbackManagerForLLMRun
from langchain_core.language_models.chat_models import BaseChatModel
from langchain_core.messages import (
    AIMessage,
    AIMessageChunk,
    BaseMessage,
    HumanMessage,
    ToolMessage,
)
from langchain_core.messages.tool import tool_call_chunk
from langchain_core.outputs import ChatGeneration, ChatGenerationChunk, ChatResult
from langgraph.graph.state import CompiledStateGraph

from src.deep_runtime import agent_builder
from src.deep_runtime.delegates import (
    DELEGATE_RESPONSE_FORMAT,
    DelegateSummary,
    build_read_only_delegate,
)
from src.deep_runtime.model_factory import build_chat_model
from src.orchestrator.agents import SubAgent, ThinkingConfig, create_sub_agents
from src.orchestrator.prompts import PRESENTER_VOICE

WS = "ws_delegate_test"
USER = "user_delegate_test"
CHILD_MODEL_ID = "claude-sonnet-4-6"

# internal_search -> internal.search (IN Perceiver scope); email_send -> email.send
# (a write cap, NOT in Perceiver scope).
_NAME_TO_CAP: dict[str, str | None] = {
    "internal_search": "internal.search",
    "email_send": "email.send",
}

_TOOL_DEFS = [
    {"name": "internal_search", "description": "search knowledge"},
    {"name": "email_send", "description": "send email"},
]


# ---------------------------------------------------------------------------
# Offline capability resolution: stub ToolRegistry with a name->capability map.
# ---------------------------------------------------------------------------
class _FakeToolDef:
    def __init__(self, capability: str | None) -> None:
        self.capability = capability


class _FakeRegistry:
    def __init__(self, db: Any, workspace_id: str | None = None) -> None:  # noqa: ARG002
        pass

    async def get_tool(self, name: str) -> _FakeToolDef | None:
        cap = _NAME_TO_CAP.get(name)
        return _FakeToolDef(cap) if cap is not None else None


def _fake_db_factory():
    """An async-context-manager factory yielding a sentinel DB (matches test_agent_builder)."""

    @asynccontextmanager
    async def _factory():
        yield SimpleNamespace(name="fake-db")

    return _factory


def _recorder(rec: list[tuple[str, dict]]):
    async def _execute(name: str, args: dict, user_id: str, workspace_id: str) -> dict:  # noqa: ARG001
        rec.append((name, args))
        return {"results": ["r1", "r2"], "count": 42}

    return _execute


def _read_only_perceiver() -> SubAgent:
    return create_sub_agents()["perceiver"]


def _empty_scope_read_only() -> SubAgent:
    """A read-only config with EMPTY capability_scope (no write caps -> no ValueError
    at construction even without a db_factory)."""
    return SubAgent(
        name="perceiver",
        prompt=create_sub_agents()["perceiver"].prompt,
        model_tier="sonnet",
        capability_scope=set(),
        max_tokens=4096,
        temperature=0.3,
        thinking=ThinkingConfig(enabled=False, budget_tokens=0),
    )


def _read_only_resolver() -> AsyncMock:
    resolver = AsyncMock()
    resolver.is_write_capability = AsyncMock(return_value=False)
    return resolver


# ---------------------------------------------------------------------------
# Scripted streaming fake chat model — turn chosen by inbound ToolMessage count.
# ---------------------------------------------------------------------------
class ScriptedModel(BaseChatModel):
    """Streams a scripted list of turns; turn N chosen by tool-round count."""

    model_name: str = CHILD_MODEL_ID

    _turns: list[list[AIMessageChunk]]

    def __init__(self, turns: list[list[AIMessageChunk]], model_name: str = CHILD_MODEL_ID) -> None:
        super().__init__(model_name=model_name)
        object.__setattr__(self, "_turns", turns)

    @property
    def _llm_type(self) -> str:
        return "scripted-fake-delegate"

    def _get_ls_params(self, *args: Any, **kwargs: Any) -> dict[str, Any]:  # noqa: ARG002
        return {"ls_provider": "anthropic", "ls_model_name": self.model_name}

    def bind_tools(self, tools: Any, **kwargs: Any):  # noqa: ANN003, ARG002
        return self

    def _select_turn(self, messages: list[BaseMessage]) -> list[AIMessageChunk]:
        tool_rounds = sum(1 for m in messages if isinstance(m, ToolMessage))
        idx = min(tool_rounds, len(self._turns) - 1)
        return self._turns[idx]

    async def _astream(
        self,
        messages: list[BaseMessage],
        stop: list[str] | None = None,  # noqa: ARG002
        run_manager: AsyncCallbackManagerForLLMRun | None = None,
        **kwargs: Any,  # noqa: ARG002
    ) -> AsyncIterator[ChatGenerationChunk]:
        for chunk in self._select_turn(messages):
            gen = ChatGenerationChunk(message=chunk)
            if run_manager is not None:
                await run_manager.on_llm_new_token("", chunk=gen)
            yield gen

    async def _agenerate(
        self,
        messages: list[BaseMessage],
        stop: list[str] | None = None,
        run_manager: AsyncCallbackManagerForLLMRun | None = None,
        **kwargs: Any,
    ) -> ChatResult:
        merged: AIMessageChunk | None = None
        async for gen in self._astream(messages, stop=stop, run_manager=run_manager, **kwargs):
            merged = gen.message if merged is None else merged + gen.message
        assert merged is not None
        msg = AIMessage(
            content=merged.content,
            tool_calls=list(merged.tool_calls),
            usage_metadata=merged.usage_metadata,
            response_metadata=merged.response_metadata,
        )
        return ChatResult(generations=[ChatGeneration(message=msg)])

    def _generate(self, *args: Any, **kwargs: Any) -> ChatResult:
        raise NotImplementedError("sync generate not used in this async test")


def _usage_chunk(stop_reason: str) -> AIMessageChunk:
    return AIMessageChunk(
        content=[],
        usage_metadata={
            "input_tokens": 40,
            "output_tokens": 8,
            "total_tokens": 48,
            "input_token_details": {"cache_read": 0, "cache_creation": 0},
        },
        response_metadata={"model_name": CHILD_MODEL_ID, "stop_reason": stop_reason},
    )


def _call_then_answer_turns(tool_name: str, args: dict) -> list[list[AIMessageChunk]]:
    """Turn 0 calls *tool_name*; turn 1 answers with terminal text."""
    return [
        [
            AIMessageChunk(
                content=[],
                tool_call_chunks=[
                    tool_call_chunk(name=tool_name, args=json.dumps(args), id="child_tc", index=0)
                ],
            ),
            _usage_chunk("tool_use"),
        ],
        [
            AIMessageChunk(content=[{"type": "text", "text": "done", "index": 0}]),
            _usage_chunk("end_turn"),
        ],
    ]


# ===========================================================================
# 0 — DELEGATE_RESPONSE_FORMAT / DelegateSummary contract mirrors the Perceiver.
# ===========================================================================
def test_delegate_summary_contract_mirrors_perceiver():
    assert DELEGATE_RESPONSE_FORMAT is DelegateSummary

    empty = DelegateSummary()
    assert empty.findings == []
    assert empty.synthesis == ""
    assert empty.gaps == []
    assert empty.confidence is None

    filled = DelegateSummary(
        findings=["f1", "f2"], synthesis="narrative", gaps=["g1"], confidence=0.5
    )
    assert filled.findings == ["f1", "f2"]
    assert filled.synthesis == "narrative"
    assert filled.gaps == ["g1"]
    assert filled.confidence == 0.5


# ===========================================================================
# 1 — build_read_only_delegate returns a CompiledSubAgent dict.
# ===========================================================================
async def test_returns_compiled_subagent_dict():
    cfg = _read_only_perceiver()
    rec: list = []
    with patch.object(agent_builder, "CapabilityResolver", return_value=_read_only_resolver()):
        spec = await build_read_only_delegate(
            cfg,
            _TOOL_DEFS,
            workspace_id=WS,
            user_id=USER,
            db_factory=_fake_db_factory(),
            execute_tool=_recorder(rec),
        )
    assert spec["name"] == "perceiver"
    assert isinstance(spec["description"], str) and spec["description"]
    assert isinstance(spec["runnable"], CompiledStateGraph)


# ===========================================================================
# 2 — per-child model is sonnet (Perceiver tier), and the COMPILED child used it.
# ===========================================================================
async def test_per_child_model_is_sonnet():
    cfg = _read_only_perceiver()
    assert cfg.model_tier == "sonnet"
    assert build_chat_model(cfg).model == "claude-sonnet-4-6"

    recorded: dict = {}
    real_build = agent_builder.build_chat_model

    def _spy(agent):
        recorded["agent"] = agent
        return real_build(agent)

    rec: list = []
    with (
        patch.object(agent_builder, "build_chat_model", side_effect=_spy),
        patch.object(agent_builder, "CapabilityResolver", return_value=_read_only_resolver()),
    ):
        await build_read_only_delegate(
            cfg,
            _TOOL_DEFS,
            workspace_id=WS,
            user_id=USER,
            db_factory=_fake_db_factory(),
            execute_tool=_recorder(rec),
        )
    # the compiled child model was built from the perceiver config (sonnet tier).
    assert recorded["agent"].name == "perceiver"
    assert recorded["agent"].model_tier == "sonnet"


# ===========================================================================
# 3 — the child gate fires: in-scope read dispatched; out-of-scope read denied.
# ===========================================================================
async def test_child_gate_dispatches_in_scope_read():
    cfg = _read_only_perceiver()
    rec: list = []
    with (
        patch.object(
            agent_builder,
            "build_chat_model",
            lambda a: ScriptedModel(_call_then_answer_turns("internal_search", {"query": "X"})),
        ),
        patch.object(agent_builder, "CapabilityResolver", return_value=_read_only_resolver()),
        patch(
            "src.deep_runtime.middleware.capability_scope.ToolRegistry",
            _FakeRegistry,
        ),
    ):
        spec = await build_read_only_delegate(
            cfg,
            _TOOL_DEFS,
            workspace_id=WS,
            user_id=USER,
            db_factory=_fake_db_factory(),
            execute_tool=_recorder(rec),
        )
        await spec["runnable"].ainvoke(
            {"messages": [HumanMessage("go")]},
            {"configurable": {"thread_id": "delegate-in-scope"}},
        )
    # in-scope read reached the recording execute_tool.
    assert ("internal_search", {"query": "X"}) in rec


async def test_child_gate_denies_out_of_scope_read():
    cfg = _read_only_perceiver()
    rec: list = []
    with (
        patch.object(
            agent_builder,
            "build_chat_model",
            lambda a: ScriptedModel(
                _call_then_answer_turns("email_send", {"to": "a@b.com", "body": "hi"})
            ),
        ),
        patch.object(agent_builder, "CapabilityResolver", return_value=_read_only_resolver()),
        patch(
            "src.deep_runtime.middleware.capability_scope.ToolRegistry",
            _FakeRegistry,
        ),
    ):
        spec = await build_read_only_delegate(
            cfg,
            _TOOL_DEFS,
            workspace_id=WS,
            user_id=USER,
            db_factory=_fake_db_factory(),
            execute_tool=_recorder(rec),
        )
        result = await spec["runnable"].ainvoke(
            {"messages": [HumanMessage("go")]},
            {"configurable": {"thread_id": "delegate-oos"}},
        )
    # out-of-scope write NEVER reached execute_tool (denied by the child's own guard) ...
    assert not any(n == "email_send" for n, _ in rec)
    # ... and a status="error" ToolMessage was produced instead.
    tool_msgs = [m for m in result["messages"] if isinstance(m, ToolMessage)]
    assert any(m.status == "error" for m in tool_msgs)


# ===========================================================================
# 4 — NO trust_gate / write_lock middleware; system prompt has NO Presenter voice.
# ===========================================================================
async def test_no_trust_gate_write_lock_or_presenter_voice():
    cfg = _read_only_perceiver()
    rec: list = []
    captured: dict = {}
    real_create = agent_builder.create_deep_agent

    def _capture(**kwargs):
        captured.update(kwargs)
        return real_create(**kwargs)

    with (
        patch.object(agent_builder, "create_deep_agent", side_effect=_capture),
        patch.object(agent_builder, "CapabilityResolver", return_value=_read_only_resolver()),
    ):
        await build_read_only_delegate(
            cfg,
            _TOOL_DEFS,
            workspace_id=WS,
            user_id=USER,
            db_factory=_fake_db_factory(),
            execute_tool=_recorder(rec),
        )

    # system prompt is the Perceiver's OWN role prompt — never the deep lead's
    # Presenter-voice inline-format augmentation.
    system_prompt = captured["system_prompt"]
    assert system_prompt == cfg.prompt
    assert PRESENTER_VOICE not in system_prompt

    # exactly two middlewares: the capability_scope guard + the tool dispatcher.
    # No trust_gate / write_lock (those gate WRITES; a read-only delegate never writes).
    middleware = captured["middleware"]
    mw_ids = {getattr(m, "name", None) or type(m).__name__ for m in middleware}
    assert len(middleware) == 2
    assert "capability_scope_guard" in mw_ids
    assert "jarvis_tool_dispatcher" in mw_ids
    lowered = " ".join(str(x).lower() for x in mw_ids)
    assert "trust_gate" not in lowered
    assert "write_lock" not in lowered


# ===========================================================================
# 5 — negative control (teeth): the capability_scope guard is what gates. It is
#     installed ONLY when a db_factory is given; with db_factory=None (no guard),
#     the same out-of-scope call executes.
#
# NOTE (deviation): the plan's literal negative control (perceiver + db_factory=None
# compiling unguarded) is impossible — build_deep_agent's fail-closed construction
# guard raises ValueError for a scoped agent without a db_factory. So this uses an
# EMPTY-scope read-only config (empty scope -> no ValueError), holding the config
# constant and toggling ONLY the db_factory (guard present vs absent).
# ===========================================================================
async def test_negative_control_scope_guard_is_the_gate():
    cfg = _empty_scope_read_only()

    # arm 1: db_factory given -> capability_scope guard installed. Empty scope means
    # deny-all, so the out-of-scope tool is DENIED (never reaches execute_tool).
    rec_guarded: list = []
    with patch.object(
        agent_builder,
        "build_chat_model",
        lambda a: ScriptedModel(
            _call_then_answer_turns("email_send", {"to": "a@b.com", "body": "hi"})
        ),
    ):
        spec_guarded = await build_read_only_delegate(
            cfg,
            _TOOL_DEFS,
            workspace_id=WS,
            user_id=USER,
            db_factory=_fake_db_factory(),
            execute_tool=_recorder(rec_guarded),
        )
        await spec_guarded["runnable"].ainvoke(
            {"messages": [HumanMessage("go")]},
            {"configurable": {"thread_id": "neg-guarded"}},
        )
    assert not any(n == "email_send" for n, _ in rec_guarded)  # guard denied it

    # arm 2: db_factory=None -> NO capability_scope guard is installed. The SAME
    # out-of-scope call now EXECUTES — proving the guard is what does the gating.
    rec_open: list = []
    with patch.object(
        agent_builder,
        "build_chat_model",
        lambda a: ScriptedModel(
            _call_then_answer_turns("email_send", {"to": "a@b.com", "body": "hi"})
        ),
    ):
        spec_open = await build_read_only_delegate(
            cfg,
            _TOOL_DEFS,
            workspace_id=WS,
            user_id=USER,
            db_factory=None,
            execute_tool=_recorder(rec_open),
        )
        await spec_open["runnable"].ainvoke(
            {"messages": [HumanMessage("go")]},
            {"configurable": {"thread_id": "neg-open"}},
        )
    assert any(n == "email_send" for n, _ in rec_open)  # no guard -> executed
