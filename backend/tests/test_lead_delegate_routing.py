"""A-4 (B3): LIVE lead->delegate routing instruction (dormant behind deep_delegates_enabled).

``_build_delegate_subagents`` already builds the read-only Perceiver delegate and registers
it on the deep lead's built-in ``task`` tool (``subagents=`` into ``_build_deep_agent_for``).
But NOTHING drove the lead to actually delegate — the scaffolding existed, the routing
DECISION did not. This module pins the new contract: a delegation instruction
(``DEEP_DELEGATION_INSTRUCTION``) is appended to the lead's system_blocks ONLY when
``deep_delegates_enabled`` is True AND a delegate was actually built (non-empty
``subagents``), telling the lead to route read-only research to the Perceiver via ``task``.

Contract pinned here:
  (a) flag ON + delegate built -> the Perceiver delegate is in the ``subagents=`` passed to
      the deep build AND the lead's built system prompt CONTAINS the delegation instruction;
  (b) flag OFF -> no delegate offered, no instruction (byte-neutral to today's lead-only turn);
  (c) a FAKE model that emits a ``task`` tool-call CAN route to a delegate named "perceiver"
      (proves the instruction names a real, routable target — not model behavior guesswork);
  (d) negative control WITH TEETH: force the INNER delegate build to raise (the 10A A4
      degrade path) -> ``_build_delegate_subagents`` degrades to ``[]`` -> the turn completes
      lead-only, NO crash, NO instruction (since no delegate built);
  (e) pure-function unit tests for ``_augment_system_blocks_for_delegation`` (append-once,
      identity when no delegates, idempotent, immutable) — mirrors the A-3 augment tests;
  (f) composition with A-3: with BOTH deep_inline_format AND deep_delegates_enabled on for
      the reply lead, BOTH PRESENTER_VOICE and the delegation instruction are present and
      neither clobbers the other.
"""

from __future__ import annotations

import json
from collections.abc import AsyncIterator
from contextlib import asynccontextmanager
from typing import Any
from unittest.mock import AsyncMock, MagicMock, patch

from langchain_core.callbacks import AsyncCallbackManagerForLLMRun
from langchain_core.language_models.chat_models import BaseChatModel
from langchain_core.messages import (
    AIMessage,
    AIMessageChunk,
    BaseMessage,
    HumanMessage,
    SystemMessage,
    ToolMessage,
)
from langchain_core.messages.tool import tool_call_chunk
from langchain_core.outputs import ChatGeneration, ChatGenerationChunk, ChatResult
from langgraph.checkpoint.memory import MemorySaver

from src.deep_runtime import agent_builder
from src.deep_runtime.delegates import build_read_only_delegate
from src.orchestrator.agent_invoker import (
    AgentInvoker,
    _augment_system_blocks_for_delegation,
)
from src.orchestrator.agents import SubAgent, ThinkingConfig
from src.orchestrator.prompts import DEEP_DELEGATION_INSTRUCTION, PRESENTER_VOICE
from tests.conftest import make_mock_settings

WS = "ws_lead_delegate"
USER = "user_lead_delegate"
LEAD_MODEL_ID = "claude-sonnet-4-6"


# =====================================================================================
# (e) Pure-function unit tests for _augment_system_blocks_for_delegation.
# =====================================================================================
def _base_blocks() -> list[dict]:
    return [{"type": "text", "text": "soul+role"}]


def test_has_delegates_appends_instruction_once():
    blocks = _base_blocks()
    out = _augment_system_blocks_for_delegation(blocks, has_delegates=True)

    assert out is not blocks  # new list, input untouched
    assert out[:-1] == blocks  # original blocks preserved, in order
    assert out[-1] == {"type": "text", "text": DEEP_DELEGATION_INSTRUCTION}
    joined = "".join(b.get("text", "") for b in out)
    assert joined.count(DEEP_DELEGATION_INSTRUCTION) == 1


def test_no_delegates_is_identity():
    """The teeth of the byte-neutral gate: no delegate built -> NO instruction, same list."""
    blocks = _base_blocks()
    out = _augment_system_blocks_for_delegation(blocks, has_delegates=False)
    assert out is blocks
    assert not any(b.get("text") == DEEP_DELEGATION_INSTRUCTION for b in out)


def test_idempotent_when_instruction_already_present():
    blocks = [{"type": "text", "text": f"role\n\n{DEEP_DELEGATION_INSTRUCTION}"}]
    out = _augment_system_blocks_for_delegation(blocks, has_delegates=True)
    assert out is blocks  # identity — no second injection
    joined = "".join(b.get("text", "") for b in out)
    assert joined.count(DEEP_DELEGATION_INSTRUCTION) == 1


def test_immutable_never_mutates_input():
    blocks = _base_blocks()
    before = list(blocks)
    _augment_system_blocks_for_delegation(blocks, has_delegates=True)
    assert blocks == before  # input list object unchanged


# =====================================================================================
# Construction tests driving call_agent_stream (assert on CONSTRUCTION, not model behavior).
# =====================================================================================
def _prompt_text(system_prompt: Any) -> str:
    """Flatten a captured build_deep_agent ``system_prompt`` (a SystemMessage whose content
    is a list of text blocks) into one string."""
    assert isinstance(system_prompt, SystemMessage)
    content = system_prompt.content
    if isinstance(content, str):
        return content
    return "\n".join(b["text"] for b in content if isinstance(b, dict) and b.get("type") == "text")


def _make_invoker(
    *, deep_delegates_enabled: bool, deep_inline_format: bool = False
) -> AgentInvoker:
    """A real AgentInvoker wired for the deep runtime with the minimal mocks to reach the
    call_agent_stream deep-lead build. ``deep_delegates_enabled`` / ``deep_inline_format`` are
    set EXPLICITLY (MagicMock-truthy hazard)."""
    tool_executor = MagicMock()
    tool_executor.apply_cache_control_to_tools = lambda tools: tools
    tool_executor.get_tools_for_agent = AsyncMock(return_value=[])
    tool_executor.execute_tool = AsyncMock(return_value={"ok": True})

    context = MagicMock()
    context.assemble_context = AsyncMock(return_value="")

    fake_db = MagicMock(name="fake-db")

    @asynccontextmanager
    async def _db_factory():
        yield fake_db

    presenter = SubAgent(
        name="presenter",
        prompt="You are the Presenter.",
        model_tier="sonnet",
        capability_scope=set(),
    )
    # A NON-lead agent too (the planner is never the reply lead), so the pinning test can
    # drive a non-lead deep build through the same helper.
    planner = SubAgent(
        name="planner",
        prompt="You are the Planner.",
        model_tier="sonnet",
        capability_scope=set(),
    )
    return AgentInvoker(
        settings=make_mock_settings(
            runtime="deep",
            cheap_mode=False,
            deep_delegates_enabled=deep_delegates_enabled,
            deep_inline_format=deep_inline_format,
        ),
        client=MagicMock(),
        services=None,
        budget=MagicMock(),
        circuit_breaker=MagicMock(),
        db_factory_provider=lambda: _db_factory,
        tool_executor=tool_executor,
        context=context,
        agents={"presenter": presenter, "planner": planner},
    )


async def _run_stream_capturing_build(inv: AgentInvoker, agent_name: str = "presenter") -> dict:
    """Drive call_agent_stream(agent_name, ...) on the deep runtime while capturing the
    kwargs passed to build_deep_agent (which receives the final subagents= and
    system_prompt=). Returns {"subagents", "system_prompt", "frames"}."""
    captured: dict = {}

    async def _capture_build(agent, shells, **kwargs):  # noqa: ANN001, ARG001
        captured["subagents"] = kwargs.get("subagents")
        captured["system_prompt"] = kwargs.get("system_prompt")
        return MagicMock(name="compiled-deep-agent")

    async def _fake_stream(*a, **k):  # noqa: ANN002, ANN003, ARG001
        yield {
            "event": "agent_done",
            "agent": agent_name,
            "text": "ok",
            "input_tokens": 1,
            "output_tokens": 1,
            "cache_creation_tokens": 0,
            "cache_read_tokens": 0,
            "tools_called": [],
            "latency_ms": 1,
            "cost_usd": 0.0,
        }

    with (
        patch("src.orchestrator.agent_invoker.build_deep_agent", side_effect=_capture_build),
        patch("src.orchestrator.agent_invoker.stream_deep_agent_events", _fake_stream),
        patch("src.orchestrator.agent_invoker.reap_thread", AsyncMock()),
        patch("src.orchestrator.agent_invoker.agent_loop") as mock_loop,
    ):
        frames = [
            f
            async for f in inv.call_agent_stream(
                agent_name, message="hi", user_id=USER, workspace_id=WS, tools_override=[]
            )
        ]
    mock_loop.assert_not_called()  # deep runtime, never legacy
    captured["frames"] = frames
    return captured


async def test_flag_on_with_delegate_passes_subagent_and_adds_instruction():
    """(a)+(b): flag ON and a delegate built -> the Perceiver delegate rides in subagents=
    AND the delegation instruction is in the lead's built system prompt."""
    inv = _make_invoker(deep_delegates_enabled=True)
    sentinel_delegate = {"name": "perceiver", "description": "d", "runnable": MagicMock()}

    with patch.object(
        inv, "_build_delegate_subagents", AsyncMock(return_value=[sentinel_delegate])
    ):
        captured = await _run_stream_capturing_build(inv)

    # (a) the delegate is forwarded to the deep build.
    assert captured["subagents"] == [sentinel_delegate]
    # (b) the lead's built prompt carries the delegation instruction.
    assert DEEP_DELEGATION_INSTRUCTION in _prompt_text(captured["system_prompt"])
    # turn completed (no crash).
    assert any(f["event"] == "agent_done" for f in captured["frames"])


async def test_flag_off_no_delegate_no_instruction_byte_neutral():
    """(b): flag OFF -> no delegate offered, no delegation instruction (byte-neutral)."""
    inv = _make_invoker(deep_delegates_enabled=False)

    # _build_delegate_subagents must NOT even be called when the flag is off.
    with patch.object(inv, "_build_delegate_subagents", AsyncMock()) as spy:
        captured = await _run_stream_capturing_build(inv)
    spy.assert_not_called()

    # subagents defaulted to the empty tuple; NO instruction; NO Presenter voice (inline off).
    assert captured["subagents"] == ()
    prompt = _prompt_text(captured["system_prompt"])
    assert DEEP_DELEGATION_INSTRUCTION not in prompt
    assert PRESENTER_VOICE not in prompt
    assert any(f["event"] == "agent_done" for f in captured["frames"])


async def test_delegate_build_failure_degrades_lead_only_no_instruction():
    """(d) negative control WITH TEETH: force the INNER delegate build to raise (10A A4
    path). _build_delegate_subagents catches it -> [] -> has_delegates False -> the turn
    completes lead-only, NO crash, and NO delegation instruction is added."""
    inv = _make_invoker(deep_delegates_enabled=True)

    with (
        patch("src.deep_runtime.delegates.disable_general_purpose_subagent"),
        patch(
            "src.deep_runtime.delegates.build_read_only_delegate",
            AsyncMock(side_effect=RuntimeError("boom")),
        ),
    ):
        captured = await _run_stream_capturing_build(inv)

    # the delegate build degraded to [] -> falsy -> no instruction, lead serves alone.
    assert captured["subagents"] == []
    assert DEEP_DELEGATION_INSTRUCTION not in _prompt_text(captured["system_prompt"])
    # NOT a crash: the stream still completed.
    assert any(f["event"] == "agent_done" for f in captured["frames"])


async def test_composition_with_a3_inline_augmentation():
    """(f): with BOTH deep_inline_format AND deep_delegates_enabled on for the reply lead,
    the built prompt carries BOTH the A-3 Presenter voice AND the delegation instruction —
    neither augmentation clobbers the other."""
    inv = _make_invoker(deep_delegates_enabled=True, deep_inline_format=True)
    sentinel_delegate = {"name": "perceiver", "description": "d", "runnable": MagicMock()}

    with patch.object(
        inv, "_build_delegate_subagents", AsyncMock(return_value=[sentinel_delegate])
    ):
        captured = await _run_stream_capturing_build(inv)

    prompt = _prompt_text(captured["system_prompt"])
    assert PRESENTER_VOICE in prompt  # A-3 augmentation survived
    assert DEEP_DELEGATION_INSTRUCTION in prompt  # A-4 augmentation survived
    assert captured["subagents"] == [sentinel_delegate]


async def test_non_lead_agent_currently_receives_instruction_not_lead_scoped():
    """PINS CURRENT (not-yet-lead-scoped) BEHAVIOR: unlike A-3's Presenter voice (gated on
    ``_is_reply_lead``), the delegation offering + instruction are gated SOLELY on
    ``bool(subagents)``. So a NON-lead deep agent (here the planner, which is never the reply
    lead) ALSO gets a delegate built and the delegation instruction when
    ``deep_delegates_enabled`` is on.

    This is intentional and dormant/byte-neutral today (flag off in prod). It is pinned here
    so that lead-scoping the offering to the post-B5 deep lead (Step-10 Part-B) becomes a
    DELIBERATE, test-visible change: whoever adds ``_is_reply_lead``-style scoping to the
    offering must update THIS test, which surfaces the decision instead of letting it slip in
    silently. Do NOT "fix" this by tightening the gate now — see the breadcrumb in
    agent_invoker.py's call site and ``_augment_system_blocks_for_delegation`` docstring.
    """
    inv = _make_invoker(deep_delegates_enabled=True)
    sentinel_delegate = {"name": "perceiver", "description": "d", "runnable": MagicMock()}

    with patch.object(
        inv, "_build_delegate_subagents", AsyncMock(return_value=[sentinel_delegate])
    ):
        captured = await _run_stream_capturing_build(inv, agent_name="planner")

    # The planner is NOT the reply lead, yet it CURRENTLY receives both the delegate and the
    # instruction — gated only on has_delegates, not on _is_reply_lead.
    assert captured["subagents"] == [sentinel_delegate]
    assert DEEP_DELEGATION_INSTRUCTION in _prompt_text(captured["system_prompt"])


# =====================================================================================
# (c) A FAKE model that emits a ``task`` tool-call CAN route to a "perceiver" delegate.
# =====================================================================================
class _ScriptedModel(BaseChatModel):
    """Streams a scripted list of turns; turn N chosen by inbound ToolMessage count."""

    model_name: str = LEAD_MODEL_ID
    _turns: list[list[AIMessageChunk]]

    def __init__(self, turns: list[list[AIMessageChunk]], model_name: str = LEAD_MODEL_ID) -> None:
        super().__init__(model_name=model_name)
        object.__setattr__(self, "_turns", turns)

    @property
    def _llm_type(self) -> str:
        return "scripted-fake-lead-delegate"

    def _get_ls_params(self, *args: Any, **kwargs: Any) -> dict[str, Any]:  # noqa: ARG002
        return {"ls_provider": "anthropic", "ls_model_name": self.model_name}

    def bind_tools(self, tools: Any, **kwargs: Any):  # noqa: ANN003, ARG002
        return self

    def _select_turn(self, messages: list[BaseMessage]) -> list[AIMessageChunk]:
        tool_rounds = sum(1 for m in messages if isinstance(m, ToolMessage))
        return self._turns[min(tool_rounds, len(self._turns) - 1)]

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
        response_metadata={"model_name": LEAD_MODEL_ID, "stop_reason": stop_reason},
    )


def _terminal_turns(text: str) -> list[list[AIMessageChunk]]:
    return [
        [
            AIMessageChunk(content=[{"type": "text", "text": text, "index": 0}]),
            _usage_chunk("end_turn"),
        ]
    ]


def _task_then_answer(subagent_type: str, description: str) -> list[list[AIMessageChunk]]:
    """Turn 0 routes to *subagent_type* via the built-in ``task`` tool; turn 1 answers."""
    return [
        [
            AIMessageChunk(
                content=[],
                tool_call_chunks=[
                    tool_call_chunk(
                        name="task",
                        args=json.dumps(
                            {"subagent_type": subagent_type, "description": description}
                        ),
                        id="lead_task_tc",
                        index=0,
                    )
                ],
            ),
            _usage_chunk("tool_use"),
        ],
        [
            AIMessageChunk(content=[{"type": "text", "text": "lead-done", "index": 0}]),
            _usage_chunk("end_turn"),
        ],
    ]


def _empty_scope_perceiver() -> SubAgent:
    """A read-only config named 'perceiver' with EMPTY capability_scope (no write caps -> no
    ValueError at construction even without a db_factory)."""
    return SubAgent(
        name="perceiver",
        prompt="You are the Perceiver.",
        model_tier="sonnet",
        capability_scope=set(),
        max_tokens=4096,
        temperature=0.3,
        thinking=ThinkingConfig(enabled=False, budget_tokens=0),
    )


async def test_fake_task_call_routes_to_perceiver_delegate():
    """(c): a lead built WITH the read-only 'perceiver' delegate, driven by a FAKE model that
    emits a ``task(subagent_type='perceiver')`` call, actually routes into the delegate and
    gets its output back — proving the delegation instruction names a real, routable target
    (not model-behavior guesswork)."""
    marker = "PERCEIVER_FINDINGS_XYZ"

    # Build the read-only delegate (name defaults to 'perceiver') in its OWN patch scope so
    # the lead's later build_chat_model patch cannot touch its baked-in model. db_factory=None
    # + empty scope -> no scope guard, no ValueError; the delegate simply terminal-answers.
    delegate_cfg = _empty_scope_perceiver()
    with patch.object(
        agent_builder, "build_chat_model", lambda a: _ScriptedModel(_terminal_turns(marker))
    ):
        delegate = await build_read_only_delegate(
            delegate_cfg,
            [],
            workspace_id=WS,
            user_id=USER,
            db_factory=None,
            execute_tool=AsyncMock(return_value={}),
        )
    assert delegate["name"] == "perceiver"  # the routable subagent_type the instruction names

    # Build the lead WITH the delegate registered; its fake model routes to 'perceiver'.
    lead_cfg = _empty_scope_perceiver()
    with patch.object(
        agent_builder,
        "build_chat_model",
        lambda a: _ScriptedModel(_task_then_answer("perceiver", "research the thing")),
    ):
        lead = await agent_builder.build_deep_agent(
            lead_cfg, [], subagents=[delegate], checkpointer=MemorySaver()
        )
    result = await lead.ainvoke(
        {"messages": [HumanMessage("go")]},
        {"configurable": {"thread_id": "lead-routes-to-perceiver"}},
    )

    # the task tool returned the delegate's output -> the lead successfully routed to the
    # 'perceiver' delegate. A non-existent subagent_type would have errored instead.
    task_msgs = [m for m in result["messages"] if isinstance(m, ToolMessage) and m.name == "task"]
    assert task_msgs, "expected a task ToolMessage from the routed delegate"
    assert any(marker in str(m.content) for m in task_msgs)
    assert all(getattr(m, "status", "success") != "error" for m in task_msgs)
