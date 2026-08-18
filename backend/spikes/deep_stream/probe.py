"""SPIKE probe (Step 6A, Task 0 — DECISION GATE): prove a compiled deepagents
LangGraph agent can stream token-by-token and that the stream can be
reconstructed into the 7 FROZEN chat-SSE dict shapes emitted today by
``AgentInvoker.call_agent_stream`` (agent_invoker.py:183-235).

THROWAWAY investigation probe. Runs fully OFFLINE — no Anthropic API key, no
real LLM. A custom ``BaseChatModel`` subclass streams scripted
``AIMessageChunk``s (thinking + text deltas + a ``tool_call`` on turn 1, then a
final text turn 2) so ``create_deep_agent`` actually executes the ``echo`` tool
and re-invokes the model — exercising the full multi-turn stream.

Run (from backend/):
    uv run python spikes/deep_stream/probe.py

Every prior repo spike used ``.ainvoke`` (single-shot). Token-level streaming
through LangGraph is what this gate proves or refutes. If no ``stream_mode``
combination reconstructs the 7 shapes (esp. token-level ``text_delta`` + the
``tool_call``/``tool_result`` pairing), the correct answer is BLOCKED.

The 7 frozen shapes (reconstruction targets):
  1. agent_start   {"event","agent","model"}
  2. thinking      {"event","agent","text","is_thinking"}
  3. text_delta    {"event","agent","text"}
  4. tool_call     {"event","agent","tool","input"}
  5. tool_result   {"event","agent","tool","result","blocked","latency_ms"}
  6. agent_done    {"event","agent","text",<telemetry>,"tools_called","latency_ms","cost_usd"}
  7. error         {"event","agent","code","message","correlation_id"}  (SANITIZED)
"""

from __future__ import annotations

import asyncio
import json
import os
import sys
import time
from collections.abc import AsyncIterator
from typing import Any

# Standalone script: put backend/ (two dirs up) on sys.path so `src.*` imports resolve
# regardless of cwd (python puts the SCRIPT dir on sys.path[0], not the cwd).
sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.dirname(os.path.abspath(__file__)))))

from deepagents import create_deep_agent
from langchain_core.callbacks import AsyncCallbackManagerForLLMRun
from langchain_core.language_models.chat_models import BaseChatModel
from langchain_core.messages import (
    AIMessage,
    AIMessageChunk,
    BaseMessage,
    SystemMessage,
    ToolMessage,
)
from langchain_core.messages.tool import tool_call_chunk
from langchain_core.outputs import ChatGeneration, ChatGenerationChunk, ChatResult
from langchain_core.tools import tool
from langgraph.checkpoint.memory import MemorySaver
from langgraph.config import get_stream_writer

# Reuse the REAL capability-scope guard for the blocked-denial test.
from src.deep_runtime.middleware.capability_scope import make_capability_scope_middleware
from src.orchestrator.agents import SubAgent

# Reuse the REAL cost helper (do NOT re-derive a pricing formula).
from src.orchestrator.budget import BudgetTracker

SYSTEM_MARKER = "<<MULDRO-SOUL-CORE-CACHE-PREFIX>>"
MODEL_ID = "claude-sonnet-4-6"
AGENT_NAME = "presenter"

# module-level side-effect counter proving the tool body ran
ECHO_CALLS: list[str] = []


@tool
def echo(text: str) -> str:
    """Echo the input text back (trivial tool so the agent takes a tool turn)."""
    ECHO_CALLS.append(text)
    return f"echo: {text}"


@tool
def boom(text: str) -> str:
    """A tool that always raises — to see how a GENUINE tool error surfaces."""
    raise RuntimeError("boom: intentional tool failure")


# ---------------------------------------------------------------------------
# Scripted streaming fake chat model
# ---------------------------------------------------------------------------
class ScriptedFakeChatModel(BaseChatModel):
    """Streams scripted ``AIMessageChunk``s mirroring langchain-anthropic's shape.

    Turn is chosen by inspecting the inbound messages: if any ``ToolMessage`` is
    present the tool already ran, so emit the terminal turn; otherwise emit the
    first turn (thinking + text deltas + a tool call).

    Records every SystemMessage it receives in ``seen_system`` so the caching /
    prompt-flattening check can assert the system prefix reaches the model unsplit.
    """

    seen_system: list[str] = []

    def __init__(self, **kwargs: Any) -> None:
        super().__init__(**kwargs)
        # pydantic model — bypass validation for our scratch attribute
        object.__setattr__(self, "seen_system", [])

    @property
    def _llm_type(self) -> str:
        return "scripted-fake"

    def bind_tools(self, tools, **kwargs):  # noqa: ANN001, ANN003, ARG002
        # deepagents binds tools onto the model; we ignore them (calls are scripted).
        return self

    # --- scripted turns -----------------------------------------------------
    @staticmethod
    def _turn1() -> list[AIMessageChunk]:
        return [
            # extended-thinking deltas → langchain-anthropic emits type="thinking"
            AIMessageChunk(content=[{"type": "thinking", "thinking": "I should ", "index": 0}]),
            AIMessageChunk(content=[{"type": "thinking", "thinking": "echo this.", "index": 0}]),
            # visible text deltas → type="text"
            AIMessageChunk(content=[{"type": "text", "text": "Let me ", "index": 1}]),
            AIMessageChunk(content=[{"type": "text", "text": "echo that.", "index": 1}]),
            # tool call (args complete in one chunk so they parse cleanly)
            AIMessageChunk(
                content=[],
                tool_call_chunks=[
                    tool_call_chunk(
                        name="echo",
                        args=json.dumps({"text": "hello"}),
                        id="call_1",
                        index=2,
                    )
                ],
            ),
            # terminal chunk carries usage + stop reason
            AIMessageChunk(
                content=[],
                usage_metadata={
                    "input_tokens": 120,
                    "output_tokens": 25,
                    "total_tokens": 145,
                    "input_token_details": {"cache_read": 100, "cache_creation": 0},
                },
                response_metadata={"model_name": MODEL_ID, "stop_reason": "tool_use"},
            ),
        ]

    @staticmethod
    def _turn2() -> list[AIMessageChunk]:
        return [
            AIMessageChunk(content=[{"type": "text", "text": "Done — echoed ", "index": 0}]),
            AIMessageChunk(content=[{"type": "text", "text": "'hello'.", "index": 0}]),
            AIMessageChunk(
                content=[],
                usage_metadata={
                    "input_tokens": 60,
                    "output_tokens": 12,
                    "total_tokens": 72,
                    "input_token_details": {"cache_read": 140, "cache_creation": 0},
                },
                response_metadata={"model_name": MODEL_ID, "stop_reason": "end_turn"},
            ),
        ]

    def _script_for(self, messages: list[BaseMessage]) -> list[AIMessageChunk]:
        for m in messages:
            if isinstance(m, SystemMessage):
                content = m.content if isinstance(m.content, str) else json.dumps(m.content)
                self.seen_system.append(content)
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
                token = _token_text(msg_chunk)
                await run_manager.on_llm_new_token(token, chunk=gen)
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

    def _generate(self, *args: Any, **kwargs: Any) -> ChatResult:  # noqa: D401
        raise NotImplementedError("sync generate not used in this async spike")


def _token_text(chunk: AIMessageChunk) -> str:
    if isinstance(chunk.content, str):
        return chunk.content
    parts: list[str] = []
    for block in chunk.content:
        if isinstance(block, dict):
            if block.get("type") == "text":
                parts.append(block.get("text", ""))
            elif block.get("type") == "thinking":
                parts.append(block.get("thinking", ""))
    return "".join(parts)


# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------
def _fresh_model() -> ScriptedFakeChatModel:
    return ScriptedFakeChatModel()


def _user_input() -> dict:
    return {"messages": [{"role": "user", "content": "echo hello"}]}


def _short(v: Any, n: int = 220) -> str:
    s = repr(v)
    return s if len(s) <= n else s[:n] + "…"


# ===========================================================================
# SECTION 1 — dump raw events for each stream_mode
# ===========================================================================
async def section1_raw_streams() -> None:
    print("\n" + "=" * 78)
    print("SECTION 1 — raw events per stream_mode (happy path: 1 tool turn)")
    print("=" * 78)

    for mode in ("messages", "updates", ["messages", "updates"]):
        ECHO_CALLS.clear()
        agent = create_deep_agent(
            model=_fresh_model(),
            tools=[echo],
            checkpointer=MemorySaver(),
            system_prompt=f"{SYSTEM_MARKER} You are a test agent.",
        )
        cfg = {"configurable": {"thread_id": f"raw-{mode}"}}
        print(f"\n--- stream_mode={mode!r} ---")
        n = 0
        async for ev in agent.astream(_user_input(), config=cfg, stream_mode=mode):
            n += 1
            if mode == ["messages", "updates"]:
                sm, payload = ev
                print(f"  [{n:02d}] ({sm}) {type(payload).__name__}: {_short(payload)}")
            else:
                print(f"  [{n:02d}] {type(ev).__name__}: {_short(ev)}")
        print(f"  echo ran? {len(ECHO_CALLS)} call(s): {ECHO_CALLS}")


# ===========================================================================
# SECTION 2 — reconstruct the 7 shapes from combined ["messages","updates"]
# ===========================================================================
async def section2_reconstruct() -> dict[str, str]:
    print("\n" + "=" * 78)
    print("SECTION 2 — reconstruct the 7 frozen shapes from ['messages','updates']")
    print("=" * 78)

    ECHO_CALLS.clear()
    agent = create_deep_agent(
        model=_fresh_model(),
        tools=[echo],
        checkpointer=MemorySaver(),
        system_prompt=f"{SYSTEM_MARKER} You are a test agent.",
    )
    cfg = {"configurable": {"thread_id": "recon"}}

    reconstructed: dict[str, dict] = {}
    source: dict[str, str] = {}

    # agent_start — synthesized by the adapter (name+model known before streaming)
    reconstructed["agent_start"] = {"event": "agent_start", "agent": AGENT_NAME, "model": MODEL_ID}
    source["agent_start"] = "SYNTHESIZED (pre-stream)"

    text_parts: list[str] = []
    tools_called: list[str] = []
    usage_total = {"input": 0, "output": 0, "cache_create": 0, "cache_read": 0}
    tool_call_started_at: dict[str, float] = {}

    t0 = time.monotonic()
    async for sm, payload in agent.astream(
        _user_input(), config=cfg, stream_mode=["messages", "updates"]
    ):
        if sm == "messages":
            msg, _meta = payload
            if isinstance(msg, AIMessageChunk):
                blocks = msg.content if isinstance(msg.content, list) else []
                for block in blocks:
                    if not isinstance(block, dict):
                        continue
                    if block.get("type") == "thinking":
                        reconstructed["thinking"] = {
                            "event": "thinking",
                            "agent": AGENT_NAME,
                            "text": block.get("thinking", ""),
                            "is_thinking": True,
                        }
                        source["thinking"] = "messages (AIMessageChunk block type=='thinking')"
                    elif block.get("type") == "text":
                        t = block.get("text", "")
                        text_parts.append(t)
                        reconstructed["text_delta"] = {
                            "event": "text_delta",
                            "agent": AGENT_NAME,
                            "text": t,
                        }
                        source["text_delta"] = "messages (AIMessageChunk block type=='text')"
                if msg.usage_metadata:
                    um = msg.usage_metadata
                    usage_total["input"] += um.get("input_tokens", 0)
                    usage_total["output"] += um.get("output_tokens", 0)
                    det = um.get("input_token_details", {}) or {}
                    usage_total["cache_create"] += det.get("cache_creation", 0)
                    usage_total["cache_read"] += det.get("cache_read", 0)
            elif isinstance(msg, ToolMessage):
                blocked = getattr(msg, "status", None) == "error"
                started = tool_call_started_at.get(msg.tool_call_id, t0)
                reconstructed["tool_result"] = {
                    "event": "tool_result",
                    "agent": AGENT_NAME,
                    "tool": msg.name,
                    "result": msg.content,
                    "blocked": blocked,
                    "latency_ms": int((time.monotonic() - started) * 1000),
                }
                source["tool_result"] = "messages (ToolMessage; blocked←status=='error')"
        elif sm == "updates":
            for _node, upd in (payload or {}).items():
                if not isinstance(upd, dict):
                    continue
                for m in upd.get("messages", []) or []:
                    if isinstance(m, AIMessage) and m.tool_calls:
                        for tc in m.tool_calls:
                            tool_call_started_at[tc["id"]] = time.monotonic()
                            tools_called.append(tc["name"])
                            reconstructed["tool_call"] = {
                                "event": "tool_call",
                                "agent": AGENT_NAME,
                                "tool": tc["name"],
                                "input": tc["args"],
                            }
                            source["tool_call"] = "updates (full AIMessage.tool_calls)"
    latency_ms = int((time.monotonic() - t0) * 1000)

    # agent_done — synthesized at stream end from accumulated telemetry
    cost = BudgetTracker().calculate_cost(
        MODEL_ID,
        usage_total["input"],
        usage_total["output"],
        cache_creation_input_tokens=usage_total["cache_create"],
        cache_read_input_tokens=usage_total["cache_read"],
    )
    reconstructed["agent_done"] = {
        "event": "agent_done",
        "agent": AGENT_NAME,
        "text": "".join(text_parts),
        "input_tokens": usage_total["input"],
        "output_tokens": usage_total["output"],
        "cache_creation_tokens": usage_total["cache_create"],
        "cache_read_tokens": usage_total["cache_read"],
        "tools_called": tools_called or None,
        "latency_ms": latency_ms,
        "cost_usd": round(cost, 6),
    }
    source["agent_done"] = "SYNTHESIZED (summed usage_metadata + BudgetTracker + monotonic)"

    # error — never a stream event; adapter wraps astream in try/except and emits sanitized frame
    source["error"] = "SYNTHESIZED (see Section 4: astream exception → sanitized frame)"

    order = [
        "agent_start",
        "thinking",
        "text_delta",
        "tool_call",
        "tool_result",
        "agent_done",
        "error",
    ]
    for name in order:
        ok = name in reconstructed or name == "error"
        mark = "OK" if ok else "MISSING"
        print(f"\n[{mark}] {name}  ← {source.get(name, '???')}")
        if name in reconstructed:
            print(f"       {reconstructed[name]}")
    print(f"\n  echo ran? {len(ECHO_CALLS)} call(s): {ECHO_CALLS}")
    return source


# ===========================================================================
# SECTION 3 — is a denied / errored ToolMessage distinguishable in the stream?
# ===========================================================================
async def section3_blocked() -> None:
    print("\n" + "=" * 78)
    print("SECTION 3 — blocked vs errored ToolMessage in the stream")
    print("=" * 78)

    # 3a: the REAL capability-scope guard. With a non-empty scope and db_factory=None,
    #     `_is_in_scope` short-circuits to False → the guard DENIES echo with its real
    #     ToolMessage(status="error") shape (no DB needed).
    probe_agent = SubAgent(
        name="probe_agent",
        prompt="test",
        model_tier="sonnet",
        capability_scope={"knowledge.search"},  # non-empty, but does NOT cover echo
    )
    guard = make_capability_scope_middleware(agent=probe_agent, workspace_id="", db_factory=None)
    ECHO_CALLS.clear()
    agent = create_deep_agent(
        model=_fresh_model(),
        tools=[echo],
        middleware=[guard],
        checkpointer=MemorySaver(),
        system_prompt="You are a test agent.",
    )
    cfg = {"configurable": {"thread_id": "blocked-real"}}
    print("\n-- 3a: REAL capability_scope guard denial (echo out of scope) --")
    async for msg, _meta in agent.astream(_user_input(), config=cfg, stream_mode="messages"):
        if isinstance(msg, ToolMessage):
            print(f"  ToolMessage name={msg.name!r} status={getattr(msg, 'status', None)!r}")
            print(f"    tool_call_id={msg.tool_call_id!r}")
            print(f"    content={_short(msg.content, 260)}")
    print(f"  echo body ran? (expect 0 — denied before execution): {len(ECHO_CALLS)}")
    print(
        "  CAVEAT: the denial ToolMessage has name=None — the adapter must recover\n"
        "  the tool name from tool_call_id → the preceding tool_call to fill\n"
        "  tool_result.tool."
    )

    # 3b: a GENUINE tool error (tool raises). Under deepagents' default ToolNode
    #     config the exception PROPAGATES out of astream (it is NOT turned into a
    #     ToolMessage(status='error')). Isolated so it can't abort 3c.
    ECHO_CALLS.clear()
    agent2 = create_deep_agent(
        model=_boom_model(),
        tools=[boom],
        checkpointer=MemorySaver(),
        system_prompt="You are a test agent.",
    )
    cfg2 = {"configurable": {"thread_id": "blocked-boom"}}
    print("\n-- 3b: GENUINE tool error (boom raises) --")
    saw_tool_msg = False
    try:
        async for msg, _meta in agent2.astream(_user_input(), config=cfg2, stream_mode="messages"):
            if isinstance(msg, ToolMessage):
                saw_tool_msg = True
                print(f"  ToolMessage name={msg.name!r} status={getattr(msg, 'status', None)!r}")
                print(f"    content={_short(msg.content, 260)}")
    except Exception as exc:  # noqa: BLE001
        print(f"  astream PROPAGATED: {type(exc).__name__}: {exc}")
    print(f"  produced a ToolMessage? {saw_tool_msg}")

    print(
        "\n  FINDING: a scope-denial surfaces as an explicit ToolMessage(status='error')\n"
        "  in the stream; a raising tool PROPAGATES as an astream exception (→ error\n"
        "  frame) under deepagents' default ToolNode. So status=='error' on a\n"
        "  ToolMessage IS a usable blocked signal for the CURRENT guard. But the\n"
        "  denial message carries no distinguishing marker beyond status + content\n"
        "  text; for an unambiguous, tool-name-bearing blocked=True the guard should\n"
        "  emit an explicit custom event → Section 3c."
    )

    # 3c: custom-writer proof — a guard that emits an explicit blocked event.
    print("\n-- 3c: custom stream writer emits an unambiguous blocked signal --")
    from langchain.agents.middleware import wrap_tool_call

    @wrap_tool_call
    async def blocking_guard(request, handler):
        writer = get_stream_writer()
        if writer is not None:
            writer(
                {
                    "muldro_event": "tool_blocked",
                    "tool": request.tool_call["name"],
                    "blocked": True,
                }
            )
        return ToolMessage(
            content=json.dumps({"error": "denied by scope"}),
            tool_call_id=request.tool_call["id"],
            status="error",
        )

    ECHO_CALLS.clear()
    agent3 = create_deep_agent(
        model=_fresh_model(),
        tools=[echo],
        middleware=[blocking_guard],
        checkpointer=MemorySaver(),
        system_prompt="You are a test agent.",
    )
    cfg3 = {"configurable": {"thread_id": "blocked-custom"}}
    saw_custom = False
    async for ev in agent3.astream(_user_input(), config=cfg3, stream_mode="custom"):
        print(f"  custom event: {ev}")
        if isinstance(ev, dict) and ev.get("muldro_event") == "tool_blocked":
            saw_custom = True
    print(f"  stream_mode='custom' surfaced the blocked marker? {saw_custom}")


def _boom_model() -> ScriptedFakeChatModel:
    class BoomModel(ScriptedFakeChatModel):
        @staticmethod
        def _turn1() -> list[AIMessageChunk]:
            return [
                AIMessageChunk(content=[{"type": "text", "text": "calling boom", "index": 0}]),
                AIMessageChunk(
                    content=[],
                    tool_call_chunks=[
                        tool_call_chunk(
                            name="boom",
                            args=json.dumps({"text": "x"}),
                            id="call_boom",
                            index=1,
                        )
                    ],
                ),
                AIMessageChunk(
                    content=[],
                    usage_metadata={
                        "input_tokens": 10,
                        "output_tokens": 5,
                        "total_tokens": 15,
                        "input_token_details": {"cache_read": 0, "cache_creation": 0},
                    },
                    response_metadata={"model_name": MODEL_ID, "stop_reason": "tool_use"},
                ),
            ]

    return BoomModel()


# ===========================================================================
# SECTION 4 — error sanitation + prompt-flattening / caching structural check
# ===========================================================================
async def section4_error_and_caching() -> None:
    print("\n" + "=" * 78)
    print("SECTION 4 — error sanitation + prompt-flattening (caching) structural check")
    print("=" * 78)

    # 4a: an exception raised mid-stream. The adapter must catch it and emit a
    #     sanitized frame (never the raw detail), exactly like agent_invoker.
    class ExplodingModel(ScriptedFakeChatModel):
        async def _astream(self, messages, stop=None, run_manager=None, **kwargs):
            raise RuntimeError("SENSITIVE upstream detail that must NEVER reach the client")
            yield  # pragma: no cover

    agent = create_deep_agent(
        model=ExplodingModel(),
        tools=[echo],
        checkpointer=MemorySaver(),
        system_prompt="You are a test agent.",
    )
    cfg = {"configurable": {"thread_id": "err"}}
    print("\n-- 4a: astream raises → adapter emits sanitized error frame --")
    emitted = None
    try:
        async for _ev in agent.astream(_user_input(), config=cfg, stream_mode="messages"):
            pass
    except Exception as exc:  # noqa: BLE001
        # This is exactly what the adapter's outer try/except would do.
        emitted = {
            "event": "error",
            "agent": AGENT_NAME,
            "code": "internal_error",
            "message": "An internal error occurred.",
            "correlation_id": "corr_probe_0001",
        }
        print(f"  raw exception (logged, NEVER emitted): {type(exc).__name__}: {exc}")
    print(f"  sanitized frame emitted to client: {emitted}")

    # 4b: does the flattened system_prompt reach the model UNSPLIT? (structural)
    fake = _fresh_model()
    agent2 = create_deep_agent(
        model=fake,
        tools=[echo],
        checkpointer=MemorySaver(),
        system_prompt=f"{SYSTEM_MARKER} soul+role prefix, one contiguous block.",
    )
    cfg2 = {"configurable": {"thread_id": "cache"}}
    ECHO_CALLS.clear()
    async for _ev in agent2.astream(_user_input(), config=cfg2, stream_mode="messages"):
        pass
    print("\n-- 4b: flattened system_prompt reaches model unsplit? --")
    print(f"  system messages seen by model (per call): {len(fake.seen_system)}")
    contiguous = any(SYSTEM_MARKER in s for s in fake.seen_system)
    split = any((SYSTEM_MARKER not in s) and (SYSTEM_MARKER[:10] in s) for s in fake.seen_system)
    for i, s in enumerate(fake.seen_system):
        print(f"    call[{i}] system head: {_short(s, 180)}")
    print(f"  marker present as ONE contiguous block? {contiguous}  (split-detected={split})")
    print(
        "  NOTE: with a fake model this is STRUCTURAL only. Real cache-hit proof\n"
        "  (cache_read_input_tokens>0 on turn 2) belongs to the LIVE smoke task."
    )


async def main() -> None:
    for section in (
        section1_raw_streams,
        section2_reconstruct,
        section3_blocked,
        section4_error_and_caching,
    ):
        try:
            await section()
        except Exception as exc:  # noqa: BLE001
            import traceback

            print(f"\n!! {section.__name__} raised: {type(exc).__name__}: {exc}")
            traceback.print_exc()

    print("\n" + "=" * 78)
    print("DONE — see the DECISION line in the spike doc.")
    print("=" * 78)


if __name__ == "__main__":
    asyncio.run(main())
