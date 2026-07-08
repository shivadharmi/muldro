"""SPIKE probe (Step 7B1, Task 0.1 — DECISION GATE): prove a real deepagents
``create_deep_agent`` driven by a scripted *streaming* fake ``BaseChatModel`` can
emit a Presenter-voice chat reply followed by a fenced ``json:surface`` block, and
that the block survives token-by-token streaming intact — i.e. after the frames are
reconstructed by ``stream_deep_agent_events``:

* the reply text arrives as ``text_delta`` frames (and the joined ``agent_done.text``
  contains it),
* ``strip_surface_blocks(agent_done.text)`` yields the clean chat reply (block gone),
* ``extract_surface_spec(agent_done.text)`` parses the block and ``.should_surface is True``.

THROWAWAY investigation probe. Runs fully OFFLINE — no Anthropic API key, no real
LLM. The fake model streams the reply + surface block as many small text deltas,
deliberately splitting the ``` ```json:surface ``` fence marker AND the JSON body
across chunk boundaries, so this genuinely tests that streaming reconstruction does
not mangle the machine-readable block.

Run (from backend/):
    uv run python spikes/deep_collapse/inline_format_probe.py

The 7B1 assumption under test: the deep INLINE-FORMAT approach (Presenter emits chat
reply + fenced surface spec in ONE streamed message, no separate surface-builder
call) is viable because the surface fence survives streaming and the existing
``surface_mapping`` parsers accept it unchanged.
"""

from __future__ import annotations

import asyncio
import json
import os
import sys
from collections.abc import AsyncIterator
from typing import Any

# Standalone script: put backend/ (three dirs up) on sys.path so `src.*` imports
# resolve regardless of cwd (python puts the SCRIPT dir on sys.path[0], not cwd).
sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.dirname(os.path.abspath(__file__)))))

from deepagents import create_deep_agent
from langchain_core.callbacks import AsyncCallbackManagerForLLMRun
from langchain_core.language_models.chat_models import BaseChatModel
from langchain_core.messages import AIMessage, AIMessageChunk, BaseMessage
from langchain_core.outputs import ChatGeneration, ChatGenerationChunk, ChatResult
from langgraph.checkpoint.memory import MemorySaver

from src.deep_runtime.prompt_bridge import build_system_message
from src.deep_runtime.stream_adapter import stream_deep_agent_events
from src.services.surface_mapping import extract_surface_spec, strip_surface_blocks

MODEL_ID = "claude-sonnet-4-6"
AGENT_NAME = "presenter"

# --- the target: a Presenter reply + a fenced surface block ------------------
# The exact fence syntax + JSON shape are dictated by surface_mapping.py:
#   _SURFACE_SPEC_RE = re.compile(r"```json:surface\s*\n(.*?)\n```", re.DOTALL)
#   SurfaceSpec requires `kind` (a valid SurfaceKind) + `title`; `should_surface`
#   defaults to False, so we set it True explicitly.
REPLY_TEXT = "Here is your summary."
SURFACE_JSON = json.dumps(
    {"should_surface": True, "kind": "summary", "title": "Weekly Summary"}
)
FENCE_OPEN = "```json:surface"
FENCE_CLOSE = "```"
FULL_TEXT = f"{REPLY_TEXT}\n\n{FENCE_OPEN}\n{SURFACE_JSON}\n{FENCE_CLOSE}"


def _chunks(text: str, size: int = 7) -> list[str]:
    """Split *text* into fixed-size slices so fence/JSON boundaries land MID-chunk."""
    return [text[i : i + size] for i in range(0, len(text), size)]


# ---------------------------------------------------------------------------
# Scripted streaming fake chat model: streams REPLY_TEXT + surface block as many
# small text deltas, then a terminal usage/stop chunk. No tool calls (tools=[]).
# ---------------------------------------------------------------------------
class SurfaceStreamingFakeModel(BaseChatModel):
    @property
    def _llm_type(self) -> str:
        return "scripted-surface-fake"

    def bind_tools(self, tools, **kwargs):  # noqa: ANN001, ANN003, ARG002
        return self  # calls are scripted; ignore bound tools (none here anyway)

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
        raise NotImplementedError("sync generate not used in this async spike")


async def main() -> None:
    system = build_system_message(
        [
            {
                "type": "text",
                "text": (
                    "You are Jarvis's Presenter. Answer the user in a natural, concise "
                    "voice. When a workspace surface is warranted, append ONE fenced "
                    "```json:surface block with the SurfaceSpec after your reply."
                ),
            }
        ]
    )

    agent = create_deep_agent(
        model=SurfaceStreamingFakeModel(),
        tools=[],
        system_prompt=system,
        checkpointer=MemorySaver(),
    )

    frames: list[dict] = []
    async for frame in stream_deep_agent_events(
        agent,
        {"messages": [{"role": "user", "content": "summarize"}]},
        {"configurable": {"thread_id": "spike"}},
        agent_name=AGENT_NAME,
        model=MODEL_ID,
    ):
        frames.append(frame)

    print("=" * 78)
    print("SPIKE 0.1 — deep inline-format streams reply + surface block")
    print("=" * 78)
    print(f"\nFULL_TEXT the model streamed (repr):\n  {FULL_TEXT!r}")
    print(f"\nStreamed in {len(_chunks(FULL_TEXT))} text deltas of <=7 chars "
          "(fence + JSON split MID-chunk).")

    text_deltas = [f for f in frames if f.get("event") == "text_delta"]
    done = next((f for f in frames if f.get("event") == "agent_done"), None)
    err = next((f for f in frames if f.get("event") == "error"), None)

    print(f"\nframe event sequence: {[f['event'] for f in frames]}")
    print(f"text_delta frame count: {len(text_deltas)}")
    print(f"first 3 text_delta texts: {[d.get('text') for d in text_deltas[:3]]}")
    if err is not None:
        print(f"\n!! error frame emitted (unexpected): {err}")

    assert done is not None, "no agent_done frame — stream did not complete normally"
    done_text = done.get("text") or ""
    joined_deltas = "".join(d.get("text", "") for d in text_deltas)

    print(f"\nagent_done.text (repr):\n  {done_text!r}")

    stripped = strip_surface_blocks(done_text)
    spec = extract_surface_spec(done_text)

    print(f"\nstrip_surface_blocks(agent_done.text) -> {stripped!r}")
    print(f"extract_surface_spec(agent_done.text) -> {spec!r}")
    if spec is not None:
        print(f"  spec.should_surface = {spec.should_surface}")
        print(f"  spec.kind           = {spec.kind}")
        print(f"  spec.title          = {spec.title}")

    # --- assertions ---------------------------------------------------------
    # (a) reply text arrives as text_delta frames AND the joined agent_done.text
    #     contains the reply.
    assert text_deltas, "no text_delta frames — reply never surfaced as streamed text"
    assert REPLY_TEXT in joined_deltas, (
        "joined text_delta frames do not contain the reply text "
        f"(joined={joined_deltas!r})"
    )
    assert REPLY_TEXT in done_text, "agent_done.text does not contain the reply text"
    assert done_text == FULL_TEXT, (
        "agent_done.text != FULL_TEXT — streaming reconstruction MANGLED the text "
        f"(got {done_text!r})"
    )
    # (b) strip_surface_blocks yields the clean chat reply with the fence removed.
    assert stripped == REPLY_TEXT, (
        f"strip_surface_blocks did not yield the clean reply: {stripped!r}"
    )
    assert FENCE_OPEN not in stripped, "surface fence leaked into the clean reply"
    # (c) extract_surface_spec parses the block and should_surface is True.
    assert spec is not None, (
        "extract_surface_spec returned None — the surface block did NOT survive "
        "streaming intact"
    )
    assert spec.should_surface is True, "parsed SurfaceSpec.should_surface is not True"
    assert spec.kind == "summary"
    assert spec.title == "Weekly Summary"

    print("\nSPIKE 0.1 PASS")


if __name__ == "__main__":
    asyncio.run(main())
