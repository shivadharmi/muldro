"""SPIKE probe (Step 7B2, Phase 0.1/0.2/0.3 — DECISION GATE): prove, fully OFFLINE,
that a read-only Muldro delegate registered via ``create_deep_agent(subagents=[...])``:

  0.1  runs GATED when invoked through the built-in ``task`` tool — its OWN
       ``capability_scope`` guard denies an out-of-scope read and its OWN
       ``muldro_tool_dispatcher`` executes an in-scope read (never the tripwire
       shell). Tested BOTH build methods (A = ``CompiledSubAgent{runnable}`` from
       ``build_deep_agent``; B = raw ``SubAgent`` dict with ``middleware=[...]``).

  0.2  streams predictably through the FROZEN ``stream_deep_agent_events`` adapter:
       does the child's reply text leak into parent-attributed ``text_delta`` frames
       (double-emission), or does only the ``task`` ``tool_result`` frame carry the
       child's summary? Captures the raw ``(msg, metadata)`` tuples so we can decide
       whether a metadata-based mitigation is available if leakage occurs.

  0.3  can have the ambient general-purpose (GP) ``task`` child DISABLED via a
       process-global ``HarnessProfile(general_purpose_subagent=...enabled=False)``
       keyed by the lead model, while ``task`` still routes to our delegate. Negative
       control: without the disable, GP is present. Key-scoping: a different-model
       lead is unaffected.

THROWAWAY investigation probe. Runs with NO Anthropic API key and NO Postgres:
scripted ``BaseChatModel`` fakes drive the lead + child; ``capability_scope``'s
``ToolRegistry`` lookup is stubbed with a name→capability map; ``build_deep_agent``'s
DB-backed write-capability precheck is short-circuited (the Perceiver config used here
is read-only — verified independently by its zero-write scope).

Run (from backend/):
    uv run python spikes/deep_delegate/subagent_gated_probe.py

Exit 0 = every assertion held. Non-zero = a plan assumption was DISPROVEN → STOP and
revise the plan before Phase 1.
"""

from __future__ import annotations

import asyncio
import json
import os
import sys
from collections.abc import AsyncIterator
from typing import Any

# Standalone script: put backend/ (two dirs up) on sys.path so `src.*` imports resolve.
sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.dirname(os.path.abspath(__file__)))))

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
from langgraph.checkpoint.memory import MemorySaver

# The seams under test (all REAL Muldro deep-runtime code).
import src.deep_runtime.agent_builder as agent_builder
import src.deep_runtime.middleware.capability_scope as capability_scope_mod
from src.deep_runtime.middleware.muldro_tool_dispatcher import make_muldro_tool_dispatcher
from src.deep_runtime.stream_adapter import stream_deep_agent_events
from src.deep_runtime.tool_bridge import build_tool_shells
from src.orchestrator.agents import SubAgent as MuldroSubAgent
from src.orchestrator.agents import create_sub_agents

WS = "ws_spike_7b2"
USER = "user_spike_7b2"
LEAD_MODEL_ID = "claude-sonnet-4-6"
OTHER_MODEL_ID = "claude-opus-4-8"

# ---------------------------------------------------------------------------
# Offline capability resolution: stub ToolRegistry with a name→capability map.
# internal_search → internal.search (IN Perceiver scope); email_send → email.send
# (a write cap, NOT in Perceiver scope).
# ---------------------------------------------------------------------------
_NAME_TO_CAP: dict[str, str | None] = {
    "internal_search": "internal.search",
    "email_send": "email.send",
}


class _FakeToolDef:
    def __init__(self, capability: str | None) -> None:
        self.capability = capability


class _FakeRegistry:
    def __init__(self, db: Any, workspace_id: str | None = None) -> None:  # noqa: ARG002
        pass

    async def get_tool(self, name: str):
        cap = _NAME_TO_CAP.get(name)
        return _FakeToolDef(cap) if cap is not None else None


class _FakeDB:
    async def __aenter__(self) -> _FakeDB:
        return self

    async def __aexit__(self, *_a: Any) -> bool:
        return False


def _fake_db_factory() -> _FakeDB:
    return _FakeDB()


# Patch the REAL capability_scope guard's registry lookup to resolve offline via the
# name→capability map above. The guard code path (_is_in_scope: fail-closed, builtin
# exemption, scope membership) is exercised unchanged — only the DB read is stubbed.
capability_scope_mod.ToolRegistry = _FakeRegistry  # type: ignore[assignment]


# ---------------------------------------------------------------------------
# Scripted streaming fake chat model — turn chosen by presence of a ToolMessage.
# ---------------------------------------------------------------------------
class ScriptedModel(BaseChatModel):
    """Streams a scripted list of turns; turn N chosen by tool-round count.

    ``turns`` is a list of turns; each turn is a list of ``AIMessageChunk``. The turn
    index = number of ToolMessages already present in the inbound messages (so turn 0
    is the first call, turn 1 after one tool round, …). Falls back to the last turn.

    Carries ``model_name`` so deepagents' harness-profile resolution derives the same
    ``provider:identifier`` key a real ``ChatAnthropic`` would (0.3).
    """

    model_name: str = LEAD_MODEL_ID
    turns_json: str = "[]"  # pydantic-friendly carrier; real turns live off-model

    _turns: list[list[AIMessageChunk]]

    def __init__(self, turns: list[list[AIMessageChunk]], model_name: str = LEAD_MODEL_ID) -> None:
        super().__init__(model_name=model_name)
        object.__setattr__(self, "_turns", turns)

    @property
    def _llm_type(self) -> str:
        return "scripted-fake"

    def _get_ls_params(self, *args: Any, **kwargs: Any) -> dict[str, Any]:  # noqa: ARG002
        # Force the provider so the GP-disable key resolves to "anthropic:<model_name>"
        # exactly as a real ChatAnthropic would.
        return {"ls_provider": "anthropic", "ls_model_name": self.model_name}

    def bind_tools(self, tools: Any, **kwargs: Any):  # noqa: ANN003, ARG002
        return self

    def _select_turn(self, messages: list[BaseMessage]) -> list[AIMessageChunk]:
        tool_rounds = sum(1 for m in messages if isinstance(m, ToolMessage))
        idx = min(tool_rounds, len(self._turns) - 1)
        return self._turns[idx]

    async def _astream(
        self,
        messages: list[BaseMessage],
        stop: list[str] | None = None,  # noqa: ARG002
        run_manager: AsyncCallbackManagerForLLMRun | None = None,
        **kwargs: Any,  # noqa: ARG002
    ) -> AsyncIterator[ChatGenerationChunk]:
        for chunk in self._select_turn(messages):
            gen = ChatGenerationChunk(message=chunk)
            if run_manager is not None:
                await run_manager.on_llm_new_token(_chunk_text(chunk), chunk=gen)
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
        raise NotImplementedError("sync generate not used in this async spike")


def _chunk_text(chunk: AIMessageChunk) -> str:
    if isinstance(chunk.content, str):
        return chunk.content
    parts: list[str] = []
    for block in chunk.content:
        if isinstance(block, dict):
            parts.append(block.get("text") or block.get("thinking") or "")
    return "".join(parts)


def _usage_chunk(stop_reason: str) -> AIMessageChunk:
    return AIMessageChunk(
        content=[],
        usage_metadata={
            "input_tokens": 50,
            "output_tokens": 10,
            "total_tokens": 60,
            "input_token_details": {"cache_read": 0, "cache_creation": 0},
        },
        response_metadata={"model_name": LEAD_MODEL_ID, "stop_reason": stop_reason},
    )


# --- lead: one turn calling task(researcher), then a terminal reply -----------
def _lead_turns(child_description: str = "look up X") -> list[list[AIMessageChunk]]:
    return [
        [
            AIMessageChunk(content=[{"type": "text", "text": "Delegating.", "index": 0}]),
            AIMessageChunk(
                content=[],
                tool_call_chunks=[
                    tool_call_chunk(
                        name="task",
                        args=json.dumps(
                            {"subagent_type": "researcher", "description": child_description}
                        ),
                        id="lead_tc1",
                        index=1,
                    )
                ],
            ),
            _usage_chunk("tool_use"),
        ],
        [
            AIMessageChunk(content=[{"type": "text", "text": "Lead final answer.", "index": 0}]),
            _usage_chunk("end_turn"),
        ],
    ]


# lead that lists allowed subagent types via a bogus task type (0.3 introspection)
def _lead_list_turns() -> list[list[AIMessageChunk]]:
    return [
        [
            AIMessageChunk(
                content=[],
                tool_call_chunks=[
                    tool_call_chunk(
                        name="task",
                        args=json.dumps({"subagent_type": "__list__", "description": "x"}),
                        id="lead_list_tc",
                        index=0,
                    )
                ],
            ),
            _usage_chunk("tool_use"),
        ],
        [
            AIMessageChunk(content=[{"type": "text", "text": "done", "index": 0}]),
            _usage_chunk("end_turn"),
        ],
    ]


# --- child: turn 1 calls an in-scope read AND an out-of-scope write; then answers.
CHILD_ANSWER = "CHILD-REPLY: found 42 results."


def _child_turns() -> list[list[AIMessageChunk]]:
    return [
        [
            AIMessageChunk(
                content=[],
                tool_call_chunks=[
                    tool_call_chunk(
                        name="internal_search",
                        args=json.dumps({"query": "X"}),
                        id="child_read",
                        index=0,
                    ),
                    tool_call_chunk(
                        name="email_send",
                        args=json.dumps({"to": "a@b.com", "body": "hi"}),
                        id="child_write",
                        index=1,
                    ),
                ],
            ),
            _usage_chunk("tool_use"),
        ],
        [
            AIMessageChunk(content=[{"type": "text", "text": CHILD_ANSWER, "index": 0}]),
            _usage_chunk("end_turn"),
        ],
    ]


# ---------------------------------------------------------------------------
# Build the child delegate both ways.
# ---------------------------------------------------------------------------
_CHILD_SHELLS = build_tool_shells(
    [
        {"name": "internal_search", "description": "search knowledge"},
        {"name": "email_send", "description": "send email"},
    ]
)


def _perceiver_cfg() -> MuldroSubAgent:
    return create_sub_agents()["perceiver"]


def _child_dispatcher(recorder: list[tuple[str, dict]]):
    async def _execute(name: str, args: dict, user_id: str, workspace_id: str) -> dict:  # noqa: ARG001
        recorder.append((name, args))
        return {"results": ["r1", "r2"], "count": 42}

    return make_muldro_tool_dispatcher(execute_tool=_execute, user_id=USER, workspace_id=WS)


async def _build_child_method_a(recorder: list, *, with_scope: bool = True):
    """Method A: CompiledSubAgent{runnable=build_deep_agent(...)} — gate baked in."""
    cfg = _perceiver_cfg()
    if not with_scope:
        cfg = MuldroSubAgent(
            name=cfg.name, prompt=cfg.prompt, model_tier=cfg.model_tier, capability_scope=set()
        )
    # Inject the child fake as the compiled model; short-circuit the DB write-cap precheck
    # (Perceiver is read-only by its scope — verified independently).
    orig_build = agent_builder.build_chat_model
    orig_precheck = agent_builder._has_write_capability_in_scope
    agent_builder.build_chat_model = lambda _a: ScriptedModel(_child_turns())

    async def _no_write(*_a: Any, **_k: Any) -> bool:
        return False

    agent_builder._has_write_capability_in_scope = _no_write
    try:
        compiled = await agent_builder.build_deep_agent(
            cfg,
            _CHILD_SHELLS,
            workspace_id=WS,
            db_factory=_fake_db_factory if with_scope else None,
            extra_middleware=(_child_dispatcher(recorder),),
            system_prompt="You are the research delegate.",
        )
    finally:
        agent_builder.build_chat_model = orig_build
        agent_builder._has_write_capability_in_scope = orig_precheck
    return {
        "name": "researcher",
        "description": "Read-only research delegate.",
        "runnable": compiled,
    }


def _build_child_method_b(recorder: list, *, with_scope: bool = True):
    """Method B: raw SubAgent dict carrying its own middleware=[capability_scope, dispatcher]."""
    cfg = _perceiver_cfg()
    if not with_scope:
        cfg = MuldroSubAgent(
            name=cfg.name, prompt=cfg.prompt, model_tier=cfg.model_tier, capability_scope=set()
        )
    mw = []
    if with_scope:
        mw.append(
            capability_scope_mod.make_capability_scope_middleware(
                agent=cfg, workspace_id=WS, db_factory=_fake_db_factory
            )
        )
    mw.append(_child_dispatcher(recorder))
    return {
        "name": "researcher",
        "description": "Read-only research delegate.",
        "system_prompt": "You are the research delegate.",
        "model": ScriptedModel(_child_turns()),
        "tools": _CHILD_SHELLS,
        "middleware": mw,
    }


# ---------------------------------------------------------------------------
# Drive a lead with a subagent and collect adapter frames + raw messages.
# ---------------------------------------------------------------------------
async def _run_lead(
    child_spec: dict,
    *,
    lead_turns: list[list[AIMessageChunk]] | None = None,
    thread: str = "t",
    subagents: list | None = None,
) -> list[dict]:
    lead = ScriptedModel(lead_turns or _lead_turns())
    agent = create_deep_agent(
        model=lead,
        tools=[],
        subagents=subagents if subagents is not None else [child_spec],
        checkpointer=MemorySaver(),
        system_prompt="You are the lead.",
    )
    cfg = {"configurable": {"thread_id": thread}}
    frames: list[dict] = []
    async for frame in stream_deep_agent_events(
        agent,
        {"messages": [{"role": "user", "content": "please research X"}]},
        cfg,
        agent_name="presenter",
        model=LEAD_MODEL_ID,
    ):
        frames.append(frame)
    return frames


async def _raw_messages(child_spec: dict, *, thread: str) -> list[tuple[Any, dict]]:
    """Capture raw (msg, metadata) tuples so 0.2 can inspect child-vs-lead attribution."""
    lead = ScriptedModel(_lead_turns())
    agent = create_deep_agent(
        model=lead,
        tools=[],
        subagents=[child_spec],
        checkpointer=MemorySaver(),
        system_prompt="You are the lead.",
    )
    cfg = {"configurable": {"thread_id": thread}}
    out: list[tuple[Any, dict]] = []
    async for mode, payload in agent.astream(
        {"messages": [{"role": "user", "content": "please research X"}]},
        config=cfg,
        stream_mode=["messages", "updates"],
    ):
        if mode == "messages":
            msg = payload[0] if isinstance(payload, tuple) else payload
            meta = payload[1] if isinstance(payload, tuple) and len(payload) > 1 else {}
            out.append((msg, meta))
    return out


# ===========================================================================
# 0.1 — the child gate fires (both build methods) + negative control
# ===========================================================================
async def section_0_1() -> bool:
    print("\n" + "=" * 78)
    print("0.1 — read-only child runs GATED through task (both build methods)")
    print("=" * 78)
    ok = True

    methods = (
        ("A CompiledSubAgent", _build_child_method_a),
        ("B SubAgent-dict", _build_child_method_b),
    )
    for label, builder in methods:
        rec: list = []
        spec = await builder(rec) if asyncio.iscoroutinefunction(builder) else builder(rec)
        frames = await _run_lead(spec, thread=f"gate-{label}")
        results = [f for f in frames if f["event"] == "tool_result"]
        # in-scope read dispatched → recorded
        read_dispatched = ("internal_search", {"query": "X"}) in rec
        # out-of-scope write NOT dispatched (denied by the child's own capability_scope)
        write_dispatched = any(n == "email_send" for n, _ in rec)
        task_result = next((f for f in results if f["tool"] == "task"), None)
        got_summary = task_result is not None and CHILD_ANSWER in str(task_result.get("result", ""))

        print(f"\n  [{label}] child dispatch recorder = {rec}")
        print(f"    in-scope read dispatched?  {read_dispatched}  (expect True)")
        print(f"    out-of-scope write ran?    {write_dispatched}  (expect False — denied)")
        print(f"    task tool_result carries child summary? {got_summary}")
        if not read_dispatched:
            print("    !! FAIL: in-scope read did NOT reach the child dispatcher")
            ok = False
        if write_dispatched:
            print("    !! FAIL: out-of-scope write EXECUTED — child gate did not fire")
            ok = False

    # NEGATIVE CONTROL (method B): strip the child capability_scope → the out-of-scope
    # write is NO LONGER denied (proves the CHILD gate, not the parent, does the work).
    print("\n  -- negative control: child WITHOUT capability_scope --")
    rec_ns: list = []
    spec_ns = _build_child_method_b(rec_ns, with_scope=False)
    await _run_lead(spec_ns, thread="gate-neg")
    write_ran = any(n == "email_send" for n, _ in rec_ns)
    print(f"    out-of-scope write ran without scope guard? {write_ran}  (expect True)")
    if not write_ran:
        print("    !! FAIL: negative control did not flip — the gate assertion has no teeth")
        ok = False

    print(f"\n  0.1 verdict: {'PASS' if ok else 'FAIL'}")
    return ok


# ===========================================================================
# 0.2 — child streaming characterization through the frozen adapter
# ===========================================================================
async def section_0_2() -> bool:
    print("\n" + "=" * 78)
    print("0.2 — child streaming: does child reply leak into parent text_delta?")
    print("=" * 78)

    rec: list = []
    spec = _build_child_method_b(rec)
    frames = await _run_lead(spec, thread="stream-char")

    text_deltas = [f for f in frames if f["event"] == "text_delta"]
    delta_texts = [f["text"] for f in text_deltas]
    child_leak = [t for t in delta_texts if CHILD_ANSWER in t or "CHILD-REPLY" in t]
    task_result = next(
        (f for f in frames if f["event"] == "tool_result" and f["tool"] == "task"), None
    )
    done = next((f for f in frames if f["event"] == "agent_done"), None)

    print(f"\n  text_delta frames ({len(text_deltas)}): {delta_texts}")
    print(f"  child text leaked into parent text_delta? {bool(child_leak)}  → {child_leak}")
    print(f"  task tool_result present? {task_result is not None}")
    if task_result:
        carries = CHILD_ANSWER in str(task_result["result"])
        print(f"    task result carries child summary? {carries}")
    if done:
        print(f"  agent_done.text = {done['text']!r}")

    # raw attribution: is child-origin distinguishable by metadata?
    print("\n  -- raw (msg, metadata) attribution for AIMessageChunk deltas --")
    raw = await _raw_messages(spec, thread="stream-raw")
    for msg, meta in raw:
        if isinstance(msg, AIMessageChunk):
            txt = _chunk_text(msg)
            if not txt:
                continue
            keys = {
                k: meta.get(k)
                for k in ("langgraph_node", "ls_agent_type", "checkpoint_ns", "langgraph_step")
                if k in meta
            }
            origin = "CHILD" if ("CHILD-REPLY" in txt or CHILD_ANSWER in txt) else "lead?"
            print(f"    [{origin}] text={txt!r:40} meta={keys}")

    # Decision signal: the frozen contract survives UNCHANGED iff no child text leaked
    # into text_delta AND the task tool_result carries the summary.
    contract_intact = (not child_leak) and task_result is not None
    print(f"\n  0.2 signal: frozen SSE contract survives unchanged? {contract_intact}")
    print("  (If child text leaked, a metadata filter on the (msg,meta) tuple is the")
    print("   mitigation — Phase 4b, byte-neutral-guarded. Recorded in the decision doc.)")
    # 0.2 is CHARACTERIZATION, not pass/fail — it always 'passes' but records the finding.
    return True


# ===========================================================================
# 0.3 — disable the ambient general-purpose task child
# ===========================================================================
def _allowed_subagent_types(frames: list[dict]) -> set[str]:
    """Read the allowed subagent-type list off the task(__list__) error tool_result."""
    tr = next((f for f in frames if f["event"] == "tool_result" and f["tool"] == "task"), None)
    if tr is None:
        return set()
    content = str(tr.get("result", ""))
    # task() returns: "...the only allowed types are `researcher`, `general-purpose`"
    return {tok.strip("`") for tok in content.replace(",", " ").split() if tok.startswith("`")}


async def section_0_3() -> bool:
    print("\n" + "=" * 78)
    print("0.3 — disable the ambient general-purpose task child")
    print("=" * 78)
    ok = True

    import deepagents.profiles.harness.harness_profiles as hp

    def _profile_disabled() -> Any:
        return hp.HarnessProfile(
            general_purpose_subagent=hp.GeneralPurposeSubagentProfile(enabled=False)
        )

    lead_key = f"anthropic:{LEAD_MODEL_ID}"
    other_key = f"anthropic:{OTHER_MODEL_ID}"

    def _clear() -> None:
        hp._HARNESS_PROFILES.pop(lead_key, None)
        hp._HARNESS_PROFILES.pop(other_key, None)

    # NEGATIVE CONTROL: no registration → GP present.
    _clear()
    rec: list = []
    frames_ctrl = await _run_lead(
        _build_child_method_b(rec), lead_turns=_lead_list_turns(), thread="gp-ctrl"
    )
    allowed_ctrl = _allowed_subagent_types(frames_ctrl)
    print(f"\n  [control: no profile] allowed subagent types = {allowed_ctrl}")
    gp_present_ctrl = "general-purpose" in allowed_ctrl
    print(f"    general-purpose present? {gp_present_ctrl}  (expect True)")
    if not gp_present_ctrl:
        print("    !! FAIL: GP not present by default — negative control has no teeth")
        ok = False

    # DISABLE via HarnessProfile keyed to the lead model.
    hp.register_harness_profile(lead_key, _profile_disabled())
    try:
        rec2: list = []
        frames_off = await _run_lead(
            _build_child_method_b(rec2), lead_turns=_lead_list_turns(), thread="gp-off"
        )
        allowed_off = _allowed_subagent_types(frames_off)
        print(f"\n  [disabled: {lead_key}] allowed subagent types = {allowed_off}")
        gp_absent = "general-purpose" not in allowed_off
        researcher_present = "researcher" in allowed_off
        print(f"    general-purpose absent? {gp_absent}  (expect True)")
        print(f"    researcher still routes? {researcher_present}  (expect True)")
        if not gp_absent:
            print("    !! FAIL: GP still present after disable")
            ok = False
        if not researcher_present:
            print("    !! FAIL: our delegate vanished when GP disabled")
            ok = False

        # KEY-SCOPING: a lead of a DIFFERENT model is NOT affected by the lead_key profile.
        rec3: list = []
        lead_other = ScriptedModel(_lead_list_turns(), model_name=OTHER_MODEL_ID)
        agent_other = create_deep_agent(
            model=lead_other,
            tools=[],
            subagents=[_build_child_method_b(rec3)],
            checkpointer=MemorySaver(),
            system_prompt="lead",
        )
        frames_other: list[dict] = []
        async for f in stream_deep_agent_events(
            agent_other,
            {"messages": [{"role": "user", "content": "x"}]},
            {"configurable": {"thread_id": "gp-other"}},
            agent_name="presenter",
            model=OTHER_MODEL_ID,
        ):
            frames_other.append(f)
        allowed_other = _allowed_subagent_types(frames_other)
        gp_present_other = "general-purpose" in allowed_other
        print(f"\n  [other model {other_key}, not disabled] allowed = {allowed_other}")
        print(f"    GP present for the OTHER model? {gp_present_other}  (expect True — key-scoped)")
        if not gp_present_other:
            print("    !! FAIL: disabling lead_key also disabled a different model (unscoped)")
            ok = False
    finally:
        _clear()

    print(f"\n  0.3 verdict: {'PASS' if ok else 'FAIL'}")
    return ok


async def main() -> int:
    print("SPIKE 7B2 Phase 0.1/0.2/0.3 — subagent gating / streaming / GP-disable (OFFLINE)")
    r1 = await section_0_1()
    _r2 = await section_0_2()  # characterization — always returns True, records the finding
    r3 = await section_0_3()
    passed = r1 and _r2 and r3
    print("\n" + "=" * 78)
    print(f"OVERALL: {'ALL ASSERTIONS HELD' if passed else 'A PLAN ASSUMPTION WAS DISPROVEN'}")
    print("=" * 78)
    return 0 if passed else 1


if __name__ == "__main__":
    sys.exit(asyncio.run(main()))
