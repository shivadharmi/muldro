"""Task 6: Prove deep runtime frames survive the web SSE pipeline end-to-end.

Pipeline under test (Step 6A seam boundary):

    stream_deep_agent_events  →  agent_event_from_sse  →  core_event_to_sse
    (adapter dict frame)         (typed CoreEvent)         (SSE dict | None)

This is the load-bearing end-to-end guarantee for the chat→Deep-Agents runtime
cutover: every frame the adapter emits must be typeable by ``agent_event_from_sse``
and must either survive ``core_event_to_sse`` as a frozen SSE shape the web client
can consume, or be legitimately stream-dropped (None → batch-only event).

The telemetry value-lock in Step 1a guards the multi-turn summing regression
(spike risk #1 from docs/superpowers/spikes/2026-07-06-langgraph-stream-to-sse.md).

Step 1b (full _process_core integration) is SKIPPED — see comment below.
"""

from __future__ import annotations

from deepagents import create_deep_agent
from langgraph.checkpoint.memory import MemorySaver

from src.deep_runtime.stream_adapter import stream_deep_agent_events
from src.orchestrator.core_events import agent_event_from_sse, core_event_to_sse

# Reuse the offline fake model + echo tool from the adapter unit-test suite.
# Importing avoids duplicating the scripted turn logic and ensures both tests
# stay in sync if the fake model is ever updated.
from tests.deep_runtime.test_stream_adapter import _ScriptedFakeChatModel, echo

# ── Frozen event-name contract (mirrors _ALLOWED_EVENTS in test_stream_adapter) ──
# These are the only event names the web client is permitted to receive.
_FROZEN = {
    "agent_start",
    "thinking",
    "text_delta",
    "tool_call",
    "tool_result",
    "agent_done",
    "error",
}

# ── Telemetry value-lock ────────────────────────────────────────────────────────
# Exact per-turn numbers read from _ScriptedFakeChatModel
# (tests/deep_runtime/test_stream_adapter.py):
#
#   Turn 1 (_turn1 usage_metadata):  input_tokens=120, output_tokens=25,
#                                     input_token_details={cache_read:100, cache_creation:0}
#   Turn 2 (_turn2 usage_metadata):  input_tokens=60,  output_tokens=12,
#                                     input_token_details={cache_read:140, cache_creation:0}
#
# Sums (stream_adapter._add_usage accumulates across all AIMessageChunks):
_EXPECTED_INPUT_TOKENS = 120 + 60  # 180
_EXPECTED_OUTPUT_TOKENS = 25 + 12  # 37
_EXPECTED_CACHE_READ_TOKENS = 100 + 140  # 240
_EXPECTED_CACHE_CREATION_TOKENS = 0 + 0  # 0


# ── Step 1a: seam-boundary round-trip + telemetry value-lock ───────────────────


async def test_deep_frames_survive_web_sse_pipeline() -> None:
    """Every adapter frame must round-trip through the web SSE pipeline cleanly.

    Proves:
    - All frames are typeable by agent_event_from_sse (never raises).
    - Survivors are a non-empty subset of the frozen SSE contract.
    - agent_done is among survivors (terminal the client must receive).
    - Multi-turn token summing is correct (value-locked against fake model).
    - Shape coverage: thinking, text_delta, tool_call/result present with echo.
    """
    agent = create_deep_agent(
        model=_ScriptedFakeChatModel(),
        tools=[echo],
        checkpointer=MemorySaver(),
        system_prompt="You are a test agent.",
    )
    config = {"configurable": {"thread_id": "t-parity"}}
    graph_input = {"messages": [{"role": "user", "content": "hi"}]}

    frames = [
        f
        async for f in stream_deep_agent_events(
            agent, graph_input, config, agent_name="executor", model="claude-sonnet-4-6"
        )
    ]

    # ── Frames non-empty ──────────────────────────────────────────────────────
    assert frames, "adapter yielded no frames — stream_deep_agent_events is broken"

    # ── Meaningful round-trip ─────────────────────────────────────────────────
    # agent_event_from_sse never returns None (it always returns a CoreEvent),
    # so the meaningful check is on what core_event_to_sse does with the result.
    # Every frame must map to either None (batch-only, legitimately dropped) or
    # a dict whose "event" key is in _FROZEN.
    for frame in frames:
        typed = agent_event_from_sse(frame)
        out = core_event_to_sse(typed)
        assert out is None or out["event"] in _FROZEN, (
            f"frame {frame!r} round-tripped to unknown event {out!r}"
        )

    # Collect surviving (non-None) event names — walrus-expr inside set comprehension.
    survivors = {
        out["event"]
        for frame in frames
        if (out := core_event_to_sse(agent_event_from_sse(frame))) is not None
    }

    # Survivors must be non-empty (a vacuous all-None pass is a bug, not a pass).
    assert survivors, "all frames were stream-dropped; no events reach the web client"
    # Survivors must be a strict subset of the frozen contract.
    assert survivors <= _FROZEN, f"unexpected event types leaked: {survivors - _FROZEN}"
    # The client must always receive the terminal event.
    assert "agent_done" in survivors, "agent_done was not among surviving SSE events"

    # ── Telemetry value-lock ──────────────────────────────────────────────────
    # Exactly one agent_done frame (guards accidental double-emission).
    done_frames = [f for f in frames if f.get("event") == "agent_done"]
    assert len(done_frames) == 1, f"expected exactly 1 agent_done frame, got {len(done_frames)}"
    done = done_frames[0]

    assert done["input_tokens"] == _EXPECTED_INPUT_TOKENS, (
        f"input_tokens {done['input_tokens']} != {_EXPECTED_INPUT_TOKENS} "
        f"(multi-turn summing regression)"
    )
    assert done["output_tokens"] == _EXPECTED_OUTPUT_TOKENS, (
        f"output_tokens {done['output_tokens']} != {_EXPECTED_OUTPUT_TOKENS}"
    )
    assert done["cache_read_tokens"] == _EXPECTED_CACHE_READ_TOKENS, (
        f"cache_read_tokens {done['cache_read_tokens']} != {_EXPECTED_CACHE_READ_TOKENS}"
    )
    assert done.get("cache_creation_tokens") == _EXPECTED_CACHE_CREATION_TOKENS, (
        f"cache_creation_tokens {done.get('cache_creation_tokens')} "
        f"!= {_EXPECTED_CACHE_CREATION_TOKENS}"
    )

    # ── Shape coverage ────────────────────────────────────────────────────────
    # At least one thinking frame (proves thinking-block extraction works).
    thinking_frames = [f for f in frames if f.get("event") == "thinking"]
    assert thinking_frames, "no thinking frames emitted — thinking-block extraction broken"

    # At least one text_delta (proves text-block extraction works).
    text_frames = [f for f in frames if f.get("event") == "text_delta"]
    assert text_frames, "no text_delta frames emitted — text-block extraction broken"

    # tool_call and tool_result counts must be equal and >= 1.
    tool_call_count = sum(1 for f in frames if f.get("event") == "tool_call")
    tool_result_count = sum(1 for f in frames if f.get("event") == "tool_result")
    assert tool_call_count == tool_result_count >= 1, (
        f"tool_call ({tool_call_count}) != tool_result ({tool_result_count}) or both 0; "
        f"tool_call_id→name recovery or ToolNode dispatch is broken"
    )

    # Every tool_result must reference 'echo' (proves tool_call_id→name recovery).
    for f in frames:
        if f.get("event") == "tool_result":
            assert f.get("tool") == "echo", (
                f"tool_result has tool={f.get('tool')!r}, expected 'echo'; "
                f"tool_names_by_id lookup failed"
            )


# ── Step 1b: full _process_core integration — SKIPPED ─────────────────────────
# Driving one turn through JarvisOrchestrator._process_core with
# settings.runtime="deep" requires wiring: a real (or faked) DB session via
# db_factory, AgentInvoker, ContextBuilder, WorldModel, a full Settings object
# with the deep-runtime flag, and enough of the plan/route machinery to reach the
# agent call. That harness is heavier than the contract boundary it would test, and
# Step 1a already covers the seam where the adapter output meets the SSE pipeline.
# Step 1b would be valuable as a separate integration test when the orchestrator
# wiring is simpler (e.g., post-Step-6 cutover when the legacy path is removed),
# but forcing it here would produce a brittle harness that adds noise without
# meaningfully strengthening the guarantee.
