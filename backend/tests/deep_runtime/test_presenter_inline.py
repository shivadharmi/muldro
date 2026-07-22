"""Step 7B1 P4 (Fork-1): deep-only, off-by-default inline-format augmentation.

Fork-1 resolved Presenter -> INLINE-PROMPT: the frozen deep stream contract makes the
user-facing reply come ONLY from the lead's ``AIMessageChunk`` text (a tool result is a
``tool_result`` frame, never ``text_delta``). So a deep lead that formats inline must carry
the Presenter voice in its SYSTEM PROMPT. ``_augment_system_blocks_for_inline`` appends the
extracted ``PRESENTER_VOICE`` fragment to the deep system blocks when
``deep_inline_format`` is on — building a NEW list so the legacy agent_loop's shared
``system_blocks`` stay byte-identical.

This module proves:
  1. helper on/off — appends a PRESENTER_VOICE block when True; identity when False;
  2. immutability — the input block list is never mutated;
  3. forced-on integration (promotes spike 0.1, backend/spikes/deep_collapse/
     inline_format_probe.py) — with the augmentation on, a deep agent driven by a scripted
     streaming fake model emits a reply + fenced ``json:surface`` block that the existing
     surface parsers consume; plus a NEGATIVE CONTROL that PRESENTER_VOICE is absent from
     the built deep system prompt when the flag is off (the augmentation is gated).
"""

from __future__ import annotations

import json
from collections.abc import AsyncIterator
from typing import Any

from deepagents import create_deep_agent
from langchain_core.callbacks import AsyncCallbackManagerForLLMRun
from langchain_core.language_models.chat_models import BaseChatModel
from langchain_core.messages import AIMessage, AIMessageChunk, BaseMessage
from langchain_core.outputs import ChatGeneration, ChatGenerationChunk, ChatResult
from langgraph.checkpoint.memory import MemorySaver

from src.deep_runtime.prompt_bridge import build_system_message, flatten_system_blocks
from src.deep_runtime.stream_adapter import stream_deep_agent_events
from src.orchestrator.agent_invoker import _augment_system_blocks_for_inline
from src.orchestrator.prompts import PRESENTER_VOICE
from src.services.surface_mapping import extract_surface_spec, strip_surface_blocks
from tests.conftest import make_mock_settings

MODEL_ID = "claude-sonnet-4-6"
AGENT_NAME = "presenter"

# --- the target: a Presenter reply + a fenced surface block (from spike 0.1) --------
REPLY_TEXT = "Here is your summary."
SURFACE_JSON = json.dumps({"should_surface": True, "kind": "summary", "title": "Weekly Summary"})
FENCE_OPEN = "```json:surface"
FENCE_CLOSE = "```"
FULL_TEXT = f"{REPLY_TEXT}\n\n{FENCE_OPEN}\n{SURFACE_JSON}\n{FENCE_CLOSE}"


def _chunks(text: str, size: int = 7) -> list[str]:
    """Split *text* into fixed-size slices so fence/JSON boundaries land MID-chunk."""
    return [text[i : i + size] for i in range(0, len(text), size)]


class _SurfaceStreamingFakeModel(BaseChatModel):
    """Streams REPLY_TEXT + a fenced surface block as many small text deltas (fence + JSON
    split mid-chunk), then a terminal usage/stop chunk. No tool calls. Fully offline."""

    @property
    def _llm_type(self) -> str:
        return "scripted-surface-fake"

    def bind_tools(self, tools, **kwargs):  # noqa: ANN001, ANN003, ARG002
        return self

    @staticmethod
    def _script() -> list[AIMessageChunk]:
        chunks: list[AIMessageChunk] = [
            AIMessageChunk(content=[{"type": "text", "text": piece, "index": 0}])
            for piece in _chunks(FULL_TEXT)
        ]
        chunks.append(
            AIMessageChunk(
                content=[],
                usage_metadata={
                    "input_tokens": 90,
                    "output_tokens": 30,
                    "total_tokens": 120,
                    "input_token_details": {"cache_read": 0, "cache_creation": 0},
                },
                response_metadata={"model_name": MODEL_ID, "stop_reason": "end_turn"},
            )
        )
        return chunks

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
        raise NotImplementedError("sync generate not used in this async test")


# --- 1. helper on/off -----------------------------------------------------------------
def test_augment_on_appends_presenter_voice_block():
    """When inline_format is True (for the reply lead), a NEW list is returned with a
    PRESENTER_VOICE block."""
    blocks = [{"type": "text", "text": "soul+role"}]
    out = _augment_system_blocks_for_inline(blocks, True, is_reply_lead=True)

    assert out is not blocks  # new list, not the input
    assert len(out) == len(blocks) + 1
    assert out[:-1] == blocks  # original blocks preserved, in order
    assert any(b.get("text") == PRESENTER_VOICE for b in out)
    assert out[-1] == {"type": "text", "text": PRESENTER_VOICE}


def test_augment_off_is_identity():
    """When inline_format is False, the input is returned unchanged (byte-neutral)."""
    blocks = [{"type": "text", "text": "soul+role"}]
    out = _augment_system_blocks_for_inline(blocks, False)

    assert out is blocks  # identity — no copy, no augmentation
    assert not any(b.get("text") == PRESENTER_VOICE for b in out)


def test_augment_is_idempotent_when_voice_already_present():
    """When a block ALREADY carries PRESENTER_VOICE (the presenter's own prompt), the
    augmentation is a no-op: the voice is NOT injected a second time and the input is
    returned unchanged."""
    blocks = [{"type": "text", "text": f"You are the Presenter.\n\n{PRESENTER_VOICE}"}]
    out = _augment_system_blocks_for_inline(blocks, True, is_reply_lead=True)

    assert out is blocks  # identity — no copy, no second injection
    # PRESENTER_VOICE appears exactly once across all block texts.
    joined = "".join(b.get("text", "") for b in out)
    assert joined.count(PRESENTER_VOICE) == 1


# --- 2. immutability ------------------------------------------------------------------
def test_augment_does_not_mutate_input():
    """The input block list (shared with the legacy agent_loop) must never be mutated."""
    blocks = [{"type": "text", "text": "soul+role"}]
    before = list(blocks)

    _augment_system_blocks_for_inline(blocks, True, is_reply_lead=True)

    assert blocks == before  # unchanged
    assert len(blocks) == 1


# --- 3. forced-on integration (promotes spike 0.1) + negative control -----------------
async def test_inline_format_on_streams_reply_and_surface():
    """With deep_inline_format=True, a deep agent whose system prompt carries the
    augmentation streams a reply + surface that the existing surface parsers consume."""
    settings = make_mock_settings(runtime="deep", deep_inline_format=True)
    base_blocks = [{"type": "text", "text": "You are Jarvis's Presenter."}]

    # is_reply_lead=True (the presenter is the reply-producing lead) → voice appended.
    augmented = _augment_system_blocks_for_inline(
        base_blocks, settings.deep_inline_format, is_reply_lead=True
    )
    system = build_system_message(augmented)
    # the augmentation actually reached the built deep system prompt
    assert PRESENTER_VOICE in flatten_system_blocks(system.content)

    agent = create_deep_agent(
        model=_SurfaceStreamingFakeModel(),
        tools=[],
        system_prompt=system,
        checkpointer=MemorySaver(),
    )

    frames: list[dict] = []
    async for frame in stream_deep_agent_events(
        agent,
        {"messages": [{"role": "user", "content": "summarize"}]},
        {"configurable": {"thread_id": "inline-on"}},
        agent_name=AGENT_NAME,
        model=MODEL_ID,
    ):
        frames.append(frame)

    done = next((f for f in frames if f.get("event") == "agent_done"), None)
    assert done is not None, "no agent_done frame — stream did not complete"
    done_text = done.get("text") or ""

    # reply text survives streaming and strips clean; surface spec parses.
    assert strip_surface_blocks(done_text) == REPLY_TEXT
    assert FENCE_OPEN not in strip_surface_blocks(done_text)
    spec = extract_surface_spec(done_text)
    assert spec is not None
    assert spec.should_surface is True
    assert spec.kind == "summary"
    assert spec.title == "Weekly Summary"


def test_inline_format_off_negative_control():
    """NEGATIVE CONTROL: with deep_inline_format=False, PRESENTER_VOICE is absent from the
    built deep system prompt — the augmentation is gated by the flag."""
    settings = make_mock_settings(runtime="deep", deep_inline_format=False)
    base_blocks = [{"type": "text", "text": "You are Jarvis's Presenter."}]

    off_blocks = _augment_system_blocks_for_inline(base_blocks, settings.deep_inline_format)
    system_off = build_system_message(off_blocks)

    assert PRESENTER_VOICE not in flatten_system_blocks(system_off.content)
