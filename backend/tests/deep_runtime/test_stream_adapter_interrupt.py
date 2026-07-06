"""Step 6B Task 3: stream_deep_agent_events must surface an 8th SSE shape,
``approval_needed``, when the trust gate pauses the graph via ``interrupt()``.

Empirical fact proven by the Task-0 spike
(backend/spikes/deep_stream/interrupt_resume_stream_proof.py, re-verified here): under
the installed langgraph (1.2.6), ``interrupt()`` called from inside a
``@wrap_tool_call`` gate during ``agent.astream(stream_mode=["messages","updates"])``
does NOT raise ``GraphInterrupt`` — it surfaces as an ``updates``-mode stream item
shaped ``("updates", {"__interrupt__": (Interrupt(value={...}),)})``. Before this task
the adapter's ``try/except Exception`` swallowed that unhandled shape as a generic
sanitized ``error`` frame (the ``updates`` branch's inner loop treats
``{"__interrupt__": (...)}`` as a non-dict-of-messages update and silently
``continue``s, so nothing was ever yielded for it and the turn looked stuck/failed).

This test builds a tiny approval gate that calls ``interrupt(...)`` before running a
tool, streams turn 1 through the real adapter, and asserts the pause is now observable
as a distinct ``approval_needed`` frame — not an ``error`` frame and not an
``agent_done`` frame (the pause must not look like completion or failure).
"""

from __future__ import annotations

import json
from collections.abc import AsyncIterator
from typing import Any

from deepagents import create_deep_agent
from langchain.agents.middleware import wrap_tool_call
from langchain_core.callbacks import AsyncCallbackManagerForLLMRun
from langchain_core.language_models.chat_models import BaseChatModel
from langchain_core.messages import AIMessage, AIMessageChunk, BaseMessage, ToolMessage
from langchain_core.messages.tool import tool_call_chunk
from langchain_core.outputs import ChatGeneration, ChatGenerationChunk, ChatResult
from langchain_core.tools import tool
from langgraph.checkpoint.memory import MemorySaver
from langgraph.types import interrupt

from src.deep_runtime.stream_adapter import stream_deep_agent_events

MODEL_ID = "claude-sonnet-5"


@tool
def echo(text: str) -> str:
    """Echo the input text back (trivial side-effecting tool for the gate proof)."""
    return f"echo: {text}"


@wrap_tool_call
async def approval_gate(request, handler):  # noqa: ANN001, ANN201
    """Mirror ``trust_gate``: pause BEFORE the handler runs, resume decides."""
    verdict = interrupt(
        {
            "approval_id": "apr_x",
            "thread_id": "t-adapter",
            "capability": "email.send",
            "risk_level": "high",
        }
    )
    approved = verdict == "approve" or (
        isinstance(verdict, dict) and verdict.get("decision") == "approve"
    )
    if approved:
        return await handler(request)
    return ToolMessage(
        content=json.dumps({"rejected": True}),
        tool_call_id=request.tool_call["id"],
        status="error",
    )


class _ScriptedModel(BaseChatModel):
    """Fake streaming model: turn 1 calls ``echo``. The test only drives turn 1
    (the gate pauses before the tool runs, so no turn 2 is ever scripted here)."""

    @property
    def _llm_type(self) -> str:
        return "scripted-fake"

    def bind_tools(self, tools, **kwargs):  # noqa: ANN001, ANN003, ARG002
        return self

    @staticmethod
    def _turn1() -> list[AIMessageChunk]:
        return [
            AIMessageChunk(
                content=[],
                tool_call_chunks=[
                    tool_call_chunk(
                        name="echo",
                        args=json.dumps({"text": "hello"}),
                        id="call_echo",
                        index=0,
                    ),
                ],
            ),
            AIMessageChunk(
                content=[],
                usage_metadata={
                    "input_tokens": 100,
                    "output_tokens": 10,
                    "total_tokens": 110,
                    "input_token_details": {"cache_read": 0, "cache_creation": 0},
                },
                response_metadata={"model_name": MODEL_ID, "stop_reason": "tool_use"},
            ),
        ]

    def _script_for(self, messages: list[BaseMessage]) -> list[AIMessageChunk]:
        return self._turn1()

    async def _astream(
        self,
        messages: list[BaseMessage],
        stop: list[str] | None = None,
        run_manager: AsyncCallbackManagerForLLMRun | None = None,
        **kwargs: Any,
    ) -> AsyncIterator[ChatGenerationChunk]:
        for ch in self._script_for(messages):
            yield ChatGenerationChunk(message=ch)

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


async def _run_adapter_over_gated_stream() -> AsyncIterator[dict]:
    agent = create_deep_agent(
        model=_ScriptedModel(),
        tools=[echo],
        middleware=[approval_gate],
        checkpointer=MemorySaver(),
        system_prompt="t",
    )
    config = {"configurable": {"thread_id": "t-adapter"}}
    async for frame in stream_deep_agent_events(
        agent,
        {"messages": [{"role": "user", "content": "go"}]},
        config,
        agent_name="operator",
        model=MODEL_ID,
    ):
        yield frame


async def test_adapter_emits_approval_needed_on_gate_interrupt():
    frames = [f async for f in _run_adapter_over_gated_stream()]

    approval_frames = [f for f in frames if f["event"] == "approval_needed"]
    assert len(approval_frames) == 1, f"expected exactly one approval_needed frame, got: {frames}"

    frame = approval_frames[0]
    assert frame["approval_id"] == "apr_x"
    assert frame["thread_id"] == "t-adapter"
    assert frame["capability"] == "email.send"
    assert frame["risk_level"] == "high"
    assert frame["agent"] == "operator"

    # The pause must not ALSO look like a failure or a completion.
    assert not any(f["event"] == "error" for f in frames)
    assert not any(f["event"] == "agent_done" for f in frames)
