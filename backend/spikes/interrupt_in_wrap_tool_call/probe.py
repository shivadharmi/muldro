"""SPIKE probe: can a LangGraph ``interrupt()`` be raised from inside a
``@wrap_tool_call`` middleware wrapper so the agent PAUSES before a tool runs,
then RESUMES via ``Command(resume=...)`` and runs the tool exactly once?

This is a THROWAWAY investigation probe (Step-0 rebuild, Task 6). It runs fully
OFFLINE — no Anthropic API key, no real LLM. A subclassed fake chat model
deterministically emits exactly one tool call (``echo``) then a final answer.

Run:
    source .venv/bin/activate
    python spikes/interrupt_in_wrap_tool_call/probe.py

Two scenarios are exercised:
  A. gate via ``@wrap_tool_call`` raising ``interrupt(...)`` BEFORE the handler.
  B. fallback: built-in ``HumanInTheLoopMiddleware`` via ``interrupt_on=``.

Each prints: PAUSED? (saw ``__interrupt__``), the interrupt payload, then after
``Command(resume=...)`` whether the tool RAN EXACTLY ONCE (module-level counter).
"""

from __future__ import annotations

import asyncio

from langchain.agents.middleware import wrap_tool_call
from langchain_core.language_models.fake_chat_models import GenericFakeChatModel
from langchain_core.messages import AIMessage
from langchain_core.tools import tool
from langgraph.checkpoint.memory import MemorySaver
from langgraph.types import Command, interrupt

from deepagents import create_deep_agent

# --- module-level side-effect counter: proves the tool body ran exactly once ---
ECHO_CALLS: list[str] = []


@tool
def echo(text: str) -> str:
    """Echo the input text back (trivial side-effecting tool for the probe)."""
    ECHO_CALLS.append(text)
    return f"echo: {text}"


class ToolCallingFake(GenericFakeChatModel):
    """Fake chat model that emits scripted messages and accepts ``bind_tools``.

    ``GenericFakeChatModel`` raises ``NotImplementedError`` on ``bind_tools``;
    deepagents binds tools to the model, so we override it as a no-op returning
    ``self``. The model emits whatever ``messages`` iterator it was given,
    regardless of the bound tools — giving a deterministic tool-call turn.
    """

    def bind_tools(self, tools, **kwargs):  # noqa: ANN001, ANN003, ARG002
        return self


def _make_fake() -> ToolCallingFake:
    """One tool-call turn (echo), then a final answer on resume."""
    return ToolCallingFake(
        messages=iter(
            [
                AIMessage(
                    content="",
                    tool_calls=[
                        {"name": "echo", "args": {"text": "hello"}, "id": "call_1"}
                    ],
                ),
                AIMessage(content="done"),
            ]
        )
    )


def _find_interrupt(result: dict) -> object | None:
    """Return the ``__interrupt__`` payload from an ``ainvoke`` result, or None."""
    if not isinstance(result, dict):
        return None
    return result.get("__interrupt__")


def _last_tool_message(result: dict) -> str | None:
    """Return the content of the last ToolMessage in the result, if any."""
    msgs = result.get("messages", []) if isinstance(result, dict) else []
    for m in reversed(msgs):
        if type(m).__name__ == "ToolMessage":
            return getattr(m, "content", None)
    return None


# ---------------------------------------------------------------------------
# Scenario A: interrupt() from inside @wrap_tool_call
# ---------------------------------------------------------------------------
async def scenario_a() -> dict:
    print("\n" + "=" * 70)
    print("SCENARIO A: interrupt() raised from inside @wrap_tool_call")
    print("=" * 70)
    ECHO_CALLS.clear()

    @wrap_tool_call
    async def approval_gate(request, handler):
        # Raise the interrupt BEFORE running the tool. On the first pass this
        # pauses the graph; on resume, interrupt() returns the resume value.
        decision = interrupt(
            {
                "reason": "approval needed",
                "tool": request.tool_call["name"],
                "args": request.tool_call.get("args"),
            }
        )
        print(f"  [gate] resumed with decision={decision!r}")
        if decision == "reject":
            from langchain_core.messages import ToolMessage

            return ToolMessage(
                content='{"status":"rejected"}',
                tool_call_id=request.tool_call["id"],
                status="error",
            )
        return await handler(request)

    agent = create_deep_agent(
        model=_make_fake(),
        tools=[echo],
        middleware=[approval_gate],
        checkpointer=MemorySaver(),
        system_prompt="You are a test agent.",
    )
    config = {"configurable": {"thread_id": "tA"}}

    result = await agent.ainvoke(
        {"messages": [{"role": "user", "content": "echo hello"}]},
        config=config,
    )
    interrupted = _find_interrupt(result)
    print(f"  PAUSED before tool ran? {interrupted is not None}")
    print(f"  echo calls so far (expect 0): {len(ECHO_CALLS)} {ECHO_CALLS}")
    print(f"  __interrupt__ payload: {interrupted}")

    if interrupted is None:
        print("  RESULT A: did NOT pause — interrupt() in wrap_tool_call had no effect.")
        return {"paused": False}

    # Resume with approval.
    resumed = await agent.ainvoke(Command(resume="approve"), config=config)
    print(f"  echo calls after resume (expect 1): {len(ECHO_CALLS)} {ECHO_CALLS}")
    print(f"  last tool message: {_last_tool_message(resumed)!r}")
    ran_once = len(ECHO_CALLS) == 1
    print(f"  RESULT A: paused=True, resumed=True, tool_ran_exactly_once={ran_once}")
    return {"paused": True, "ran_once": ran_once}


# ---------------------------------------------------------------------------
# Scenario B: fallback — built-in HumanInTheLoopMiddleware via interrupt_on
# ---------------------------------------------------------------------------
async def scenario_b() -> dict:
    print("\n" + "=" * 70)
    print("SCENARIO B: built-in HumanInTheLoopMiddleware via interrupt_on=")
    print("=" * 70)
    ECHO_CALLS.clear()

    agent = create_deep_agent(
        model=_make_fake(),
        tools=[echo],
        interrupt_on={"echo": True},
        checkpointer=MemorySaver(),
        system_prompt="You are a test agent.",
    )
    config = {"configurable": {"thread_id": "tB"}}

    result = await agent.ainvoke(
        {"messages": [{"role": "user", "content": "echo hello"}]},
        config=config,
    )
    interrupted = _find_interrupt(result)
    print(f"  PAUSED before tool ran? {interrupted is not None}")
    print(f"  echo calls so far (expect 0): {len(ECHO_CALLS)} {ECHO_CALLS}")
    print(f"  __interrupt__ payload: {interrupted}")

    if interrupted is None:
        print("  RESULT B: did NOT pause.")
        return {"paused": False}

    # Resume with the HITL decision schema documented for HumanInTheLoopMiddleware.
    resumed = await agent.ainvoke(
        Command(resume={"decisions": [{"type": "approve"}]}),
        config=config,
    )
    print(f"  echo calls after resume (expect 1): {len(ECHO_CALLS)} {ECHO_CALLS}")
    print(f"  last tool message: {_last_tool_message(resumed)!r}")
    ran_once = len(ECHO_CALLS) == 1
    print(f"  RESULT B: paused=True, resumed=True, tool_ran_exactly_once={ran_once}")
    return {"paused": True, "ran_once": ran_once}


async def main() -> None:
    a = None
    b = None
    try:
        a = await scenario_a()
    except Exception as exc:  # noqa: BLE001
        import traceback

        print(f"\n  SCENARIO A raised: {type(exc).__name__}: {exc}")
        traceback.print_exc()
    try:
        b = await scenario_b()
    except Exception as exc:  # noqa: BLE001
        import traceback

        print(f"\n  SCENARIO B raised: {type(exc).__name__}: {exc}")
        traceback.print_exc()

    print("\n" + "=" * 70)
    print("SUMMARY")
    print("=" * 70)
    print(f"  A (wrap_tool_call + interrupt): {a}")
    print(f"  B (HumanInTheLoopMiddleware):   {b}")


if __name__ == "__main__":
    asyncio.run(main())
