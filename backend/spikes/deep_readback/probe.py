"""SPIKE probe (Step 7C, Phase 0 — DECISION GATE): prove, fully OFFLINE, that the
7C ``readback`` middleware mechanism works end-to-end on the deep runtime.

7C will add a NEW ``@wrap_tool_call`` middleware ``readback`` placed INNER of
``write_lock`` and OUTER of the central ``muldro_tool_dispatcher`` (so the chain is
``... → write_lock → readback → dispatcher``). For an irreversible write it runs the
tool via ``await handler(request)`` — the dispatcher executes and returns a BARE
``ToolMessage`` — then ANNOTATES a ``verification`` key onto the ToolMessage's
content-JSON and returns a ``model_copy`` of it. It must NOT touch ``status`` (the
SSE adapter maps ``blocked ← status == "error"``).

The specific combination is UNPROVEN offline end-to-end (the annotation-through-SSE
trick is proven for 7B2's ``task`` **Command** result, and inner-of-write_lock
placement is proven by ``write_lock.py``, but NOT this exact path): a read-back
``@wrap_tool_call`` inner of write_lock reading a **bare dispatcher ToolMessage**,
re-annotating a content-JSON key, and that annotation surviving ``write_lock`` + the
full chain + ``stream_deep_agent_events`` to a ``tool_result`` SSE frame with
``blocked == False`` and NO ``stream_adapter`` change.

Two sub-probes drive the REAL ``build_deep_agent`` + ``stream_deep_agent_events``:

  1. UNVERIFIED annotate — verification = {"verdict": "unverified"}. ASSERT: a
     ``tool_result`` frame's ``result`` JSON contains ``verification.verdict ==
     "unverified"`` AND that frame's ``blocked`` is False.

  2. CONTRADICTED escalate-first — verification carries an ``escalation`` block.
     ASSERT: the frame carries ``verification.escalation`` AND is STILL
     ``blocked == False`` (escalate-first surfaces; it does NOT block an
     already-executed write).

THROWAWAY probe. NO Anthropic API key, NO Postgres, NO Redis (write_lock runs with
``redis=None`` — it falls through, returning ``await handler(request)`` unchanged;
its ``async with`` branch is return-value-transparent, so redis=None faithfully
exercises the return path readback depends on). Reuses the scripted streaming fakes
from ``subagent_gated_probe`` (importing it applies its offline ``ToolRegistry`` stub
as a harmless side effect — capability_scope is NOT installed here).

Run (from backend/):
    uv run python spikes/deep_readback/probe.py

Exit 0 = every assertion held (design CONFIRMED). Non-zero = the 7C mechanism was
DISPROVEN offline → STOP and revise the plan before building 7C.
"""

from __future__ import annotations

import asyncio
import json
import os
import sys

# Standalone script: put backend/ (two dirs up) on sys.path so `src.*` imports resolve.
sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.dirname(os.path.abspath(__file__)))))

from langchain.agents.middleware import wrap_tool_call
from langchain_core.messages import AIMessageChunk, ToolMessage
from langchain_core.messages.tool import tool_call_chunk
from langgraph.checkpoint.memory import MemorySaver

import src.deep_runtime.agent_builder as agent_builder

# Reuse the scripted streaming fake + usage helper from the delegate probe (side effect:
# its offline ToolRegistry stub is applied — harmless, capability_scope isn't installed here).
from spikes.deep_delegate.subagent_gated_probe import (  # noqa: E402
    LEAD_MODEL_ID,
    ScriptedModel,
    _usage_chunk,
)
from src.deep_runtime.middleware.muldro_tool_dispatcher import make_muldro_tool_dispatcher
from src.deep_runtime.middleware.write_lock import make_write_lock_middleware
from src.deep_runtime.stream_adapter import stream_deep_agent_events
from src.deep_runtime.tool_bridge import build_tool_shells
from src.orchestrator.agents import SubAgent

WS = "ws_spike_7c"
USER = "user_spike_7c"
WRITE_TOOL = "mock_write"
DISPATCHER_PAYLOAD = {"message_id": "m1"}  # what execute_tool returns (a bare success dict)


# ---------------------------------------------------------------------------
# Lead: one turn calling the stub Muldro write tool, then a terminal reply.
# ---------------------------------------------------------------------------
def _lead_write_turns() -> list[list[AIMessageChunk]]:
    return [
        [
            AIMessageChunk(content=[{"type": "text", "text": "Writing.", "index": 0}]),
            AIMessageChunk(
                content=[],
                tool_call_chunks=[
                    tool_call_chunk(
                        name=WRITE_TOOL,
                        args=json.dumps({"to": "a@b.com", "body": "hi"}),
                        id="w1",
                        index=1,
                    )
                ],
            ),
            _usage_chunk("tool_use"),
        ],
        [
            AIMessageChunk(content=[{"type": "text", "text": "Done.", "index": 0}]),
            _usage_chunk("end_turn"),
        ],
    ]


# ---------------------------------------------------------------------------
# The read-back-shaped @wrap_tool_call middleware under test (7C-shaped).
# Reads the BARE dispatcher ToolMessage, merges a `verification` key into its
# content-JSON, returns a model_copy — NEVER touching `status`.
# ---------------------------------------------------------------------------
def make_readback_probe(verification: dict):
    @wrap_tool_call
    async def readback(request, handler):
        result = await handler(request)
        if not isinstance(result, ToolMessage) or result.status == "error":
            return result
        obj = json.loads(result.content) if isinstance(result.content, str) else result.content
        if not isinstance(obj, dict):
            obj = {"result": obj}
        obj["verification"] = verification
        return result.model_copy(update={"content": json.dumps(obj, default=str)})

    return readback


# ---------------------------------------------------------------------------
# Central dispatcher: execute_tool returns a bare success dict → the dispatcher
# wraps it as ToolMessage(content=json, status="success").
# ---------------------------------------------------------------------------
def _dispatcher():
    async def _execute(name: str, args: dict, user_id: str, workspace_id: str) -> dict:  # noqa: ARG001
        return dict(DISPATCHER_PAYLOAD)

    return make_muldro_tool_dispatcher(execute_tool=_execute, user_id=USER, workspace_id=WS)


async def _no_cap(_name: str) -> str | None:
    # write_lock with redis=None never calls this (it falls through first); present for signature.
    return None


# ---------------------------------------------------------------------------
# Build the deep lead: chain = write_lock (redis=None → falls through)
#                              → readback (INNER of write_lock, OUTER of dispatcher)
#                              → dispatcher.
# Empty capability_scope + db_factory=None → no capability_scope guard installed and
# no write-cap ValueError, so the tool call reaches the chain unfiltered.
# ---------------------------------------------------------------------------
async def _build_lead(readback):
    agent = SubAgent(
        name="presenter",
        prompt="You are the lead.",
        model_tier="sonnet",
        capability_scope=set(),
    )
    shells = build_tool_shells([{"name": WRITE_TOOL, "description": "send an email"}])
    write_lock = make_write_lock_middleware(workspace_id=WS, redis=None, resolve_capability=_no_cap)

    orig_build = agent_builder.build_chat_model
    agent_builder.build_chat_model = lambda _a: ScriptedModel(_lead_write_turns())
    try:
        compiled = await agent_builder.build_deep_agent(
            agent,
            shells,
            workspace_id=WS,
            db_factory=None,
            extra_middleware=(write_lock, readback, _dispatcher()),
            system_prompt="You are the lead.",
            checkpointer=MemorySaver(),
        )
    finally:
        agent_builder.build_chat_model = orig_build
    return compiled


async def _run(readback, *, thread: str) -> list[dict]:
    agent = await _build_lead(readback)
    frames: list[dict] = []
    async for frame in stream_deep_agent_events(
        agent,
        {"messages": [{"role": "user", "content": "send the email"}]},
        {"configurable": {"thread_id": thread}},
        agent_name="presenter",
        model=LEAD_MODEL_ID,
    ):
        frames.append(frame)
    return frames


def _annotated_tool_result(frames: list[dict]) -> tuple[dict | None, dict | None]:
    """Return the (frame, parsed-content-dict) of the first tool_result carrying a
    `verification` key, or (None, None)."""
    for f in frames:
        if f.get("event") != "tool_result":
            continue
        raw = f.get("result")
        try:
            obj = json.loads(raw) if isinstance(raw, str) else raw
        except (TypeError, ValueError):
            continue
        if isinstance(obj, dict) and "verification" in obj:
            return f, obj
    return None, None


# ===========================================================================
# Sub-probe 1 — UNVERIFIED annotation survives to a non-blocked tool_result.
# ===========================================================================
async def sub_probe_1() -> bool:
    print("\n" + "=" * 78)
    print("Sub-probe 1 — UNVERIFIED annotate survives SSE tool_result (blocked==False)")
    print("=" * 78)
    ok = True

    frames = await _run(
        make_readback_probe({"verdict": "unverified"}), thread="readback-unverified"
    )
    frame, obj = _annotated_tool_result(frames)

    print(f"\n  tool_result carrying a `verification` key present? {frame is not None}")
    if frame is None:
        print("  !! FAIL: annotation was DROPPED — no tool_result frame carries `verification`")
        trs = [f for f in frames if f.get("event") == "tool_result"]
        print(f"  (all tool_result frames: {trs})")
        return False

    print(f"    annotated content = {obj}")
    verdict = (obj.get("verification") or {}).get("verdict")
    print(f"    verification.verdict = {verdict!r}  (expect 'unverified')")
    print(f"    frame.blocked = {frame.get('blocked')!r}  (expect False)")
    print(f"    original dispatcher key survived? message_id={obj.get('message_id')!r}")

    if verdict != "unverified":
        print("    !! FAIL: verification.verdict did not survive as 'unverified'")
        ok = False
    if frame.get("blocked") is not False:
        print("    !! FAIL: annotating a content-JSON key must NOT flip the frame to blocked")
        ok = False
    if obj.get("message_id") != DISPATCHER_PAYLOAD["message_id"]:
        print("    !! FAIL: annotation replaced rather than merged the dispatcher payload")
        ok = False

    print(f"\n  Sub-probe 1 verdict: {'PASS' if ok else 'FAIL'}")
    return ok


# ===========================================================================
# Sub-probe 2 — CONTRADICTED escalate-first surfaces WITHOUT blocking.
# ===========================================================================
async def sub_probe_2() -> bool:
    print("\n" + "=" * 78)
    print("Sub-probe 2 — CONTRADICTED escalate-first surfaces, still blocked==False")
    print("=" * 78)
    ok = True

    verification = {
        "verdict": "contradicted",
        "escalation": {
            "capability": "mock.write",
            "artifact_ref": {"kind": "message", "id": "m1"},
            "observed": "write landed but post-write read disagrees",
        },
    }
    frames = await _run(make_readback_probe(verification), thread="readback-contradicted")
    frame, obj = _annotated_tool_result(frames)

    print(f"\n  tool_result carrying a `verification` key present? {frame is not None}")
    if frame is None:
        print("  !! FAIL: annotation was DROPPED — no tool_result frame carries `verification`")
        return False

    print(f"    annotated content = {obj}")
    v = obj.get("verification") or {}
    verdict = v.get("verdict")
    escalation = v.get("escalation")
    print(f"    verification.verdict = {verdict!r}  (expect 'contradicted')")
    print(f"    verification.escalation present? {escalation is not None}  (expect True)")
    print(f"    frame.blocked = {frame.get('blocked')!r}  (expect False — surface, not block)")

    if verdict != "contradicted":
        print("    !! FAIL: contradicted verdict did not survive")
        ok = False
    if not isinstance(escalation, dict) or "capability" not in escalation:
        print("    !! FAIL: escalation block did not survive intact")
        ok = False
    if frame.get("blocked") is not False:
        print("    !! FAIL: escalate-first must SURFACE, not block an already-executed write")
        ok = False

    print(f"\n  Sub-probe 2 verdict: {'PASS' if ok else 'FAIL'}")
    return ok


async def main() -> int:
    print("SPIKE 7C Phase 0 — deep read-back annotation → SSE tool_result (OFFLINE)")
    r1 = await sub_probe_1()
    r2 = await sub_probe_2()
    passed = r1 and r2
    print("\n" + "=" * 78)
    verdict = (
        "ALL ASSERTIONS HELD — 7C mechanism CONFIRMED"
        if passed
        else "A 7C ASSUMPTION WAS DISPROVEN"
    )
    print(f"OVERALL: {verdict}")
    print("=" * 78)
    return 0 if passed else 1


if __name__ == "__main__":
    sys.exit(asyncio.run(main()))
