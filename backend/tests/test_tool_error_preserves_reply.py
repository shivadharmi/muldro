"""A tool that RETURNS an error keeps the turn alive — and keeps the reply already written.

R2 flips ``tool_executor._REJECT_ON_INVALID_TOOL_ARGS`` to ``True``, at which point
``ToolExecutor.execute_tool`` starts RETURNING ``{"error": ..., "error_code":
"invalid_tool_args"}`` for an internal tool whose arguments fail a Pydantic parse — most
often ``render_surface``, the tool the chat lead uses to draw a workspace card. That is only
safe if a rejected tool call costs the lead its CARD and not its PROSE.

The dangerous shape is a lead that emits text and a tool call in the SAME assistant message
(both Anthropic and OpenAI allow this): it has already committed to a reply before the tool
fails. If that prose were dropped the user would see an empty chat bubble — ``routes_chat``
persists a reply only on ``Presentation``, and an empty one persists nothing at all.

This module pins the property end to end, offline:

1. ``test_returned_tool_error_keeps_the_committed_reply`` — a returned error dict becomes a
   ``blocked`` ``tool_result`` frame carrying the message the model must act on, the stream
   does NOT emit an ``error`` frame, ``agent_done`` still arrives, and its ``text`` holds
   BOTH the pre-tool prose and the post-tool correction (``stream_adapter``'s ``text_parts``
   accumulates across model turns and the ``ToolMessage`` branch yields WITHOUT returning).
2. ``test_agent_done_text_becomes_a_presentation`` — that ``agent_done`` really does reach
   the user, by driving the REAL ``_ChatSingleLeadMixin._stream_lead_and_complete``.
3. ``test_raised_tool_error_loses_the_reply`` — the contrast that proves (1) measures
   something: a tool that RAISES produces an ``error`` frame and NO ``agent_done``, and the
   same mixin then reports ``RunFailed`` with the prose discarded.

Everything runs through a real compiled ``build_deep_agent`` graph with a scripted offline
model, the real ``muldro_tool_dispatcher`` and the real ``stream_deep_agent_events``. Zero
network.
"""

from __future__ import annotations

import json
from collections.abc import AsyncIterator
from contextlib import asynccontextmanager
from types import SimpleNamespace
from typing import Any
from unittest.mock import AsyncMock, create_autospec, patch

from langchain_core.callbacks import AsyncCallbackManagerForLLMRun
from langchain_core.language_models.chat_models import BaseChatModel
from langchain_core.messages import AIMessage, AIMessageChunk, BaseMessage, ToolMessage
from langchain_core.messages.tool import tool_call_chunk
from langchain_core.outputs import ChatGeneration, ChatGenerationChunk, ChatResult
from langgraph.checkpoint.memory import MemorySaver

from src.deep_runtime.agent_builder import build_deep_agent
from src.deep_runtime.middleware.muldro_tool_dispatcher import make_muldro_tool_dispatcher
from src.deep_runtime.stream_adapter import stream_deep_agent_events
from src.deep_runtime.tool_bridge import build_tool_shells
from src.orchestrator.agent_invoker import AgentInvoker
from src.orchestrator.agents import SubAgent, ThinkingConfig
from src.orchestrator.chat_single_lead import _NO_REPLY_TEXT, _ChatSingleLeadMixin
from src.orchestrator.core_events import Presentation, RunFailed

MODEL_ID = "claude-sonnet-4-6"
TOOL_NAME = "render_surface"

# The prose the lead has ALREADY committed to when the tool call goes out, and the prose it
# writes after seeing the rejection. Both must survive into the final reply.
COMMITTED_PROSE = "Here is the comparison you asked for."
CORRECTIVE_PROSE = " I could not draw the card, so here it is as plain text instead."

# What R2 will return once _REJECT_ON_INVALID_TOOL_ARGS is True. Shaped exactly like
# ToolExecutor's own rendering (_render_validation_error) so the assertion below is about the
# real payload the model would have to read.
INVALID_ARGS_ERROR = {
    "error": (
        f"Invalid argument(s) for '{TOOL_NAME}': sections.0.type: "
        "Input tag 'bogus' found using 'type' does not match any of the expected tags. "
        "Fix the arguments and call the tool again."
    ),
    "error_code": "invalid_tool_args",
}

BOGUS_ARGS = {"title": "Comparison", "sections": [{"type": "bogus"}]}


# ---------------------------------------------------------------------------
# Scripted offline model — text AND a tool call in the same assistant message.
# ---------------------------------------------------------------------------


def _token_text(chunk: AIMessageChunk) -> str:
    if isinstance(chunk.content, str):
        return chunk.content
    parts: list[str] = []
    for block in chunk.content:
        if isinstance(block, dict) and block.get("type") == "text":
            parts.append(block.get("text", ""))
    return "".join(parts)


class _CommittedReplyModel(BaseChatModel):
    """Turn 1 = prose THEN a tool call (one assistant message); turn 2 = corrective prose.

    ``tool_name`` is a constructor field so the raise-contrast test can point the same script
    at a tool whose execution blows up instead of returning an error dict.
    """

    tool_name: str = TOOL_NAME

    @property
    def _llm_type(self) -> str:
        return "committed-reply-scripted-fake"

    def bind_tools(self, tools: Any, **kwargs: Any) -> "_CommittedReplyModel":  # noqa: ANN401
        return self

    def _turn1(self) -> list[AIMessageChunk]:
        return [
            # The commitment: prose streamed BEFORE the tool call, same message.
            AIMessageChunk(content=[{"type": "text", "text": COMMITTED_PROSE, "index": 0}]),
            AIMessageChunk(
                content=[],
                tool_call_chunks=[
                    tool_call_chunk(
                        name=self.tool_name,
                        args=json.dumps(BOGUS_ARGS),
                        id="call_render",
                        index=1,
                    )
                ],
            ),
            AIMessageChunk(
                content=[],
                usage_metadata={
                    "input_tokens": 100,
                    "output_tokens": 20,
                    "total_tokens": 120,
                    "input_token_details": {"cache_read": 0, "cache_creation": 0},
                },
                response_metadata={"model_name": MODEL_ID, "stop_reason": "tool_use"},
            ),
        ]

    @staticmethod
    def _turn2() -> list[AIMessageChunk]:
        return [
            AIMessageChunk(content=[{"type": "text", "text": CORRECTIVE_PROSE, "index": 0}]),
            AIMessageChunk(
                content=[],
                usage_metadata={
                    "input_tokens": 50,
                    "output_tokens": 10,
                    "total_tokens": 60,
                    "input_token_details": {"cache_read": 0, "cache_creation": 0},
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
        for chunk in self._script_for(messages):
            gen = ChatGenerationChunk(message=chunk)
            if run_manager is not None:
                await run_manager.on_llm_new_token(_token_text(chunk), chunk=gen)
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
        raise NotImplementedError("sync _generate not used in async tests")


# ---------------------------------------------------------------------------
# Harness
# ---------------------------------------------------------------------------


def _make_lead(scope: set[str]) -> SubAgent:
    return SubAgent(
        name="lead",
        prompt="You are the test lead.",
        model_tier="sonnet",
        capability_scope=scope,
        max_tokens=4096,
        temperature=0.3,
        thinking=ThinkingConfig(enabled=False, budget_tokens=0),
    )


def _make_db_factory():
    @asynccontextmanager
    async def _factory():
        yield SimpleNamespace(name="fake-db")

    return _factory


def _tool_defs(name: str) -> list[dict]:
    return [
        {
            "name": name,
            "description": "Draw a workspace surface.",
            # Deliberately permissive: the shell must NOT reject BOGUS_ARGS itself. What is
            # under test is the transport of an error RETURNED by execute_tool, which is where
            # R2's Pydantic rejection lives.
            "input_schema": {"type": "object", "properties": {}},
        }
    ]


async def _stream_turn(*, tool_name: str, execute_tool, thread_id: str) -> list[dict]:
    """Run one scripted turn through the real graph + real adapter and collect SSE frames."""
    lead = _make_lead({"system.respond"})
    shells = build_tool_shells(_tool_defs(tool_name))
    dispatcher = make_muldro_tool_dispatcher(
        execute_tool=execute_tool, user_id="u", workspace_id="ws"
    )

    with (
        patch(
            "src.deep_runtime.middleware.capability_scope._is_in_scope",
            new=AsyncMock(side_effect=lambda name, *a, **k: name == tool_name),
        ),
        patch(
            "src.deep_runtime.agent_builder._has_write_capability_in_scope",
            new=AsyncMock(return_value=False),
        ),
        patch(
            "src.deep_runtime.agent_builder.build_chat_model",
            return_value=_CommittedReplyModel(tool_name=tool_name),
        ),
    ):
        agent = await build_deep_agent(
            lead,
            shells,
            workspace_id="ws",
            db_factory=_make_db_factory(),
            extra_middleware=(dispatcher,),
            checkpointer=MemorySaver(),
            system_prompt="test",
        )
        return [
            f
            async for f in stream_deep_agent_events(
                agent,
                {"messages": [{"role": "user", "content": "compare these"}]},
                {"configurable": {"thread_id": thread_id}},
                agent_name="lead",
                model=MODEL_ID,
            )
        ]


async def _returns_invalid_args(name: str, args: dict, user_id: str, workspace_id: str) -> dict:
    """Stand-in for a post-R2 ToolExecutor.execute_tool: RETURNS the rejection."""
    assert name == TOOL_NAME
    return dict(INVALID_ARGS_ERROR)


async def _raises(name: str, args: dict, user_id: str, workspace_id: str) -> dict:
    """The contrast: execution blows up instead of returning a dict."""
    raise RuntimeError("boom: execute_tool exploded")


class _LeadHarness(_ChatSingleLeadMixin):
    """Minimal host for the mixin — real ``_stream_lead_and_complete``, real completion tail.

    Only the collaborators the mixin reaches through ``self`` are stubbed: the invoker (whose
    stream is replayed from a REAL streamed run, never hand-written), the runtime event
    publisher, and the surface pusher. ``_interaction_learner=None`` skips the learner branch.
    """

    def __init__(self, frames: list[dict]) -> None:
        self._frames = frames
        self._spawned: list[Any] = []
        self._interaction_learner = None
        self._events = SimpleNamespace(emit_runtime_event=AsyncMock(return_value=None))
        self._surfaces = SimpleNamespace(push_presenter_surface=AsyncMock(return_value=None))

        invoker = create_autospec(AgentInvoker, instance=True)
        invoker.stream_deep_lead.side_effect = self._replay
        self._invoker = invoker

    async def _replay(self, *args: Any, **kwargs: Any):
        for frame in self._frames:
            yield frame

    def _spawn_background(self, coro) -> None:
        self._spawned.append(coro)
        coro.close()  # never scheduled — avoids an un-awaited-coroutine warning.

    async def run(self) -> list[Any]:
        return [
            evt
            async for evt in self._stream_lead_and_complete(
                lead=_make_lead({"system.respond"}),
                message="compare these",
                context_block="",
                intent="simple_question",
                trace=SimpleNamespace(trace_id="trace_test"),
                user_id="u",
                workspace_id="ws",
                effective_mode="auto",
                presence="present",
            )
        ]


# ---------------------------------------------------------------------------
# 1. The committed-reply case
# ---------------------------------------------------------------------------


async def test_returned_tool_error_keeps_the_committed_reply() -> None:
    """A RETURNED tool error blocks the card, not the reply."""
    frames = await _stream_turn(
        tool_name=TOOL_NAME, execute_tool=_returns_invalid_args, thread_id="t_returned"
    )
    kinds = [f["event"] for f in frames]

    results = [f for f in frames if f["event"] == "tool_result" and f["tool"] == TOOL_NAME]
    assert len(results) == 1, f"expected exactly one {TOOL_NAME} tool_result; frames={frames}"
    assert results[0]["blocked"] is True, f"rejected tool must map to blocked; {results[0]}"
    assert INVALID_ARGS_ERROR["error"] in str(results[0]["result"]), (
        f"the rejection text must reach the model so it can act on it; {results[0]}"
    )

    assert "error" not in kinds, f"a returned error must not become an error frame; {frames}"

    done = [f for f in frames if f["event"] == "agent_done"]
    assert len(done) == 1, f"the turn must still complete with agent_done; frames={frames}"

    text = done[0]["text"]
    assert COMMITTED_PROSE in text, (
        f"the prose committed BEFORE the tool call was dropped; agent_done text={text!r}"
    )
    assert CORRECTIVE_PROSE in text, (
        f"the prose written AFTER the rejection was dropped; agent_done text={text!r}"
    )
    assert text.index(COMMITTED_PROSE) < text.index(CORRECTIVE_PROSE), (
        f"accumulated reply is out of order; agent_done text={text!r}"
    )


# ---------------------------------------------------------------------------
# 2. The Presentation hop
# ---------------------------------------------------------------------------


async def test_agent_done_text_becomes_a_presentation() -> None:
    """The surviving ``agent_done`` text really is what the user is shown.

    Drives the REAL ``_ChatSingleLeadMixin._stream_lead_and_complete`` (including the real
    ``strip_surface_blocks`` / ``_NO_REPLY_TEXT`` fallback) over the frames produced by a REAL
    streamed run — no hand-written agent_done.
    """
    frames = await _stream_turn(
        tool_name=TOOL_NAME, execute_tool=_returns_invalid_args, thread_id="t_present"
    )
    events = await _LeadHarness(frames).run()

    presentations = [e for e in events if isinstance(e, Presentation)]
    assert len(presentations) == 1, f"expected exactly one Presentation; events={events}"
    reply = presentations[0].text
    assert reply != _NO_REPLY_TEXT, "the turn fell back to the no-reply text"
    assert COMMITTED_PROSE in reply and CORRECTIVE_PROSE in reply, (
        f"the Presentation lost the lead's prose; reply={reply!r}"
    )
    assert not any(isinstance(e, RunFailed) for e in events), (
        f"a returned tool error must not fail the turn; events={events}"
    )


# ---------------------------------------------------------------------------
# 3. The contrast — a RAISE is different
# ---------------------------------------------------------------------------


async def test_raised_tool_error_loses_the_reply() -> None:
    """A tool that RAISES ends the stream: an ``error`` frame, no ``agent_done``, no reply.

    This is what makes test 1 meaningful — the two cases must come out differently.
    """
    frames = await _stream_turn(tool_name=TOOL_NAME, execute_tool=_raises, thread_id="t_raised")
    kinds = [f["event"] for f in frames]

    assert "error" in kinds, f"a raising tool must produce an error frame; frames={frames}"
    assert "agent_done" not in kinds, f"a raising tool must NOT reach agent_done; frames={frames}"
    assert any(f["event"] == "text_delta" for f in frames), (
        "sanity: the committed prose was streamed before the raise"
    )

    events = await _LeadHarness(frames).run()
    assert any(isinstance(e, RunFailed) for e in events), (
        f"no agent_done must surface as RunFailed; events={events}"
    )
    assert not any(isinstance(e, Presentation) for e in events), (
        f"a failed turn must not invent a reply; events={events}"
    )
