"""SPIKE probe (Step 7B2, Phase 0.4 — DECISION GATE): prove, fully OFFLINE, that a
lead-side ``@wrap_tool_call`` middleware — the ONE middleware that does NOT skip the
built-in ``task`` tool — can:

  * run the delegate (``result = await handler(request)`` executes ``task`` → child),
  * read the delegate's returned summary out of the ``Command``
    (``result.update["messages"][0].content``),
  * merge an ``"unreviewed"`` / ``"critique"`` annotation into the content JSON, and
  * return a rebuilt ``Command`` so the annotation survives to the frozen
    ``stream_deep_agent_events`` ``tool_result`` frame.

Negative control: a middleware that SKIPS ``task`` (builtin exemption, like every
current gate) yields NO annotation — proving the critique's non-skip is load-bearing.

THROWAWAY probe. NO Anthropic API key, NO Postgres. Reuses the scripted fakes +
gated child builder from ``subagent_gated_probe`` (importing it applies its offline
``ToolRegistry`` stub as a side effect — intended).

Run (from backend/):
    uv run python spikes/deep_delegate/critique_probe.py
"""

from __future__ import annotations

import asyncio
import json
import os
import sys
from typing import Any

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.dirname(os.path.abspath(__file__)))))

from deepagents import create_deep_agent
from langchain.agents.middleware import wrap_tool_call
from langchain_core.messages import ToolMessage
from langgraph.checkpoint.memory import MemorySaver
from langgraph.types import Command

# Reuse the scripted fakes + gated child from the gating probe (side-effect: ToolRegistry stub).
from spikes.deep_delegate.subagent_gated_probe import (  # noqa: E402
    LEAD_MODEL_ID,
    ScriptedModel,
    _build_child_method_b,
    _lead_turns,
)
from src.deep_runtime.stream_adapter import stream_deep_agent_events

_MERGE_MARK = "unreviewed"


def _annotate_content(content: Any, *, ok: bool, concerns: list[str]) -> str:
    """Merge the critique verdict into a delegate summary's content JSON.

    If the content parses as a JSON object, merge in place; otherwise wrap the raw
    text as ``{"summary": content}`` and annotate. Never raises — annotation is a
    non-blocking overlay for read-only delegates.
    """
    try:
        obj = json.loads(content) if isinstance(content, str) else content
        if not isinstance(obj, dict):
            obj = {"summary": obj}
    except (TypeError, ValueError):
        obj = {"summary": content}
    obj[_MERGE_MARK] = not ok
    obj["critique"] = {"ok": ok, "concerns": concerns}
    return json.dumps(obj)


def make_critique_middleware(*, verdict_ok: bool, concerns: list[str], skip_task: bool):
    """A minimal lead-side critique middleware.

    ``skip_task=True`` reproduces the NEGATIVE CONTROL (behaves like every current
    gate that exempts ``task``): it passes ``task`` through untouched → no annotation.
    """

    @wrap_tool_call
    async def critique(request, handler):
        name = request.tool_call["name"]
        if name != "task" or skip_task:
            return await handler(request)

        result = await handler(request)  # runs the delegate; returns a Command
        # Unwrap the task Command → the summary ToolMessage.
        if isinstance(result, Command):
            messages = (result.update or {}).get("messages") or []
            tm = messages[0] if messages else None
            if isinstance(tm, ToolMessage):
                new_content = _annotate_content(tm.content, ok=verdict_ok, concerns=concerns)
                new_tm = ToolMessage(
                    content=new_content,
                    tool_call_id=tm.tool_call_id,
                    name=getattr(tm, "name", None),
                    status=getattr(tm, "status", None) or "success",
                )
                new_update = {**result.update, "messages": [new_tm]}
                return Command(update=new_update)
        return result

    return critique


async def _run_with_critique(critique_mw, *, thread: str) -> list[dict]:
    rec: list = []
    lead = ScriptedModel(_lead_turns())
    agent = create_deep_agent(
        model=lead,
        tools=[],
        subagents=[_build_child_method_b(rec)],
        middleware=[critique_mw],  # OUTER — wraps the task tool call
        checkpointer=MemorySaver(),
        system_prompt="You are the lead.",
    )
    frames: list[dict] = []
    async for f in stream_deep_agent_events(
        agent,
        {"messages": [{"role": "user", "content": "research X"}]},
        {"configurable": {"thread_id": thread}},
        agent_name="presenter",
        model=LEAD_MODEL_ID,
    ):
        frames.append(f)
    return frames


def _task_result(frames: list[dict]) -> dict | None:
    return next((f for f in frames if f["event"] == "tool_result" and f["tool"] == "task"), None)


async def section_0_4() -> bool:
    print("\n" + "=" * 78)
    print("0.4 — lead-side @wrap_tool_call reads + annotates the task Command")
    print("=" * 78)
    ok = True

    # (a) clean verdict → unreviewed=false, annotation present, delegate summary intact.
    frames_clean = await _run_with_critique(
        make_critique_middleware(verdict_ok=True, concerns=[], skip_task=False),
        thread="crit-clean",
    )
    tr = _task_result(frames_clean)
    print(f"\n  [clean] task tool_result present? {tr is not None}")
    if tr is None:
        print("    !! FAIL: no task tool_result frame")
        return False
    content = json.loads(tr["result"])
    print(f"    annotated content = {content}")
    if content.get(_MERGE_MARK) is not False:
        print("    !! FAIL: clean verdict did not set unreviewed=false")
        ok = False
    if "CHILD-REPLY" not in json.dumps(content):
        print("    !! FAIL: delegate summary was lost during annotation")
        ok = False
    if tr["blocked"] is not False:
        print("    !! FAIL: read-only annotation must NOT block (blocked should be False)")
        ok = False

    # (b) flagged verdict → unreviewed=true + concerns, result STILL returned (fail-open).
    frames_flag = await _run_with_critique(
        make_critique_middleware(verdict_ok=False, concerns=["risk"], skip_task=False),
        thread="crit-flag",
    )
    trf = _task_result(frames_flag)
    contentf = json.loads(trf["result"]) if trf else {}
    print(f"\n  [flagged] annotated content = {contentf}")
    if contentf.get(_MERGE_MARK) is not True:
        print("    !! FAIL: flagged verdict did not set unreviewed=true")
        ok = False
    if trf and trf["blocked"] is not False:
        print("    !! FAIL: a flagged READ delegate must fail-open (not blocked)")
        ok = False

    # NEGATIVE CONTROL: a middleware that SKIPS task → no annotation on the summary.
    frames_skip = await _run_with_critique(
        make_critique_middleware(verdict_ok=True, concerns=[], skip_task=True),
        thread="crit-skip",
    )
    trs = _task_result(frames_skip)
    contents = trs["result"] if trs else ""
    has_annotation = _MERGE_MARK in str(contents)
    print(f"\n  [neg-control: skip task] annotation present? {has_annotation}  (expect False)")
    if has_annotation:
        print("    !! FAIL: annotation appeared even when task was skipped — no teeth")
        ok = False

    print(f"\n  0.4 verdict: {'PASS' if ok else 'FAIL'}")
    return ok


async def main() -> int:
    print("SPIKE 7B2 Phase 0.4 — lead-side task-Command critique annotation (OFFLINE)")
    r = await section_0_4()
    print("\n" + "=" * 78)
    print(f"OVERALL: {'ALL ASSERTIONS HELD' if r else 'A PLAN ASSUMPTION WAS DISPROVEN'}")
    print("=" * 78)
    return 0 if r else 1


if __name__ == "__main__":
    sys.exit(asyncio.run(main()))
