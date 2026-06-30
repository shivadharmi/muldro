# Spike: `interrupt()` from inside `@wrap_tool_call` — gate-topology decision

**Date:** 2026-06-28
**Task:** Step-0 rebuild, Task 6
**Question:** Can the rebuild's unified write-approval gate (§4.3) raise a LangGraph
`interrupt()` from inside a `@wrap_tool_call` middleware wrapper, so a write tool
pauses for human approval mid-run, then resumes via `Command(resume=...)`? Or must
it fall back to `HumanInTheLoopMiddleware` / `interrupt_on=`?

## TL;DR — DECISION

**(A) — the `@wrap_tool_call` + `interrupt()` gate WORKS. Use it for the Step-6 unified write-approval gate.**

The probe empirically confirmed all three required properties on the installed
substrate (deepagents 0.6.11, langchain 1.3.10, langchain_core 1.4.8, langgraph
current). The built-in fallback (B) also works, but (A) is preferred because it
lets the gate raise a **custom Jarvis approval payload** (trust level, risk,
capability, artifact refs) and apply Jarvis's own approve/reject verdict logic,
rather than being constrained to the built-in HITL `decisions` schema. The
fallback (B) is documented below as a viable alternative if we later want
deepagents to manage the message-history repair on cancel (via
`PatchToolCallsMiddleware`, which ships in the `interrupt_on` stack).

## Environment

- No Anthropic API key — driven fully OFFLINE.
- Fake model: subclass of `langchain_core.language_models.fake_chat_models.GenericFakeChatModel`
  overriding `bind_tools` as a no-op returning `self` (the stock class raises
  `NotImplementedError` on `bind_tools`, which deepagents calls). The fake emits a
  scripted iterator: turn 1 = one `echo` tool call, turn 2 = a final answer. This
  yields a deterministic, offline tool-call turn.
- Agent built directly via `create_deep_agent(model=fake, tools=[echo], middleware=[gate], checkpointer=MemorySaver())`
  (see "Caveat" — `build_deep_agent` cannot yet take a checkpointer).
- `echo` increments a module-level `ECHO_CALLS` list so we can prove the tool body
  ran **exactly once** (or zero times on reject/at-pause).

Probe: `backend/spikes/interrupt_in_wrap_tool_call/probe.py`

## What was run & OBSERVED results

### Scenario A — `interrupt()` inside `@wrap_tool_call` (the spike question)

The gate calls `decision = interrupt({...})` **before** `await handler(request)`.

```
PAUSED before tool ran? True
echo calls so far (expect 0): 0 []
__interrupt__ payload: [Interrupt(value={'reason': 'approval needed', 'tool': 'echo',
                        'args': {'text': 'hello'}}, id='602e773c...')]
[gate] resumed with decision='approve'
echo calls after resume (expect 1): 1 ['hello']
last tool message: 'echo: hello'
RESULT A: paused=True, resumed=True, tool_ran_exactly_once=True
```

- **PAUSED**: first `ainvoke` returned a result containing `__interrupt__` with our
  custom payload, and `ECHO_CALLS == []` — the graph paused **before** the tool body.
- **RESUMED**: `await agent.ainvoke(Command(resume="approve"), config=<same thread_id>)`
  resumed; `interrupt()` returned `"approve"` inside the gate.
- **RAN EXACTLY ONCE**: `ECHO_CALLS == ["hello"]` after resume — no double-execution.

### Scenario A' — reject path (separate run)

Gate returns a `ToolMessage(status="error")` instead of calling the handler when the
resume value is `"reject"`:

```
paused: True | echo before resume (expect 0): 0
echo after REJECT (expect 0): 0 []
tool messages: ['{"status":"rejected"}']
```

- On `Command(resume="reject")`, the tool body **never ran** (`ECHO_CALLS == []`) and
  a rejection `ToolMessage` was injected. This is exactly the behavior a write gate
  needs: **approve → run once; reject → never run, feed verdict back to the model.**

### Scenario B — fallback `HumanInTheLoopMiddleware` via `interrupt_on={"echo": True}`

```
PAUSED before tool ran? True
echo calls so far (expect 0): 0 []
__interrupt__ payload: [Interrupt(value={'action_requests': [{'name':'echo',
   'args':{'text':'hello'}, 'description': 'Tool execution requires approval...'}],
   'review_configs': [{'action_name':'echo',
   'allowed_decisions': ['approve','edit','reject','respond']}]}, id='22c20805...')]
echo calls after resume (expect 1): 1 ['hello']
RESULT B: paused=True, resumed=True, tool_ran_exactly_once=True
```

- Also paused/resumed/ran-once, but the interrupt payload is the **built-in HITL
  schema** (`action_requests` + `review_configs`), and resume must use
  `Command(resume={"decisions": [{"type": "approve"}]})`.

No tracebacks in any scenario.

## Gate API shape for Step 6 (decision A)

The Step-6 unified write-approval gate should be a `@wrap_tool_call` middleware that:

```python
from langchain.agents.middleware import wrap_tool_call
from langchain_core.messages import ToolMessage
from langgraph.types import interrupt

@wrap_tool_call
async def write_approval_gate(request, handler):
    tool_name = request.tool_call["name"]

    # 1. (Jarvis side, deterministic) decide if this call needs approval:
    #    capability lookup -> is_write_capability + TrustEngine.evaluate(...).
    #    If auto_execute_silent / not a write -> just `return await handler(request)`.

    # 2. If approval is required, PAUSE with a custom Jarvis payload:
    verdict = interrupt({
        "kind": "write_approval",
        "tool": tool_name,
        "args": request.tool_call.get("args"),
        "capability": <resolved>,
        "risk_level": <RiskAssessment>,
        "trust_level": <TrustState>,
        "run_id": <run_id>,
        "artifact_refs": <...>,
    })

    # 3. On resume, `verdict` is whatever Command(resume=<value>) supplied.
    if verdict in ("approve", {"type": "approve"}):
        return await handler(request)          # runs the tool EXACTLY ONCE
    # reject -> short-circuit, tool never runs, model gets the verdict back:
    return ToolMessage(
        content=json.dumps({"status": "rejected", "reason": ...}),
        tool_call_id=request.tool_call["id"],
        status="error",
    )
```

Resume from the API layer: `await agent.ainvoke(Command(resume=<verdict>), config={"configurable": {"thread_id": <thread>}})`.

Wrapper signature confirmed: `async def (request, handler)`; `request.tool_call` is a
dict with `name` / `args` / `id`. `interrupt(value)` takes a single value and returns
the resume value on the second pass. The gate's design is identical in shape to the
existing `capability_scope_guard` (`src/deep_runtime/middleware/capability_scope.py`),
so both can co-exist as `wrap_tool_call` middlewares — scope guard first
(fail-closed deny), then the approval gate.

## CAVEAT for gate wiring (action item, NOT a blocker)

**`build_deep_agent` (`src/deep_runtime/agent_builder.py`) does not expose
`checkpointer` (nor `interrupt_on`).** `create_deep_agent` accepts both, but the
Jarvis wrapper only forwards `model`, `tools`, `system_prompt`, `middleware`, `name`.

A LangGraph `interrupt()` requires a **checkpointer + a `thread_id` in config** to
persist state across the pause/resume boundary — without it, there is nothing to
resume into. So Step-6 must:

1. Add a `checkpointer` parameter to `build_deep_agent` and forward it to
   `create_deep_agent`. (The autonomous/GraphExecutor path already needs durable
   checkpoints; the rebuild can use the Postgres checkpointer, not just `MemorySaver`.)
2. Ensure every invoke on a gated agent passes `config={"configurable": {"thread_id": ...}}`.
3. The approval-resume surface (A2UI / API) resumes with
   `Command(resume=<verdict>)` against the same `thread_id`.

If the checkpointer is omitted, the interrupt will not behave (no persistence to
pause into). This matches the docs warning: "Human-in-the-loop middleware requires a
checkpointer to maintain state across interruptions."

## Files

- `backend/spikes/interrupt_in_wrap_tool_call/probe.py` — runnable probe (kept; throwaway).
- `docs/superpowers/spikes/2026-06-28-interrupt-in-wrap-tool-call.md` — this finding.

## Decision summary

| | Result |
|---|---|
| `wrap_tool_call` + `interrupt()` pauses before tool? | **YES** |
| `Command(resume=...)` resumes & runs tool exactly once? | **YES** |
| Reject path: tool never runs, verdict fed back? | **YES** |
| Fallback `HumanInTheLoopMiddleware` works? | YES (alternative) |
| **DECISION** | **(A) gate = `@wrap_tool_call` raising `interrupt()`** |
| Caveat | `build_deep_agent` must add a `checkpointer` param (+ thread_id on invoke) |
