"""SPIKE (Step 6B research): does a ``@wrap_tool_call`` gate body RE-RUN its
PRE-``interrupt()`` side effects when the graph is resumed via ``Command(resume=…)``?

This is the design-driving question for the trust gate (Task 2). The gate persists
an ``Approval`` and THEN calls ``interrupt()``. If the tool node replays from the top
on resume — as a LangGraph interrupt is known to do (see spikes/postgres_saver/probe.py,
where a plain node's ``NODE_RUNS`` reaches 2) — then a naive ``create_approval()`` placed
before ``interrupt()`` would create a DUPLICATE pending Approval on every resume. The gate
must therefore make approval persistence IDEMPOTENT (get-or-create keyed on
``(workspace_id, thread_id, tool_call_id)``).

THROWAWAY offline probe. No API key. A counter is incremented BEFORE the interrupt and
another AFTER; the tool has its own counter.

Run (from backend/):
    uv run python spikes/deep_stream/interrupt_replay_side_effect_probe.py

============================================================================
FINDING (langgraph 1.2.6 / deepagents 0.6.11):
    after turn-1 (paused):  PRE=1 POST=0 ECHO=0
    after resume(approve):  PRE=2 POST=1 ECHO=1
    => The wrap_tool_call body REPLAYS on resume: PRE-interrupt code runs TWICE,
       POST-interrupt code + the tool run exactly ONCE.
    => CONSEQUENCE for the gate: create_approval BEFORE interrupt() must be
       idempotent (get-or-create on (workspace_id, thread_id, tool_call_id), no
       status filter — the original row may already be marked approved/rejected by
       the resume path). Otherwise every resumed gated action leaves an orphan
       pending Approval.
============================================================================
"""

from __future__ import annotations

import asyncio
import json
from collections.abc import AsyncIterator
from typing import Any

from deepagents import create_deep_agent
from langchain.agents.middleware import wrap_tool_call
from langchain_core.callbacks import AsyncCallbackManagerForLLMRun
from langchain_core.language_models.chat_models import BaseChatModel
from langchain_core.messages import AIMessage, AIMessageChunk, ToolMessage
from langchain_core.messages.tool import tool_call_chunk
from langchain_core.outputs import ChatGeneration, ChatGenerationChunk, ChatResult
from langchain_core.tools import tool
from langgraph.checkpoint.memory import MemorySaver
from langgraph.types import Command, interrupt

PRE_INTERRUPT_RUNS: list[int] = []
POST_INTERRUPT_RUNS: list[int] = []
ECHO_CALLS: list[str] = []


@tool
def echo(text: str) -> str:
    """Trivial side-effecting tool."""
    ECHO_CALLS.append(text)
    return f"echo: {text}"


@wrap_tool_call
async def gate(request, handler):  # noqa: ANN001, ANN201
    PRE_INTERRUPT_RUNS.append(1)  # stands in for create_approval()
    verdict = interrupt({"n_pre": len(PRE_INTERRUPT_RUNS)})
    POST_INTERRUPT_RUNS.append(1)
    if verdict == "approve":
        return await handler(request)
    return ToolMessage(
        content='{"rejected":true}', tool_call_id=request.tool_call["id"], status="error"
    )


class _M(BaseChatModel):
    @property
    def _llm_type(self) -> str:
        return "fake"

    def bind_tools(self, tools, **kwargs):  # noqa: ANN001, ANN003, ARG002
        return self

    def _script(self, messages):  # noqa: ANN001
        if any(isinstance(m, ToolMessage) for m in messages):
            return [AIMessageChunk(content=[{"type": "text", "text": "done", "index": 0}])]
        return [
            AIMessageChunk(
                content=[],
                tool_call_chunks=[
                    tool_call_chunk(name="echo", args=json.dumps({"text": "hi"}), id="c1", index=0)
                ],
            )
        ]

    async def _astream(  # noqa: ANN001, ANN003
        self, messages, stop=None, run_manager=None, **kwargs
    ) -> AsyncIterator[ChatGenerationChunk]:
        for ch in self._script(messages):
            yield ChatGenerationChunk(message=ch)

    async def _agenerate(  # noqa: ANN001, ANN003
        self,
        messages,
        stop=None,
        run_manager: AsyncCallbackManagerForLLMRun | None = None,
        **kwargs,
    ) -> ChatResult:
        merged: AIMessageChunk | None = None
        async for gen in self._astream(messages):
            merged = gen.message if merged is None else merged + gen.message
        assert merged is not None
        msg = AIMessage(content=merged.content, tool_calls=list(merged.tool_calls))
        return ChatResult(generations=[ChatGeneration(message=msg)])

    def _generate(self, *a: Any, **k: Any) -> ChatResult:
        raise NotImplementedError


async def main() -> None:
    agent = create_deep_agent(
        model=_M(),
        tools=[echo],
        middleware=[gate],
        checkpointer=MemorySaver(),
        system_prompt="t",
    )
    cfg = {"configurable": {"thread_id": "replay-1"}}

    async for _ in agent.astream(
        {"messages": [{"role": "user", "content": "go"}]},
        config=cfg,
        stream_mode=["messages", "updates"],
        durability="sync",
    ):
        pass

    def _snap(label: str) -> None:
        print(
            f"{label}: PRE={len(PRE_INTERRUPT_RUNS)} "
            f"POST={len(POST_INTERRUPT_RUNS)} ECHO={len(ECHO_CALLS)}"
        )

    _snap("after turn-1")

    async for _ in agent.astream(
        Command(resume="approve"), config=cfg, stream_mode=["messages", "updates"]
    ):
        pass
    _snap("after resume")

    print("=" * 60)
    replayed = len(PRE_INTERRUPT_RUNS) > 1
    once = len(ECHO_CALLS) == 1
    print(f"PRE-interrupt code re-ran on resume? {replayed}  (PRE={len(PRE_INTERRUPT_RUNS)})")
    print(f"tool ran exactly once?               {once}  (ECHO={len(ECHO_CALLS)})")
    print("=> gate approval persistence MUST be idempotent (get-or-create).")


if __name__ == "__main__":
    asyncio.run(main())
