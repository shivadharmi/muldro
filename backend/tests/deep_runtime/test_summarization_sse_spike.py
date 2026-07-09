"""Step 8 Phase 0 Task 0.1 — OFFLINE spike: does a nested/same-graph
``model.ainvoke`` call made by LangChain's summarization middleware
(tagged ``metadata={"lc_source": "summarization"}``) leak into the
frozen ``text_delta`` SSE frames?

Static-analysis hypothesis: ``stream_deep_agent_events``
(src/deep_runtime/stream_adapter.py:172-174) discards ``payload[1]`` (the
per-chunk metadata half) in the "messages" stream-mode branch:

    msg = payload[0] if isinstance(payload, tuple) else payload

If LangGraph relays a same-graph nested ``model.ainvoke`` call (as the
summarization middleware makes internally) through the SAME "messages"
stream with ``metadata={"lc_source": "summarization", ...}`` riding on
``payload[1]``, the adapter has no way to distinguish it from a normal
turn's chunk — it will emit the summarization chunk's text straight into
a user-visible ``text_delta`` frame (and double-count its usage into
``agent_done`` telemetry).

This spike drives the REAL ``stream_deep_agent_events`` over a minimal
fake agent whose ``astream`` yields exactly the ``(mode, (chunk, metadata))``
tuple shape LangGraph produces under ``stream_mode=["messages", "updates"]``,
and asserts on the resulting frames.

VALID OUTCOMES (this is a spike, not a feature — a FAIL is an expected,
useful result, not a bug in this test):
  - FAIL -> leak confirmed: a Phase 3 filter on ``payload[1]["lc_source"]``
    is required before summarization middleware ships on the deep path.
  - PASS -> the adapter, as it stands today, already happens not to leak
    this specific vector; no filter is needed for it.

Do NOT "fix" stream_adapter.py here to force a pass — that is Phase 3,
explicitly out of scope for this spike.
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


async def test_summarization_chunk_leaks_into_frozen_text_delta_frames():
    """OFFLINE spike (Step 8 P0 Task 0.1). See module docstring for the
    hypothesis and valid-outcomes framing — a FAIL here is a confirmed leak,
    not a defect in the test.
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
        "LEAK CONFIRMED: summarization chunk leaked into text_delta frames — "
        f"P3 filter REQUIRED. Full text_delta stream was: {text_delta_text!r}"
    )
