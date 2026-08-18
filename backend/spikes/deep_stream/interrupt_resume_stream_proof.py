"""SPIKE (Step 6B research, Task 0): how does a LangGraph ``interrupt()`` raised
from inside a ``@wrap_tool_call`` gate surface under
``agent.astream(stream_mode=["messages","updates"])`` — the exact streaming path
the Muldro deep chat runtime uses — and does ``Command(resume=...)`` re-stream
and run the tool exactly once?

THROWAWAY offline probe. No Anthropic API key, no real LLM. A subclassed fake
streaming chat model deterministically emits one ``echo`` tool call on turn 1,
then a final answer on the resumed turn. A single ``@wrap_tool_call`` gate calls
``interrupt(...)`` BEFORE running the tool.

Run (from backend/):
    uv run python spikes/deep_stream/interrupt_resume_stream_proof.py

Prior art:
- spikes/interrupt_in_wrap_tool_call/probe.py  — proved the gate under ``ainvoke``
  (interrupt surfaces as ``result["__interrupt__"]``, resume via ``Command``).
- spikes/deep_stream/central_dispatcher_proof.py — the streaming ``_ScriptedModel``.

============================================================================
FINDINGS (langgraph 1.2.6 / deepagents 0.6.11 / langchain 1.3.10 / core 1.4.8)
============================================================================
Q1 (does GraphInterrupt RAISE out of the async-for?):
    NO. The ``async for`` over ``agent.astream(..., stream_mode=["messages",
    "updates"])`` completes normally. No ``GraphInterrupt`` is raised.

Q2 (does an ``{"__interrupt__": (...)}`` payload appear in the UPDATES stream?):
    YES. An ``updates``-mode item appears with the exact shape:
        ("updates", {"__interrupt__": (Interrupt(value={...}, id="<hex>"),)})
    i.e. the value under key ``"__interrupt__"`` is a 1-tuple of
    ``langgraph.types.Interrupt`` objects. The gate's dict payload is on
    ``Interrupt.value``.

Q3 (which of raise vs update actually happens?):
    ONLY the ``__interrupt__`` UPDATES item. No raise. Step 6B's stream adapter
    must DETECT THE PAUSE by watching the updates stream for the
    ``"__interrupt__"`` key — NOT by try/except on GraphInterrupt.

Q4 (resume-approve re-streams and runs the tool exactly once + final AI msg?):
    YES. ``agent.astream(Command(resume="approve"), config, stream_mode=[...])``
    on the SAME thread re-streams; the ``echo`` body runs exactly once
    (ECHO_CALLS == 1) and a final AI message ("All done.") is produced.

Q5 (resume-reject on a fresh thread does NOT run the tool + rejection ToolMsg?):
    YES. ``Command(resume="reject")`` re-streams; the ``echo`` body does NOT run
    (ECHO_CALLS unchanged) and a rejection ToolMessage (status="error",
    content '{"rejected":true}') is produced.

Q6 (EXACT payload accessor for Step 6B):
    From an updates item ``("updates", payload)``:
        intr = payload["__interrupt__"][0]     # langgraph.types.Interrupt
        gate_payload = intr.value              # the dict passed to interrupt()
        interrupt_id = intr.id                 # opaque str id
    (If a GraphInterrupt were ever raised instead, ``gi.args[0]`` is the same
    tuple of Interrupt objects — but that path is NOT taken here.)

Q7 (is ``durability="sync"`` accepted as an astream kwarg?):
    YES. ``agent.astream(..., durability="sync")`` is accepted with no error.

DECISION-A REPRODUCED: interrupt pauses the stream (via __interrupt__ update) and
Command(resume) re-runs the tool. See the SUMMARY block printed at the end.
"""

from __future__ import annotations

import asyncio
import json
from collections.abc import AsyncIterator
from typing import Any

from deepagents import create_deep_agent
from langchain.agents.middleware import wrap_tool_call
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
from langgraph.errors import GraphInterrupt
from langgraph.types import Command, interrupt

MODEL_ID = "claude-sonnet-4-6"

# --- module-level side-effect counter: proves the tool body ran exactly once ---
ECHO_CALLS: list[str] = []


@tool
def echo(text: str) -> str:
    """Echo the input text back (trivial side-effecting write tool for the probe)."""
    ECHO_CALLS.append(text)
    return f"echo: {text}"


# --- the ONE approval gate: interrupt() BEFORE the handler -------------------
@wrap_tool_call
async def approval_gate(request, handler):  # noqa: ANN001, ANN201
    verdict = interrupt(
        {
            "approval_id": "apr_test",
            "reason": "needs approval",
            "tool": request.tool_call["name"],
        }
    )
    print(f"  [gate] resumed with verdict={verdict!r}")
    approved = verdict == "approve" or (
        isinstance(verdict, dict) and verdict.get("decision") == "approve"
    )
    if approved:
        return await handler(request)
    return ToolMessage(
        content='{"rejected":true}',
        tool_call_id=request.tool_call["id"],
        status="error",
    )


# --- scripted fake streaming model: turn 1 calls echo, resumed turn answers --
class _ScriptedModel(BaseChatModel):
    @property
    def _llm_type(self) -> str:
        return "scripted-fake"

    def bind_tools(self, tools, **kwargs):  # noqa: ANN001, ANN003, ARG002
        return self

    @staticmethod
    def _turn1() -> list[AIMessageChunk]:
        return [
            AIMessageChunk(
                content=[],
                tool_call_chunks=[
                    tool_call_chunk(
                        name="echo",
                        args=json.dumps({"text": "hello"}),
                        id="call_echo",
                        index=0,
                    ),
                ],
            ),
            AIMessageChunk(
                content=[],
                usage_metadata={
                    "input_tokens": 100,
                    "output_tokens": 10,
                    "total_tokens": 110,
                    "input_token_details": {"cache_read": 0, "cache_creation": 0},
                },
                response_metadata={"model_name": MODEL_ID, "stop_reason": "tool_use"},
            ),
        ]

    @staticmethod
    def _turn2() -> list[AIMessageChunk]:
        return [
            AIMessageChunk(content=[{"type": "text", "text": "All done.", "index": 0}]),
            AIMessageChunk(
                content=[],
                usage_metadata={
                    "input_tokens": 50,
                    "output_tokens": 5,
                    "total_tokens": 55,
                    "input_token_details": {"cache_read": 0, "cache_creation": 0},
                },
                response_metadata={"model_name": MODEL_ID, "stop_reason": "end_turn"},
            ),
        ]

    def _script_for(self, messages: list[BaseMessage]) -> list[AIMessageChunk]:
        took_tool_turn = any(isinstance(m, ToolMessage) for m in messages)
        return self._turn2() if took_tool_turn else self._turn1()

    async def _astream(  # noqa: ANN001, ANN003
        self, messages, stop=None, run_manager=None, **kwargs
    ) -> AsyncIterator[ChatGenerationChunk]:
        for ch in self._script_for(messages):
            yield ChatGenerationChunk(message=ch)

    async def _agenerate(  # noqa: ANN001, ANN003
        self,
        messages,
        stop=None,
        run_manager: AsyncCallbackManagerForLLMRun | None = None,
        **kwargs,
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

    def _generate(self, *a: Any, **k: Any) -> ChatResult:
        raise NotImplementedError


# --- stream driver: collects every (mode, payload); catches a possible raise --
async def drive_stream(
    agent: Any, graph_input: Any, config: dict, *, durability: str | None = None
) -> tuple[list[tuple[str, Any]], GraphInterrupt | None, bool]:
    items: list[tuple[str, Any]] = []
    raised: GraphInterrupt | None = None
    durability_accepted = True
    kwargs: dict[str, Any] = {"stream_mode": ["messages", "updates"]}
    if durability is not None:
        kwargs["durability"] = durability
    try:
        async for mode, payload in agent.astream(graph_input, config=config, **kwargs):
            items.append((mode, payload))
    except GraphInterrupt as gi:  # noqa: BLE001 — the whole point is to see IF this fires
        raised = gi
    except TypeError as te:
        if durability is not None and "durability" in str(te):
            durability_accepted = False
            print(f"  durability kwarg REJECTED: {te}")
            # retry without the kwarg so the rest of the probe can proceed
            kwargs.pop("durability", None)
            async for mode, payload in agent.astream(graph_input, config=config, **kwargs):
                items.append((mode, payload))
        else:
            raise
    return items, raised, durability_accepted


def find_interrupt_updates(items: list[tuple[str, Any]]) -> list[tuple]:
    """Return every ``__interrupt__`` tuple found in ``updates``-mode items."""
    found: list[tuple] = []
    for mode, payload in items:
        if mode == "updates" and isinstance(payload, dict) and "__interrupt__" in payload:
            found.append(payload["__interrupt__"])
    return found


def print_stream(label: str, items: list[tuple[str, Any]]) -> None:
    print(f"  --- {label}: {len(items)} stream item(s) ---")
    for mode, payload in items:
        if mode == "updates":
            # updates payloads are the interesting ones (node outputs + interrupts)
            keys = list(payload.keys()) if isinstance(payload, dict) else payload
            has_intr = isinstance(payload, dict) and "__interrupt__" in payload
            marker = " <-- __interrupt__" if has_intr else ""
            print(f"    ({mode}) keys={keys}{marker}")
            if isinstance(payload, dict) and "__interrupt__" in payload:
                print(f"        raw __interrupt__ = {payload['__interrupt__']!r}")
        else:
            # messages mode: (chunk, metadata)
            chunk = payload[0] if isinstance(payload, tuple) and payload else payload
            cname = type(chunk).__name__
            print(f"    ({mode}) {cname}")


async def final_ai_texts(agent: Any, config: dict) -> list[str]:
    state = await agent.aget_state(config)
    msgs = state.values.get("messages", [])
    return [
        m.text if hasattr(m, "text") else str(m.content)
        for m in msgs
        if isinstance(m, AIMessage) and not m.tool_calls and (m.content)
    ]


async def rejection_tool_messages(agent: Any, config: dict) -> list[ToolMessage]:
    state = await agent.aget_state(config)
    msgs = state.values.get("messages", [])
    return [m for m in msgs if isinstance(m, ToolMessage) and m.status == "error"]


def build_agent() -> Any:
    return create_deep_agent(
        model=_ScriptedModel(),
        tools=[echo],
        middleware=[approval_gate],
        checkpointer=MemorySaver(),
        system_prompt="test",
    )


async def main() -> None:
    results: dict[str, Any] = {}

    # =====================================================================
    # STEP 2: interrupt surfaces during astream(stream_mode=["messages","updates"])
    # =====================================================================
    print("=" * 74)
    print("STEP 2: turn-1 astream -> interrupt() in gate. Where does it surface?")
    print("=" * 74)
    ECHO_CALLS.clear()
    agent = build_agent()
    cfg_approve = {"configurable": {"thread_id": "t-approve"}}

    items, raised, durability_accepted = await drive_stream(
        agent,
        {"messages": [{"role": "user", "content": "echo hello"}]},
        cfg_approve,
        durability="sync",
    )
    print_stream("turn-1 stream", items)

    intr_updates = find_interrupt_updates(items)
    print(f"\n  GraphInterrupt RAISED out of async-for? {raised is not None}")
    if raised is not None:
        print(f"    raised type: {type(raised).__name__}")
        print(f"    raised.args[0]: {raised.args[0]!r}")
    print(f"  __interrupt__ appeared in updates stream? {bool(intr_updates)}")
    print(f"  durability='sync' accepted as astream kwarg? {durability_accepted}")

    accessor_payload = None
    accessor_id = None
    if intr_updates:
        intr_tuple = intr_updates[0]
        intr = intr_tuple[0]
        accessor_payload = intr.value
        accessor_id = getattr(intr, "id", None)
        print(f"  Interrupt object type: {type(intr).__name__}")
        print(f"  ACCESSOR payload['__interrupt__'][0].value = {accessor_payload!r}")
        print(f"  ACCESSOR payload['__interrupt__'][0].id    = {accessor_id!r}")
    elif raised is not None:
        # fallback accessor if a raise DID happen
        accessor_payload = raised.args[0][0].value
        accessor_id = getattr(raised.args[0][0], "id", None)
        print(f"  ACCESSOR gi.args[0][0].value = {accessor_payload!r}")

    print(f"  echo calls so far (expect 0): {len(ECHO_CALLS)} {ECHO_CALLS}")

    state = await agent.aget_state(cfg_approve)
    print(f"  paused? state.next (non-empty means paused): {state.next}")

    results["raised"] = raised is not None
    results["interrupt_in_updates"] = bool(intr_updates)
    results["durability_sync_accepted"] = durability_accepted
    results["paused_before_tool"] = len(ECHO_CALLS) == 0 and bool(state.next)
    results["accessor_payload"] = accessor_payload

    # =====================================================================
    # STEP 3a: resume approve -> re-stream, run tool exactly once
    # =====================================================================
    print("\n" + "=" * 74)
    print("STEP 3a: resume with Command(resume='approve') on SAME thread")
    print("=" * 74)
    items2, raised2, _ = await drive_stream(agent, Command(resume="approve"), cfg_approve)
    print_stream("resume-approve stream", items2)
    print(f"  GraphInterrupt raised on resume? {raised2 is not None}")
    print(f"  echo calls after approve (expect 1): {len(ECHO_CALLS)} {ECHO_CALLS}")
    approve_finals = await final_ai_texts(agent, cfg_approve)
    print(f"  final AI message(s) after approve: {approve_finals}")
    results["approve_echo_count"] = len(ECHO_CALLS)
    results["approve_final_ai"] = approve_finals

    # =====================================================================
    # STEP 3b: fresh thread, resume reject -> tool must NOT run
    # =====================================================================
    print("\n" + "=" * 74)
    print("STEP 3b: fresh thread t-reject -> interrupt -> Command(resume='reject')")
    print("=" * 74)
    echo_before_reject = len(ECHO_CALLS)
    cfg_reject = {"configurable": {"thread_id": "t-reject"}}
    items3, raised3, _ = await drive_stream(
        agent,
        {"messages": [{"role": "user", "content": "echo hello"}]},
        cfg_reject,
    )
    intr_updates3 = find_interrupt_updates(items3)
    print(f"  interrupt surfaced on fresh thread? {bool(intr_updates3)}")
    print(f"  echo calls after fresh interrupt (expect unchanged): {len(ECHO_CALLS)}")

    items4, raised4, _ = await drive_stream(agent, Command(resume="reject"), cfg_reject)
    print_stream("resume-reject stream", items4)
    echo_after_reject = len(ECHO_CALLS)
    reject_tms = await rejection_tool_messages(agent, cfg_reject)
    reject_finals = await final_ai_texts(agent, cfg_reject)
    print(f"  echo calls before/after reject (equal): {echo_before_reject} / {echo_after_reject}")
    print(f"  rejection ToolMessage(s): {[(m.status, m.content) for m in reject_tms]}")
    print(f"  final AI message(s) after reject: {reject_finals}")
    results["reject_ran_tool"] = echo_after_reject != echo_before_reject
    results["reject_tool_messages"] = [(m.status, m.content) for m in reject_tms]

    # =====================================================================
    # STEP 4: SUMMARY
    # =====================================================================
    print("\n" + "=" * 74)
    print("SUMMARY")
    print("=" * 74)
    if results["interrupt_in_updates"] and not results["raised"]:
        q3_path = "UPDATES __interrupt__ (no raise)"
    elif results["raised"]:
        q3_path = "RAISE"
    else:
        q3_path = "NEITHER"
    approve_count = results["approve_echo_count"]
    approve_once = approve_count == 1
    approve_final = bool(results["approve_final_ai"])
    reject_ran = results["reject_ran_tool"]
    reject_tm = bool(results["reject_tool_messages"])
    print(f"  Q1 GraphInterrupt raised out of async-for?  {results['raised']}")
    print(f"  Q2 __interrupt__ in updates stream?         {results['interrupt_in_updates']}")
    print(f"  Q3 which path ->                            {q3_path}")
    print(f"  Q4 resume-approve tool ran exactly once?    {approve_once} (count={approve_count})")
    print(f"     resume-approve final AI produced?        {approve_final}")
    print(f"  Q5 resume-reject tool ran?                  {reject_ran} (expect False)")
    print(f"     resume-reject rejection ToolMessage?     {reject_tm}")
    print(f"  Q6 accessor payload['__interrupt__'][0].value = {results['accessor_payload']!r}")
    print(f"  Q7 durability='sync' accepted?              {results['durability_sync_accepted']}")

    decision_a = (
        results["interrupt_in_updates"]
        and not results["raised"]
        and results["paused_before_tool"]
        and results["approve_echo_count"] == 1
        and bool(results["approve_final_ai"])
        and not results["reject_ran_tool"]
        and bool(results["reject_tool_messages"])
    )
    print("\n  DECISION-A REPRODUCED (stream pause + Command resume runs tool once): "
          f"{'YES ✅' if decision_a else 'NO ❌'}")


if __name__ == "__main__":
    asyncio.run(main())
