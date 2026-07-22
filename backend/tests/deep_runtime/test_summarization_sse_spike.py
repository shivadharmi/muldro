"""Regression guard (originally Step 8 Phase 0 Task 0.1 spike, now a
permanent test): summarization-tagged model-call chunks must never leak
into the frozen ``text_delta`` SSE frames, and normal chunks must survive
the filter untouched.

LangChain's summarization middleware issues a nested/same-graph
``model.ainvoke`` call tagged ``metadata={"lc_source": "summarization"}``.
LangGraph relays that call through the SAME "messages" stream as ordinary
turn chunks, so without a filter ``stream_deep_agent_events``
(src/deep_runtime/stream_adapter.py) would emit the summarization chunk's
text straight into a user-visible ``text_delta`` frame (and double-count
its usage into ``agent_done`` telemetry).

The Phase 3 fix (commit 25f7e16) filters on ``payload[1]["lc_source"] ==
"summarization"`` in the "messages" stream-mode branch of
``stream_deep_agent_events``. This test drives the REAL
``stream_deep_agent_events`` over a minimal fake agent whose ``astream``
yields exactly the ``(mode, (chunk, metadata))`` tuple shape LangGraph
produces under ``stream_mode=["messages", "updates"]``, and asserts both
that the summarization text is absent AND that the surrounding normal
chunks are preserved intact — guarding against both under-filtering (leak
regresses) and over-filtering (normal chunks wrongly dropped).
"""

from __future__ import annotations

from collections.abc import AsyncIterator
from typing import Any

from langchain_core.messages import AIMessageChunk

from src.deep_runtime.stream_adapter import stream_deep_agent_events


class _FakeAgent:
    """Mimics a compiled LangGraph agent's ``astream`` just enough to drive
    the adapter's "messages" branch with a scripted sequence of
    ``(message_chunk, metadata)`` tuples — the exact shape LangGraph's
    ``stream_mode="messages"`` yields (message half + per-chunk metadata half).
    """

    def __init__(self, events: list[tuple[str, Any]]) -> None:
        self._events = events

    async def astream(
        self, graph_input: Any, config: dict | None = None, **kwargs: Any
    ) -> AsyncIterator[tuple[str, Any]]:
        for ev in self._events:
            yield ev


def _scripted_events() -> list[tuple[str, tuple[AIMessageChunk, dict]]]:
    """Normal chunk, then a summarization-sourced chunk, then another normal
    chunk — mirroring a mid-turn nested summarization call sandwiched inside
    an otherwise ordinary streamed response.
    """
    normal_1 = AIMessageChunk(content="hello ")
    summarization_chunk = AIMessageChunk(content="[[internal summary]] ")
    normal_2 = AIMessageChunk(content="world")
    return [
        ("messages", (normal_1, {"lc_source": None})),
        ("messages", (summarization_chunk, {"lc_source": "summarization"})),
        ("messages", (normal_2, {"lc_source": None})),
    ]


async def test_summarization_chunks_are_filtered_from_sse_frames():
    """Guards the Phase 3 ``lc_source == "summarization"`` filter in
    ``stream_adapter.py``: internal summarization model-call chunks must be
    kept out of the frozen SSE ``text_delta`` frames, while normal chunks
    pass through unaffected. See module docstring for background.
    """
    agent = _FakeAgent(_scripted_events())
    config = {"configurable": {"thread_id": "spike-t1"}}

    frames = [
        f
        async for f in stream_deep_agent_events(
            agent,
            {"messages": [{"role": "user", "content": "hi"}]},
            config,
            agent_name="presenter",
            model="claude-sonnet-4-6",
        )
    ]

    text_delta_text = "".join(f["text"] for f in frames if f.get("event") == "text_delta")

    assert "[[internal summary]]" not in text_delta_text, (
        "REGRESSION: summarization chunk leaked into text_delta frames — "
        f"Phase 3 filter is broken. Full text_delta stream was: {text_delta_text!r}"
    )
    assert text_delta_text == "hello world", (
        "OVER-FILTERING: normal chunks did not survive the summarization filter "
        f"intact. Full text_delta stream was: {text_delta_text!r}"
    )
