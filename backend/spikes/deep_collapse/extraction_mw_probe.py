"""SPIKE probe (Step 7B1, Task 0.2 — DECISION GATE): prove an ``@after_model``
middleware (same shape as ``make_budget_middleware``) fires once per turn inside a
real ``create_deep_agent`` and can drive an ASYNC extraction side effect from the
turn's messages.

The 7B1 assumption under test: the Librarian's entity/memory extraction (today a
separate post-turn agent call) can be re-homed onto an ``@after_model`` hook that
reads ``state["messages"]`` — pulling the first human message + last AI message
text — and awaits an injected async extractor. If ``@after_model`` reliably fires
and the async body runs to completion, the collapse is viable.

THROWAWAY investigation probe. Runs fully OFFLINE — no Anthropic API key, no real
LLM. A scripted fake ``BaseChatModel`` emits a single text-only turn (no tool
calls), so the agent loop runs the model exactly once → ``after_model`` fires
exactly once. The "extractor" is an ``AsyncMock`` we assert was awaited once with
the turn's human text present.

Run (from backend/):
    uv run python spikes/deep_collapse/extraction_mw_probe.py
"""

from __future__ import annotations

import asyncio
import os
import sys
from collections.abc import AsyncIterator
from typing import Any
from unittest.mock import AsyncMock

# Standalone script: put backend/ (three dirs up) on sys.path so `src.*` imports
# resolve regardless of cwd.
sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.dirname(os.path.abspath(__file__)))))

from deepagents import create_deep_agent
from langchain.agents.middleware import AgentMiddleware, after_model
from langchain_core.callbacks import AsyncCallbackManagerForLLMRun
from langchain_core.language_models.chat_models import BaseChatModel
from langchain_core.messages import (
    AIMessage,
    AIMessageChunk,
    BaseMessage,
    HumanMessage,
)
from langchain_core.outputs import ChatGeneration, ChatGenerationChunk, ChatResult
from langgraph.checkpoint.memory import MemorySaver

MODEL_ID = "claude-sonnet-4-6"
AGENT_NAME = "librarian"
HUMAN_TEXT = "remember Bob works at Acme"
AI_REPLY = "Noted — I'll remember that Bob works at Acme."


def _message_text(msg: BaseMessage | None) -> str:
    """Return the plain-text content of a message (robust to str or block list).

    Parses ``.content`` directly rather than the ``.text``/``.text()`` accessor,
    which is mid-deprecation across langchain versions.
    """
    if msg is None:
        return ""
    content = getattr(msg, "content", "")
    if isinstance(content, str):
        return content
    parts = [
        b.get("text", "")
        for b in content
        if isinstance(b, dict) and b.get("type") == "text"
    ]
    return "".join(parts)


def make_extraction_middleware(extractor: AsyncMock) -> AgentMiddleware:
    """Build an ``@after_model`` middleware that awaits *extractor* with the turn's
    (human_text, ai_text) — mirrors ``make_budget_middleware``'s hook shape."""

    @after_model(name="ExtractionProbeMiddleware")
    async def _extract(state: dict[str, Any], runtime: Any) -> None:
        messages = state.get("messages") or []
        human = next((m for m in messages if isinstance(m, HumanMessage)), None)
        ai = next((m for m in reversed(messages) if isinstance(m, AIMessage)), None)
        await extractor(_message_text(human), _message_text(ai))
        return None

    return _extract


# ---------------------------------------------------------------------------
# Scripted fake model: one text-only turn (no tool call) so after_model fires once.
# ---------------------------------------------------------------------------
class TextOnlyFakeModel(BaseChatModel):
    @property
    def _llm_type(self) -> str:
        return "scripted-textonly-fake"

    def bind_tools(self, tools, **kwargs):  # noqa: ANN001, ANN003, ARG002
        return self

    @staticmethod
    def _script() -> list[AIMessageChunk]:
        return [
            AIMessageChunk(content=[{"type": "text", "text": AI_REPLY, "index": 0}]),
            AIMessageChunk(
                content=[],
                usage_metadata={
                    "input_tokens": 40,
                    "output_tokens": 12,
                    "total_tokens": 52,
                    "input_token_details": {"cache_read": 0, "cache_creation": 0},
                },
                response_metadata={"model_name": MODEL_ID, "stop_reason": "end_turn"},
            ),
        ]

    async def _astream(
        self,
        messages: list[BaseMessage],
        stop: list[str] | None = None,
        run_manager: AsyncCallbackManagerForLLMRun | None = None,
        **kwargs: Any,
    ) -> AsyncIterator[ChatGenerationChunk]:
        for msg_chunk in self._script():
            yield ChatGenerationChunk(message=msg_chunk)

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
        raise NotImplementedError("sync generate not used in this async spike")


async def main() -> None:
    extractor = AsyncMock(name="fake_extractor")
    middleware = make_extraction_middleware(extractor)

    agent = create_deep_agent(
        model=TextOnlyFakeModel(),
        tools=[],
        middleware=[middleware],
        system_prompt="You are Muldro's Librarian.",
        checkpointer=MemorySaver(),
    )

    final = await agent.ainvoke(
        {"messages": [{"role": "user", "content": HUMAN_TEXT}]},
        {"configurable": {"thread_id": "spike2"}},
    )

    print("=" * 78)
    print("SPIKE 0.2 — @after_model extraction middleware fires + drives async extraction")
    print("=" * 78)

    msg_types = [type(m).__name__ for m in final["messages"]]
    print(f"\nfinal message types: {msg_types}")
    print(f"extractor.await_count = {extractor.await_count} (expect 1)")
    print(f"extractor.await_args  = {extractor.await_args}")

    # --- assertions ---------------------------------------------------------
    assert extractor.await_count == 1, (
        f"@after_model did NOT fire exactly once (await_count={extractor.await_count})"
    )
    (called_human, called_ai), _kwargs = extractor.await_args
    print(f"\nextractor called with human_text = {called_human!r}")
    print(f"extractor called with ai_text    = {called_ai!r}")
    assert HUMAN_TEXT in called_human, (
        f"turn's human text not present in the extractor call (got {called_human!r})"
    )
    assert AI_REPLY in called_ai, (
        f"turn's AI reply not present in the extractor call (got {called_ai!r})"
    )

    print("\nSPIKE 0.2 PASS")


if __name__ == "__main__":
    asyncio.run(main())
