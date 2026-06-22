# Deep Agents Hard Replacement — Design Spec

**Date:** 2026-06-22 · **Status:** Approved shape; ready for implementation plan
**Supersedes:** the strangler-fig / gradual-migration strategy in
[`docs/deep-agents-migration-assessment.md`](../../deep-agents-migration-assessment.md)
(Part E Option 1 + Part F phasing). **Carries forward** from that doc: the audit (Part B),
the invariants register (§B.7), the constraints (§B.8), and the **already-built, committed**
Phase 0 spike + Phase 1 `deep_runtime` foundation/middleware (commit `da8c459`).

---

## 1. Goal & principles

Replace Jarvis's bespoke agent runtime with a **LangGraph-native** runtime (`deepagents`),
and **delete the legacy internal seams entirely** — no permanent dual-runtime flag, no
permanent legacy-shaped adapters. The motivation (user): the gradual path forces us to keep
adapting the new runtime to legacy internal vocabulary (`LoopEvent`, the per-turn
`agent_loop` call shape, `GraphExecutor`'s bespoke DAG), which permanently compromises the
better architecture.

Principles:
- **Reach = backend-internal native, stable client edge** (assessment Option A). Rebuild
  runtime/execution/streaming fully native internally; keep **exactly one serializer** at
  the client boundary so the existing SSE/A2UI contract + Next.js app are untouched. The
  client contract is a versioned external API, not a legacy internal seam.
- **Mark legacy → build native in parallel → cut over per path → remove legacy completely.**
  End state: one runtime, zero legacy vocabulary, zero compatibility shims. (§5)
- **No permanent feature flag.** A short-lived build-time toggle is acceptable *within* a
  cutover step, but it is removed when that step lands. The repo never carries two runtimes.
- **Preserve every invariant** in assessment §B.7/§B.8 (capability-scope fail-closed,
  TrustEngine 4×4 gate, fail-closed risk, workspace isolation, durable resume, turn-scoped
  MCP teardown, no-direct-status-mutation, immutable plans). (§4)
- **Keep the domain layer.** `ToolRegistry`, `TrustEngine`/`RiskAssessor`, `ContextBuilder`/
  memory/world model, perception/scheduler, A2UI surface builders, and the `Plan`/`Approval`/
  `TrustState` models are unchanged; only how they are *wired into the runtime* changes.

---

## 2. Target architecture (components)

Small, independently-testable units. ✅ = already built (commit `da8c459`).

| Unit | Purpose | Interface | Depends on |
|---|---|---|---|
| `deep_runtime/model_factory` ✅ | SubAgent → `ChatAnthropic` (adaptive thinking/effort) | `build_chat_model(agent)` | langchain-anthropic |
| `deep_runtime/agent_builder` ✅ | Compile a deep agent | `build_deep_agent(agent, tools, *, extra_middleware, system_prompt, name)` | deepagents |
| `deep_runtime/middleware/capability_scope` ✅ | Per-call fail-closed scope check | `make_capability_scope_middleware(agent, workspace_id, db_factory)` | ToolRegistry |
| `deep_runtime/middleware/budget` ✅ | Per-model-call cost record | `make_budget_middleware(...)` | BudgetTracker |
| `deep_runtime/middleware/unavailable_server` ✅ | Per-turn auth_required breaker | `make_unavailable_server_middleware(...)` | provider_map / registry |
| `deep_runtime/tool_adapter` ⬜ | Wrap a Jarvis registry tool dict as a LangChain `StructuredTool` whose coroutine dispatches via `ToolExecutor.execute_tool` (keeps registry + capability gate authoritative) | `as_langchain_tool(tool_dict, tool_executor, user_id, workspace_id)` | ToolExecutor |
| `deep_runtime/event_serializer` ⬜ | **The one client-edge adapter.** `astream(stream_mode=["messages","updates"])` → the 7 existing SSE dict shapes | `astream_to_sse(compiled, input, cfg, *, agent, model, budget) -> AsyncIterator[dict]` | — |
| `deep_runtime/middleware/trust_interrupt` ⬜ | Autonomous-only: call `TrustEngine.evaluate` (+ fail-closed `RiskAssessor`); on `approval_required` raise a LangGraph `interrupt` carrying `ApprovalContext`; persist `Approval` | `make_trust_interrupt_middleware(...)` | TrustEngine, RiskAssessor, Approval |
| `deep_runtime/chat_driver` ⬜ | Build-per-turn native chat path (replaces `AgentInvoker.call_agent_stream` body): assemble system_prompt (soul + ContextPack), adapt tools, attach middleware (no trust gate), stream via serializer | same yielded dict shapes as today | the units above, ContextAssembler |
| `deep_runtime/durable_graph` ⬜ | Autonomous durable execution: a LangGraph graph + `AsyncPostgresSaver` checkpointer + `interrupt`/`Command`, replacing `GraphExecutor`/`DagRunner`/`execution_state` | `execute_run(...)`, `resume_run(...)` | checkpointer, trust_interrupt, run projection |
| `deep_runtime/run_projection` ⬜ | Thin run/step record synced from graph state so A2UI surfaces + history keep working without the legacy `TaskRun`/`TaskStep` state machine | projection read/write | DB (Plan/run record), SurfaceService |

---

## 3. Data flow

**Chat turn (native, ungated by design):**
```
routes_chat/ws → ChatProcessor._process_core
  └ async with turn_scope(on_close=close_turn_sessions):       # unchanged outer wrapper
      chat_driver.call_agent_stream(agent, message, user_id, workspace_id, ...)
        ├ system_prompt = JARVIS_SOUL_CORE + role + ContextAssembler.assemble_context(...)
        ├ lc_tools = [tool_adapter(t) for t in CapabilityResolver tools]
        ├ mw = [capability_scope, unavailable_server, budget]   # NO trust gate (user-authorized)
        ├ compiled = build_deep_agent(agent, lc_tools, extra_middleware=mw, system_prompt=...)
        └ event_serializer.astream_to_sse(compiled, {messages:[Human(message)]}, cfg)
             → yields the 7 SSE dict shapes → frontend (unchanged)
```

**Autonomous turn (native, TrustEngine-gated):**
```
SchedulerLoop / PerceptionRunner / approval-resume → durable_graph.execute_run
  └ async with turn_scope(...):
      LangGraph graph (AsyncPostgresSaver checkpointer)
        per step → same native agent + mw=[capability_scope, unavailable_server, budget, TRUST_INTERRUPT]
          ├ trust_interrupt: TrustEngine.evaluate(risk, trust) →
          │     approval_required → raise interrupt(ApprovalContext); persist Approval; pause (checkpoint)
          │     auto_execute_* → proceed
          └ resume: scheduler calls execute_run with Command(resume={decision}) → durable continue
```

Both paths run the **same** compiled agent; the only difference is whether
`trust_interrupt` middleware is attached. One runtime, two configs — replacing today's two
separate code paths while preserving the gating invariant.

---

## 4. Invariant preservation map

| Invariant (assessment §B.7/§B.8) | How preserved natively |
|---|---|
| Capability-scope enforced at tool time, fail-closed | `capability_scope` middleware (`@wrap_tool_call`) + tools dispatched only through `tool_adapter`→`ToolExecutor` (no direct MCP discovery bypass) |
| Two paths, different gating (chat ungated / autonomous gated) | Same agent; `trust_interrupt` middleware attached only on the autonomous build |
| TrustEngine 4×4 is the sole gate; risk fails closed to `high` | `TrustEngine.evaluate` + `RiskAssessor` unchanged, called *inside* `trust_interrupt`; deny-by-default contract guard retained |
| TrustEngine stays external to the runtime | Middleware only *raises* `interrupt`; decision/Approval/resume stay in Jarvis (§B.8#4) |
| Durable resume across restarts | `AsyncPostgresSaver` checkpointer + `interrupt`/`Command` replace `GraphExecutor` resume |
| No direct status mutation | `run_projection` is the only writer of the user-facing run/step record; transitions centralized there |
| Turn-scoped MCP teardown | `turn_scope` stays the **outer** `async with` around `astream`/`execute_run` (ContextVar propagation verified in Step 0 probe) |
| workspace_id isolation | threaded into every middleware factory + `tool_adapter` + `durable_graph` entry; domain services unchanged |
| Immutable plans, acyclic DAG | `PlanOutput` (frozen + cycle validation) unchanged; drives graph construction |
| Per-tool / per-agent cost + budget | `budget` middleware (per-model-call); per-tool split remains a tracked follow-up |
| Bedrock/Opus-4.8 adaptive thinking | `model_factory` via `ChatAnthropic` (Phase 0 confirmed) |
| Client SSE/A2UI contract | `event_serializer` is the single boundary; emits the exact 7 dict shapes; frontend untouched |

---

## 5. Legacy inventory, marking & removal manifest

**Marking convention.** The moment a native replacement begins for a module, stamp the
legacy module with a top-of-file banner:

```python
# LEGACY — deep-agents hard replacement. Scheduled for COMPLETE removal.
# Replaced by: src/deep_runtime/<unit>. Do not extend; do not add callers.
# Removal trigger: <cutover step N>. Spec: docs/superpowers/specs/2026-06-22-deep-agents-hard-replacement-design.md
```

A `grep -rn "LEGACY — deep-agents hard replacement" backend/src` lists everything still on
death row. **Definition of done for the whole project = that grep returns nothing AND the
files in the removal manifest no longer exist.**

**Removal rule.** Each cutover step **deletes the legacy it supersedes in the same commit**
that lands its native replacement (structure/behavior commit separation still applies:
"add native + tests" can precede "delete legacy + retarget callers", but both land within
the step — no half-migrated state merged to the integration branch).

**Inventory → replacement → removal trigger** (exact file set confirmed during writing-plans):

| Legacy component | Native replacement | Removed at |
|---|---|---|
| `orchestrator/agent_loop.py` (loop + `LoopEvent`/`LoopDone`/… types) | deepagents agent + middleware + `event_serializer` | Step 5 (after BOTH drivers are native) |
| `orchestrator/agent_invoker.py` (legacy body) | `deep_runtime/chat_driver` (rewritten in place or replaced) | Step 2 (legacy body deleted) |
| `orchestrator/api_circuit_breaker.py` | native retry + optional `model_resilience` middleware | Step 5 |
| `orchestrator/core_events.py` (internal `LoopEvent→CoreEvent` vocab) | `event_serializer` (boundary only). **Keep** the 7 SSE dict *shapes* | Step 2/5 (internal vocab removed; boundary shapes retained) |
| `services/graph_executor.py` + `dag_runner` + `step_runner` (legacy) + `step_graph_store.py` + `execution_support.py` | `deep_runtime/durable_graph` (LangGraph + checkpointer + interrupts) | Step 4 |
| `services/trust_gate.py` (side-effect helpers) | `trust_interrupt` middleware + `run_projection` | Step 4 |
| `services/execution_state.py` (TaskRun/TaskStep machine) | LangGraph graph state + `run_projection` | Step 4 |

> Note: `TrustEngine`, `RiskAssessor`, `TrustState`, `Approval`, `Plan`, `ToolRegistry`,
> `ToolExecutor`, `ContextBuilder`/memory/world model, perception/scheduler, A2UI builders
> are **NOT** legacy — they are kept and re-wired.

---

## 6. Sequencing (staged hard cutover, no permanent flag)

> `agent_loop` is shared by both paths, so it can only be deleted once BOTH drivers are
> native. Steps are ordered so each lands tested-green; the integration branch is never left
> with a half-migrated runtime.

- **Step 0 — Live-probe spike** (resolves the two open risks before committing to the rebuild):
  1. **cache_control parity** — can `ChatAnthropic` keep Anthropic prompt caching for the
     soul/role block + last tool? If not, scope the mitigation (cost regression otherwise).
  2. **thinking-delta streaming** — confirm `type:"thinking"` blocks actually stream for the
     deepagents-assembled Opus-4.8 body (else the "thinking…" UI goes silent).
  3. **turn_scope ContextVar propagation** into LangGraph tool execution (else MCP sessions
     leak — §B.7 teardown violated).
  4. **`astream` messages v1/v2** + the `langgraph_node` metadata keys for node filtering.
  Exit: all four answered; mitigations scoped. (Subagent-driven, one live `astream`.)

- **Step 1 — Edge units:** `tool_adapter` + `event_serializer` (new, fully unit-tested,
  no integration yet). Exit: serializer maps a recorded `astream` trace → the 7 SSE dicts;
  tool_adapter dispatches through `ToolExecutor` with capability re-check intact.

- **Step 2 — Chat path native + delete its legacy:** implement `chat_driver`; rewire
  `_process_core`'s three call sites to it; **characterization parity** vs legacy (tool
  selection, capability denials, cost/token fields, streamed surfaces). Then delete the
  legacy `AgentInvoker` body + internal `CoreEvent` vocab it used. Mark `agent_loop` LEGACY
  (still used by autonomous). Exit: chat parity green; chat no longer imports `agent_loop`.

- **Step 3 — Autonomous step execution native:** rewire `StepRunner` (the per-step agent
  call) to the native agent + middleware. Exit: a DAG step runs through the native agent.

- **Step 4 — Durable execution native + delete GraphExecutor/state machine:** implement
  `durable_graph` (LangGraph + `AsyncPostgresSaver` + `interrupt`) and `trust_interrupt` +
  `run_projection`; migrate approval pause/resume to `interrupt`/`Command(resume)`; rewire
  scheduler/perception/approval-route resume. Then delete `graph_executor`, `dag_runner`,
  `step_runner` (legacy), `step_graph_store`, `trust_gate`, `execution_support`,
  `execution_state`. Exit: approval-gated autonomous run pauses, persists, **survives a
  worker restart**, resumes on approval, trust reinforced/reversed correctly; fail-closed
  risk verified.

- **Step 5 — Final removal + cleanup:** delete `agent_loop` (+ `LoopEvent` types),
  `api_circuit_breaker`, and any remaining internal `CoreEvent` vocab; remove all LEGACY
  banners. Update CLAUDE.md + engineering-standards + assessment doc to the native runtime.
  Exit: `grep "LEGACY — deep-agents hard replacement"` returns nothing; removal manifest
  files are gone; full suite green.

---

## 7. Error handling & resilience

- **API retry / rate limits:** native LangChain retry; optional `model_resilience`
  (`@wrap_model_call`) ports the per-model circuit breaker if native fallback is insufficient.
- **Tool timeouts:** enforced in `tool_adapter` (`asyncio.wait_for`, mirrors the legacy 60s).
- **Tool errors / auth_required:** `unavailable_server` middleware (per-turn breaker + steer).
- **Fail-closed gates:** capability-scope and trust both deny-by-default; risk fails to `high`.
- **Generator drain:** `event_serializer` is an async generator; `_process_core`'s `finally`
  (trace + `turn_scope` teardown) must still run on early-return/cancel — verified by test.
- **Cancellation:** cooperative (`asyncio.CancelledError` cancels the `astream` task); the
  outer `async with turn_scope` cleanup runs on cancel.

---

## 8. Testing strategy

- **Characterization first:** before deleting any legacy path, capture golden traces of
  representative chat turns and one approval-gated autonomous run (tool selection, denials,
  cost fields, streamed SSE dicts, pause/resume) to assert native parity against.
- **Invariant tests:** capability fail-closed (benign out-of-scope tool — Phase-0 lesson),
  trust 4×4 matrix + deny-by-default guard, fail-closed risk, workspace isolation, durable
  resume across a simulated restart, turn_scope teardown, generator-drain-on-cancel.
- **Edge serializer tests:** recorded `astream` events → exact SSE dict shapes (offline,
  no API).
- **Live-probe results** from Step 0 recorded in the plan.
- **Suite hygiene:** the pre-existing red tests (`test_websocket`, `test_endpoint_rate_limits`,
  `test_briefing_feedback` — confirmed failing at parent `31ce42b`, env/infra-dependent) are
  out of scope; do not let the native work be blamed for them, but re-baseline before each
  cutover so new regressions are visible.

---

## 9. Risks & open questions (resolve in plan / Step 0)

1. **Prompt-caching parity (HIGH)** — `ChatAnthropic` dropping `cache_control` → input-token
   cost regression every turn. Step-0 spike; mitigate or accept-and-flag.
2. **Persistence model depth (HIGH)** — exactly how much of `TaskRun`/`TaskStep` becomes
   LangGraph checkpointer state vs. a retained thin `run_projection` for surfaces/history.
   Detailed in writing-plans; affects scheduler, perception queueing, approval resume.
3. **Live-probe unknowns (MEDIUM)** — thinking-delta streaming, turn_scope ContextVar
   propagation, `astream` v1/v2 ToolMessage visibility, node-filter metadata keys.
4. **Per-tool cost attribution (LOW)** — deferred (analytics, not safety).

---

## 10. Out of scope

- Frontend rebuild (assessment Option B) — the client SSE/A2UI contract is held stable.
- LangSmith adoption — optional, later.
- Per-tool `TokenUsage` split — tracked follow-up.
