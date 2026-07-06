"""Step 6A: stream_deep_agent_events must reproduce the exact SSE dict shapes that
AgentInvoker.call_agent_stream emits from LoopEvents, so agent_event_from_sse still types
them and the frozen web contract is preserved.

The fake streaming model + echo tool are copied (minimally) from the Task-0 probe
(backend/spikes/deep_stream/probe.py) — spikes/ is a standalone script dir, not an
importable package, so the scripted model is reproduced here rather than imported.
"""

from __future__ import annotations

import json
from collections.abc import AsyncIterator
from typing import Any

from deepagents import create_deep_agent
from langchain_core.callbacks import AsyncCallbackManagerForLLMRun
from langchain_core.language_models.chat_models import BaseChatModel
from langchain_core.messages import (
    AIMessage,
    AIMessageChunk,
    BaseMessage,
    ToolMessage,
)
from langchain_core.messages.tool import tool_call_chunk
from langchain_core.outputs import ChatGeneration, ChatGenerationChunk, ChatResult
from langchain_core.tools import tool
from langgraph.checkpoint.memory import MemorySaver

from src.deep_runtime.stream_adapter import stream_deep_agent_events

MODEL_ID = "claude-sonnet-4-6"

_ALLOWED_EVENTS = {
    "agent_start",
    "thinking",
    "text_delta",
    "tool_call",
    "tool_result",
    "agent_done",
    "error",
}
_REQUIRED_KEYS = {
    "agent_start": {"event", "agent", "model"},
    "thinking": {"event", "agent", "text", "is_thinking"},
    "text_delta": {"event", "agent", "text"},
    "tool_call": {"event", "agent", "tool", "input"},
    "tool_result": {"event", "agent", "tool", "result", "blocked", "latency_ms"},
    "agent_done": {
        "event",
        "agent",
        "text",
        "input_tokens",
        "output_tokens",
        "cache_creation_tokens",
        "cache_read_tokens",
        "tools_called",
        "latency_ms",
        "cost_usd",
    },
    "error": {"event", "agent", "code", "message", "correlation_id"},
}


@tool
def echo(text: str) -> str:
    """Echo the input text back (trivial tool so the agent takes a tool turn)."""
    return f"echo: {text}"


def _token_text(chunk: AIMessageChunk) -> str:
    if isinstance(chunk.content, str):
        return chunk.content
    parts: list[str] = []
    for block in chunk.content:
        if isinstance(block, dict):
            if block.get("type") == "text":
                parts.append(block.get("text", ""))
            elif block.get("type") == "thinking":
                parts.append(block.get("thinking", ""))
    return "".join(parts)


class _ScriptedFakeChatModel(BaseChatModel):
    """Streams scripted ``AIMessageChunk``s mirroring langchain-anthropic's shape:
    turn 1 = thinking + text deltas + a tool call; turn 2 (after the ToolMessage
    is present) = a final text turn. Fully offline — no Anthropic API."""

    @property
    def _llm_type(self) -> str:
        return "scripted-fake"

    def bind_tools(self, tools, **kwargs):  # noqa: ANN001, ANN003, ARG002
        return self

    @staticmethod
    def _turn1() -> list[AIMessageChunk]:
        return [
            AIMessageChunk(content=[{"type": "thinking", "thinking": "I should ", "index": 0}]),
            AIMessageChunk(content=[{"type": "thinking", "thinking": "echo this.", "index": 0}]),
            AIMessageChunk(content=[{"type": "text", "text": "Let me ", "index": 1}]),
            AIMessageChunk(content=[{"type": "text", "text": "echo that.", "index": 1}]),
            AIMessageChunk(
                content=[],
                tool_call_chunks=[
                    tool_call_chunk(
                        name="echo",
                        args=json.dumps({"text": "hello"}),
                        id="call_1",
                        index=2,
                    )
                ],
            ),
            AIMessageChunk(
                content=[],
                usage_metadata={
                    "input_tokens": 120,
                    "output_tokens": 25,
                    "total_tokens": 145,
                    "input_token_details": {"cache_read": 100, "cache_creation": 0},
                },
                response_metadata={"model_name": MODEL_ID, "stop_reason": "tool_use"},
            ),
        ]

    @staticmethod
    def _turn2() -> list[AIMessageChunk]:
        return [
            AIMessageChunk(content=[{"type": "text", "text": "Done — echoed ", "index": 0}]),
            AIMessageChunk(content=[{"type": "text", "text": "'hello'.", "index": 0}]),
            AIMessageChunk(
                content=[],
                usage_metadata={
                    "input_tokens": 60,
                    "output_tokens": 12,
                    "total_tokens": 72,
                    "input_token_details": {"cache_read": 140, "cache_creation": 0},
                },
                response_metadata={"model_name": MODEL_ID, "stop_reason": "end_turn"},
            ),
        ]

    def _script_for(self, messages: list[BaseMessage]) -> list[AIMessageChunk]:
        took_tool_turn = any(isinstance(m, ToolMessage) for m in messages)
        return self._turn2() if took_tool_turn else self._turn1()

    async def _astream(
        self,
        messages: list[BaseMessage],
        stop: list[str] | None = None,
        run_manager: AsyncCallbackManagerForLLMRun | None = None,
        **kwargs: Any,
    ) -> AsyncIterator[ChatGenerationChunk]:
        for msg_chunk in self._script_for(messages):
            gen = ChatGenerationChunk(message=msg_chunk)
            if run_manager is not None:
                await run_manager.on_llm_new_token(_token_text(msg_chunk), chunk=gen)
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


def _make_exploding_model(detail: str) -> _ScriptedFakeChatModel:
    class _ExplodingModel(_ScriptedFakeChatModel):
        async def _astream(self, messages, stop=None, run_manager=None, **kwargs):  # noqa: ANN001
            raise RuntimeError(detail)
            yield  # pragma: no cover

    return _ExplodingModel()


def _graph_input() -> dict:
    return {"messages": [{"role": "user", "content": "hi"}]}


async def _run_adapter_over_fake_stream() -> AsyncIterator[dict]:
    agent = create_deep_agent(
        model=_ScriptedFakeChatModel(),
        tools=[echo],
        checkpointer=MemorySaver(),
        system_prompt="You are a test agent.",
    )
    config = {"configurable": {"thread_id": "t1"}}
    async for frame in stream_deep_agent_events(
        agent, _graph_input(), config, agent_name="operator", model=MODEL_ID
    ):
        yield frame


async def _run_adapter_over_raising_stream(detail: str) -> AsyncIterator[dict]:
    agent = create_deep_agent(
        model=_make_exploding_model(detail),
        tools=[echo],
        checkpointer=MemorySaver(),
        system_prompt="You are a test agent.",
    )
    config = {"configurable": {"thread_id": "t-err"}}
    async for frame in stream_deep_agent_events(
        agent, _graph_input(), config, agent_name="operator", model=MODEL_ID
    ):
        yield frame


async def test_adapter_emits_frozen_sse_shapes():
    frames = [f async for f in _run_adapter_over_fake_stream()]
    assert frames, "adapter yielded nothing"
    for f in frames:
        assert f["event"] in _ALLOWED_EVENTS
        assert _REQUIRED_KEYS[f["event"]] <= set(f.keys())
        assert f["agent"] == "operator"
    kinds = [f["event"] for f in frames]
    assert "text_delta" in kinds
    assert kinds.count("tool_call") == kinds.count("tool_result") >= 1
    assert kinds.count("agent_done") == 1
    from src.orchestrator.core_events import agent_event_from_sse

    for f in frames:
        typed = agent_event_from_sse(f)
        assert typed is not None


async def test_adapter_sanitizes_errors():
    frames = [f async for f in _run_adapter_over_raising_stream("boom-secret-detail")]
    err = [f for f in frames if f["event"] == "error"]
    assert err, "no error frame emitted"
    for f in err:
        assert "boom-secret-detail" not in f["message"]
        assert _REQUIRED_KEYS["error"] <= set(f.keys())
