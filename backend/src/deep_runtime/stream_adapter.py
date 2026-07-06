"""Adapter: translate a compiled deep-agent LangGraph stream into the 7 frozen
chat SSE dict shapes that ``AgentInvoker.call_agent_stream`` emits today
(agent_invoker.py:183-235). This keeps ``agent_event_from_sse`` (core_events.py)
able to type the frames and preserves the frozen web contract during the Step 6A
chat-path runtime cutover.

Mechanism is fixed by the Task-0 spike
(docs/superpowers/spikes/2026-07-06-langgraph-stream-to-sse.md):

* stream with ``stream_mode=["messages", "updates"]``;
* telemetry via ``usage_metadata`` summed across ``AIMessage``s +
  ``BudgetTracker.calculate_cost`` + ``time.monotonic()``;
* ``blocked`` via ``ToolMessage.status == "error"``.

The 7 shapes (key-identical to the legacy LoopEvent → SSE mapping):

1. ``agent_start``  — synthesized BEFORE the stream (agent + model known).
2. ``thinking``     — ``AIMessageChunk`` content block ``type == "thinking"``.
3. ``text_delta``   — ``AIMessageChunk`` plain-str content OR block ``type == "text"``
   (Caveat A: content may be a plain ``str`` under ``coerce_content_to_string`` OR a
   ``list`` of block dicts on thinking turns — both are handled).
4. ``tool_call``    — full ``AIMessage.tool_calls`` in an ``updates`` payload (args are
   already parsed; no ``input_json_delta`` accumulation).
5. ``tool_result``  — ``ToolMessage`` in a ``messages`` payload. The tool name is
   recovered from ``tool_call_id`` (``ToolMessage.name`` is often ``None`` on denials);
   ``blocked`` ← ``status == "error"``; latency is monotonic-clocked from the matching
   ``tool_call``.
6. ``agent_done``   — synthesized AT stream end from the summed telemetry.
7. ``error``        — sanitized: the raw exception is logged, only a generic frame is
   emitted (a raising tool propagates out of ``astream`` and lands here).
8. ``approval_needed`` — Step 6B: emitted when a ``wrap_tool_call`` gate (e.g.
   ``trust_gate``) pauses the graph via ``interrupt()``. Empirically (Task-0 spike,
   ``spikes/deep_stream/interrupt_resume_stream_proof.py``, langgraph 1.2.6) this
   does NOT raise — ``astream`` yields an ``updates`` item shaped
   ``{"__interrupt__": (Interrupt(value={...}),)}`` and then ends normally. The adapter
   detects that item and yields this frame instead of falling through to a bogus
   ``agent_done``. A ``GraphInterrupt`` except-clause is kept as a harmless
   version-portability fallback (it does not fire on the currently installed
   langgraph). This is a stream DICT frame only — the typed ``CoreEvent`` counterpart
   in ``core_events.py`` and its HTTP-emission arms are deferred (Step 6B scope
   lever B); do not add them here.

is_thinking=False parity (spike concern #4): the LangGraph stream has no native signal
for legacy's ``thinking{is_thinking=False}`` relabel of pre-tool plain text; per the
spike we accept that deep-path pre-tool text maps to ``text_delta`` (a benign
divergence). No text-buffering re-labeler is built.
"""

from __future__ import annotations

import logging
import time
from collections.abc import AsyncGenerator
from typing import Any

from langchain_core.messages import AIMessage, AIMessageChunk, ToolMessage
from langgraph.errors import GraphInterrupt

from src.errors import _GENERIC_CODE, _GENERIC_MESSAGE, new_correlation_id
from src.middleware.observability import get_correlation_id
from src.orchestrator.budget import BudgetTracker

logger = logging.getLogger(__name__)

_ZERO_USAGE: dict[str, int] = {"input": 0, "output": 0, "cache_creation": 0, "cache_read": 0}


def _approval_needed_frame(agent_name: str, value: dict, config: dict) -> dict:
    """Build the ``approval_needed`` frame from a gate's ``interrupt(...)`` payload.

    ``value`` is the dict a ``wrap_tool_call`` gate (e.g. ``trust_gate``) passed to
    ``interrupt(...)`` — see ``src/deep_runtime/middleware/trust_gate.py``. ``thread_id``
    falls back to the LangGraph ``config`` when the gate payload omits it.
    """
    configurable = config.get("configurable", {}) or {}
    return {
        "event": "approval_needed",
        "agent": agent_name,
        "approval_id": value.get("approval_id"),
        "thread_id": value.get("thread_id") or configurable.get("thread_id"),
        "capability": value.get("capability"),
        "risk_level": value.get("risk_level"),
    }


def _content_events(content: Any) -> list[tuple[str, str]]:
    """Normalize ``AIMessageChunk.content`` into ``(kind, text)`` pairs.

    Handles Caveat A: ``content`` may be a plain ``str`` (non-thinking turns under
    ``coerce_content_to_string=True``) OR a ``list`` of content-block dicts (thinking
    turns). ``kind`` is ``"thinking"`` or ``"text"``. Empty text is skipped so we
    never emit blank deltas.
    """
    if isinstance(content, str):
        return [("text", content)] if content else []
    if not isinstance(content, list):
        return []
    events: list[tuple[str, str]] = []
    for block in content:
        if not isinstance(block, dict):
            continue
        btype = block.get("type")
        if btype == "thinking":
            text = block.get("thinking", "")
            if text:
                events.append(("thinking", text))
        elif btype == "text":
            text = block.get("text", "")
            if text:
                events.append(("text", text))
    return events


def _add_usage(usage: dict[str, int], um: dict[str, Any] | None) -> dict[str, int]:
    """Return a new usage total with *um* (an ``AIMessage.usage_metadata``) folded in.

    Summed across every ``AIMessage`` seen (reading only the terminal turn would
    under-count multi-turn runs). Immutable: never mutates *usage*.
    """
    if not um:
        return usage
    details = um.get("input_token_details") or {}
    return {
        "input": usage["input"] + (um.get("input_tokens") or 0),
        "output": usage["output"] + (um.get("output_tokens") or 0),
        "cache_creation": usage["cache_creation"] + (details.get("cache_creation") or 0),
        "cache_read": usage["cache_read"] + (details.get("cache_read") or 0),
    }


async def stream_deep_agent_events(
    agent: Any,
    graph_input: Any,
    config: dict,
    *,
    agent_name: str,
    model: str | None = None,
    durability: str | None = None,
) -> AsyncGenerator[dict, None]:
    """Stream *agent* and yield the 7 frozen chat SSE dicts (see module docstring).

    Args:
        agent: A ``CompiledStateGraph`` from ``build_deep_agent`` / ``create_deep_agent``.
        graph_input: ``{"messages": [{"role": "user", "content": message}]}`` on a fresh
            turn, or a ``Command(resume=...)`` to re-enter a paused turn (Step 6B).
        config: LangGraph config, e.g. ``{"configurable": {"thread_id": ...}}``.
        agent_name: The Jarvis agent name stamped onto every frame (``"agent"``).
        model: The model id (for the ``agent_start`` frame + cost attribution).
        durability: Forwarded to ``agent.astream(..., durability=...)`` when set (e.g.
            ``"sync"`` on the resume path, per the Task-0 interrupt/resume spike).
            Defaults to ``None``, which omits the kwarg entirely so every existing
            caller's behaviour is unchanged.

    Yields:
        Client-safe SSE dicts key-identical to ``AgentInvoker.call_agent_stream``.
    """
    # agent_start — synthesized before the stream (name + model are known up front).
    yield {"event": "agent_start", "agent": agent_name, "model": model}

    start = time.monotonic()
    text_parts: list[str] = []
    tools_called: list[str] = []
    tool_names_by_id: dict[str, str] = {}
    tool_started_at: dict[str, float] = {}
    usage = dict(_ZERO_USAGE)

    astream_kwargs: dict[str, Any] = {"stream_mode": ["messages", "updates"]}
    if durability is not None:
        astream_kwargs["durability"] = durability

    try:
        async for mode, payload in agent.astream(graph_input, config=config, **astream_kwargs):
            if mode == "messages":
                msg = payload[0] if isinstance(payload, tuple) else payload
                if isinstance(msg, AIMessageChunk):
                    for kind, text in _content_events(msg.content):
                        if kind == "thinking":
                            yield {
                                "event": "thinking",
                                "agent": agent_name,
                                "text": text,
                                "is_thinking": True,
                            }
                        else:
                            text_parts.append(text)
                            yield {"event": "text_delta", "agent": agent_name, "text": text}
                    usage = _add_usage(usage, msg.usage_metadata)
                elif isinstance(msg, ToolMessage):
                    tool_id = msg.tool_call_id
                    tool_name = tool_names_by_id.get(tool_id) or getattr(msg, "name", None) or ""
                    started = tool_started_at.get(tool_id, start)
                    yield {
                        "event": "tool_result",
                        "agent": agent_name,
                        "tool": tool_name,
                        "result": msg.content,
                        "blocked": getattr(msg, "status", None) == "error",
                        "latency_ms": max(0, int((time.monotonic() - started) * 1000)),
                    }
            elif mode == "updates":
                if isinstance(payload, dict) and "__interrupt__" in payload:
                    interrupts = payload["__interrupt__"]
                    value = interrupts[0].value if interrupts else {}
                    if not isinstance(value, dict):
                        value = {}
                    yield _approval_needed_frame(agent_name, value, config)
                    return
                for _node, update in (payload or {}).items():
                    if not isinstance(update, dict):
                        continue
                    for message in update.get("messages") or []:
                        if not (isinstance(message, AIMessage) and message.tool_calls):
                            continue
                        for call in message.tool_calls:
                            call_id = call.get("id")
                            name = call.get("name", "")
                            if call_id:
                                tool_names_by_id[call_id] = name
                                tool_started_at[call_id] = time.monotonic()
                            tools_called.append(name)
                            yield {
                                "event": "tool_call",
                                "agent": agent_name,
                                "tool": name,
                                "input": call.get("args", {}),
                            }
    except GraphInterrupt as gi:
        # Version-portability fallback ONLY: on the currently installed langgraph
        # (1.2.6) a gate's interrupt() does NOT raise — the "updates" branch above
        # detects the "__interrupt__" item and returns before this except is ever
        # reached (see the Task-0 spike). This clause exists so a future langgraph
        # bump that starts raising GraphInterrupt can't silently fall through to the
        # generic `except Exception` below and turn a pause into a client-visible
        # "error" frame.
        try:
            value = gi.args[0][0].value
        except (IndexError, AttributeError, TypeError):
            value = {}
        if not isinstance(value, dict):
            value = {}
        yield _approval_needed_frame(agent_name, value, config)
        return
    except Exception as exc:  # noqa: BLE001 — sanitize any upstream failure into a safe frame.
        # exc may carry raw upstream detail (a raising tool propagates here under
        # deepagents' default ToolNode). Log it, but emit only a client-safe frame.
        logger.error("deep stream error agent=%s: %s", agent_name, exc, exc_info=True)
        cid = get_correlation_id() or new_correlation_id()
        yield {
            "event": "error",
            "agent": agent_name,
            "code": _GENERIC_CODE,
            "message": _GENERIC_MESSAGE,
            "correlation_id": cid,
        }
        return

    # agent_done — synthesized at stream end from accumulated telemetry.
    cost = BudgetTracker().calculate_cost(
        model or "",
        usage["input"],
        usage["output"],
        cache_creation_input_tokens=usage["cache_creation"],
        cache_read_input_tokens=usage["cache_read"],
    )
    yield {
        "event": "agent_done",
        "agent": agent_name,
        "text": "".join(text_parts),
        "input_tokens": usage["input"],
        "output_tokens": usage["output"],
        "cache_creation_tokens": usage["cache_creation"],
        "cache_read_tokens": usage["cache_read"],
        "tools_called": tools_called or None,
        "latency_ms": int((time.monotonic() - start) * 1000),
        "cost_usd": round(cost, 6),
    }
