"""Provider-neutrality matrix for ``stream_deep_agent_events`` (spec §11 / finding L3).

``stream_adapter.py`` was written against Anthropic stream frames; §11 says streaming is
"provider-neutral in principle but verify per provider." This test drives the adapter with
fake chat models shaped like DIFFERENT providers and asserts it produces the same frozen SSE
frames regardless of provider-specific chunk shape — WITHOUT raising on Anthropic-only keys.

The three shapes that actually differ between providers as they surface through
``langchain-*`` streaming:

* ``AIMessageChunk.content`` — Anthropic yields a ``list`` of content-block dicts
  (``{"type": "text", ...}``); langchain-openai / langchain-google-genai coerce text
  deltas to a plain ``str``.
* ``usage_metadata`` — Anthropic populates ``input_token_details`` with ``cache_read`` /
  ``cache_creation`` sub-keys; OpenAI omits ``input_token_details`` entirely; Gemini may
  send an empty ``input_token_details``. These Anthropic-only sub-keys are exactly what a
  naive adapter would ``[]``-index and KeyError on.
* ``tool_calls`` — LangChain normalizes every provider's tool call into the same
  ``{"name", "args", "id"}`` shape, so the adapter's tool frames should be provider-neutral
  for free; this test verifies that empirically.

Mocking style is copied from ``test_stream_adapter.py`` (fake ``BaseChatModel`` streamed
through ``deepagents.create_deep_agent`` — fully offline, no provider API).
"""

from __future__ import annotations

import json
from collections.abc import AsyncIterator, Callable
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


# --- Per-provider content shape --------------------------------------------------------
# The one place the raw stream chunk differs enough to break a naive adapter.


def _anthropic_content(text: str) -> list[dict]:
    """Anthropic surfaces text deltas as content-block dicts."""
    return [{"type": "text", "text": text}] if text else []


def _plain_content(text: str) -> str:
    """OpenAI + Gemini surface text deltas as a coerced plain string."""
    return text


# --- Per-provider usage_metadata shape -------------------------------------------------
# Anthropic carries cache sub-keys under input_token_details; the others do not.

_ANTHROPIC_USAGE: dict[str, Any] = {
    "input_tokens": 120,
    "output_tokens": 25,
    "total_tokens": 145,
    "input_token_details": {"cache_read": 100, "cache_creation": 0},
}
_OPENAI_USAGE: dict[str, Any] = {
    # No input_token_details at all — the KeyError trap for a naive adapter.
    "input_tokens": 120,
    "output_tokens": 25,
    "total_tokens": 145,
}
_GEMINI_USAGE: dict[str, Any] = {
    # input_token_details present but WITHOUT Anthropic's cache sub-keys.
    "input_tokens": 120,
    "output_tokens": 25,
    "total_tokens": 145,
    "input_token_details": {},
}

# provider key -> (content_fn, usage_metadata, model_id)
_PROVIDERS: dict[str, tuple[Callable[[str], Any], dict[str, Any], str]] = {
    "anthropic": (_anthropic_content, _ANTHROPIC_USAGE, "claude-sonnet-4-6"),
    "openai": (_plain_content, _OPENAI_USAGE, "gpt-4o"),
    "gemini": (_plain_content, _GEMINI_USAGE, "gemini-1.5-pro"),
}

# The user-visible text every provider must reproduce identically.
_EXPECTED_TEXT = "Let me echo that.Done — echoed 'hello'."


def _token_text(chunk: AIMessageChunk) -> str:
    if isinstance(chunk.content, str):
        return chunk.content
    parts: list[str] = []
    for block in chunk.content:
        if isinstance(block, dict) and block.get("type") == "text":
            parts.append(block.get("text", ""))
    return "".join(parts)


def _make_provider_model(
    content_fn: Callable[[str], Any], usage_meta: dict[str, Any], model_id: str
) -> BaseChatModel:
    """Build a fake streaming chat model shaped like a given provider.

    Turn 1 = two text deltas + an ``echo`` tool call + a usage-bearing terminal chunk;
    turn 2 (after the ToolMessage lands) = a final text turn. The ONLY things that vary by
    provider are ``content_fn`` (str vs content-block list) and ``usage_meta``.
    """

    class _ProviderFakeChatModel(BaseChatModel):
        @property
        def _llm_type(self) -> str:
            return "provider-fake"

        def bind_tools(self, tools, **kwargs):  # noqa: ANN001, ANN003, ARG002
            return self

        def _turn1(self) -> list[AIMessageChunk]:
            return [
                AIMessageChunk(content=content_fn("Let me ")),
                AIMessageChunk(content=content_fn("echo that.")),
                AIMessageChunk(
                    content=content_fn(""),
                    tool_call_chunks=[
                        tool_call_chunk(
                            name="echo",
                            args=json.dumps({"text": "hello"}),
                            id="call_1",
                            index=0,
                        )
                    ],
                ),
                AIMessageChunk(
                    content=content_fn(""),
                    usage_metadata=usage_meta,
                    response_metadata={"model_name": model_id, "stop_reason": "tool_use"},
                ),
            ]

        def _turn2(self) -> list[AIMessageChunk]:
            return [
                AIMessageChunk(content=content_fn("Done — echoed ")),
                AIMessageChunk(content=content_fn("'hello'.")),
                AIMessageChunk(
                    content=content_fn(""),
                    usage_metadata=usage_meta,
                    response_metadata={"model_name": model_id, "stop_reason": "end_turn"},
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

    return _ProviderFakeChatModel()


def _make_exploding_model(content_fn: Callable[[str], Any], detail: str) -> BaseChatModel:
    base = _make_provider_model(content_fn, _OPENAI_USAGE, "gpt-4o")

    class _ExplodingModel(base.__class__):  # type: ignore[misc, name-defined]
        async def _astream(self, messages, stop=None, run_manager=None, **kwargs):  # noqa: ANN001
            raise RuntimeError(detail)
            yield  # pragma: no cover

    return _ExplodingModel()


async def _run_provider(provider_key: str) -> list[dict]:
    content_fn, usage_meta, model_id = _PROVIDERS[provider_key]
    agent = create_deep_agent(
        model=_make_provider_model(content_fn, usage_meta, model_id),
        tools=[echo],
        checkpointer=MemorySaver(),
        system_prompt="You are a test agent.",
    )
    config = {"configurable": {"thread_id": f"t-{provider_key}"}}
    return [
        frame
        async for frame in stream_deep_agent_events(
            agent,
            {"messages": [{"role": "user", "content": "hi"}]},
            config,
            agent_name="executor",
            model=model_id,
        )
    ]


def _joined_text_deltas(frames: list[dict]) -> str:
    return "".join(f["text"] for f in frames if f["event"] == "text_delta")


async def test_every_provider_yields_frozen_sse_shapes():
    """Each provider shape produces valid, typeable frames with no Anthropic-only crash."""
    from src.orchestrator.core_events import agent_event_from_sse

    for provider_key in _PROVIDERS:
        frames = await _run_provider(provider_key)
        assert frames, f"{provider_key}: adapter yielded nothing"
        for f in frames:
            assert f["event"] in _ALLOWED_EVENTS, f"{provider_key}: unexpected event {f['event']}"
            assert _REQUIRED_KEYS[f["event"]] <= set(f.keys()), f"{provider_key}: missing keys"
            assert f["agent"] == "executor"
            # No frame is an error frame — a provider-neutral stream must not blow up.
            assert f["event"] != "error", f"{provider_key}: adapter raised on provider shape"
        kinds = [f["event"] for f in frames]
        assert "text_delta" in kinds, f"{provider_key}: no text emitted"
        assert kinds.count("tool_call") == kinds.count("tool_result") >= 1, provider_key
        assert kinds.count("agent_done") == 1, f"{provider_key}: expected exactly one agent_done"
        # Frames must round-trip through the typed CoreEvent layer for every provider.
        for f in frames:
            assert agent_event_from_sse(f) is not None, f"{provider_key}: untypeable frame {f}"


async def test_text_content_is_provider_neutral():
    """Identical logical text produces identical text frames regardless of chunk shape."""
    joined_by_provider: dict[str, str] = {}
    done_text_by_provider: dict[str, str] = {}
    for provider_key in _PROVIDERS:
        frames = await _run_provider(provider_key)
        joined_by_provider[provider_key] = _joined_text_deltas(frames)
        done = [f for f in frames if f["event"] == "agent_done"]
        assert len(done) == 1
        done_text_by_provider[provider_key] = done[0]["text"]

    # Every provider reproduces the same user-visible text.
    assert set(joined_by_provider.values()) == {_EXPECTED_TEXT}, joined_by_provider
    assert set(done_text_by_provider.values()) == {_EXPECTED_TEXT}, done_text_by_provider


async def test_tool_frames_are_provider_neutral():
    """LangChain-normalized tool calls surface identically across providers."""
    for provider_key in _PROVIDERS:
        frames = await _run_provider(provider_key)
        tool_calls = [f for f in frames if f["event"] == "tool_call"]
        tool_results = [f for f in frames if f["event"] == "tool_result"]
        assert len(tool_calls) == 1, f"{provider_key}: expected one tool_call"
        assert tool_calls[0]["tool"] == "echo", provider_key
        assert tool_calls[0]["input"] == {"text": "hello"}, provider_key
        assert len(tool_results) == 1, f"{provider_key}: expected one tool_result"
        assert tool_results[0]["tool"] == "echo", provider_key
        assert tool_results[0]["blocked"] is False, provider_key


async def test_missing_anthropic_usage_keys_default_to_zero():
    """A provider whose usage omits input_token_details must not crash and must report 0 cache.

    This is the concrete Anthropic-only key trap: ``_add_usage`` reads
    ``input_token_details.cache_read`` / ``cache_creation``. OpenAI omits the sub-dict
    entirely and Gemini sends it empty — both must fold to 0 via ``.get()``, never KeyError.
    """
    for provider_key in ("openai", "gemini"):
        frames = await _run_provider(provider_key)
        done = next(f for f in frames if f["event"] == "agent_done")
        assert done["cache_creation_tokens"] == 0, provider_key
        assert done["cache_read_tokens"] == 0, provider_key
        # Non-cache token counts still flow through (summed across both turns).
        assert isinstance(done["input_tokens"], int) and done["input_tokens"] > 0, provider_key
        assert isinstance(done["output_tokens"], int) and done["output_tokens"] > 0, provider_key

    # Anthropic baseline: the cache sub-keys ARE carried through when present.
    anthropic_frames = await _run_provider("anthropic")
    anthropic_done = next(f for f in anthropic_frames if f["event"] == "agent_done")
    assert anthropic_done["cache_read_tokens"] > 0


async def test_stream_error_maps_to_frozen_error_frame_per_provider():
    """A raising stream sanitizes to the frozen error frame for any provider content shape."""
    for provider_key, (content_fn, _usage, model_id) in _PROVIDERS.items():
        agent = create_deep_agent(
            model=_make_exploding_model(content_fn, "boom-secret-detail"),
            tools=[echo],
            checkpointer=MemorySaver(),
            system_prompt="You are a test agent.",
        )
        config = {"configurable": {"thread_id": f"t-err-{provider_key}"}}
        frames = [
            f
            async for f in stream_deep_agent_events(
                agent,
                {"messages": [{"role": "user", "content": "hi"}]},
                config,
                agent_name="executor",
                model=model_id,
            )
        ]
        err = [f for f in frames if f["event"] == "error"]
        assert err, f"{provider_key}: no error frame emitted"
        for f in err:
            assert "boom-secret-detail" not in f["message"], f"{provider_key}: leaked raw detail"
            assert _REQUIRED_KEYS["error"] <= set(f.keys()), provider_key
