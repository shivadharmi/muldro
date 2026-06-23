# Deep Agents Hard Replacement — Design Spec

**Date:** 2026-06-22 · **Status:** Approved shape; ready for implementation plan
**Amended by:** [`2026-06-23-agentic-redesign-design.md`](./2026-06-23-agentic-redesign-design.md) —
replaces orchestrator routing + the mandatory Planner / `PlanOutput` / `Plan` / `PlanTask` with a
pure-agentic agent registry + on-demand planning + user-creatable agents + perception act-tier
escalation. The `durable_graph` / `trust_interrupt` / `run_projection` / §4 tool-auth / §5.1
carry-forward / §12 decisions in THIS doc are unchanged and carried forward.
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
  End state: one runtime, zero legacy vocabulary, zero compatibility shims. (§6)
- **No permanent feature flag.** A short-lived build-time toggle is acceptable *within* a
  cutover step, but it is removed when that step lands. The repo never carries two runtimes.
- **Tool authorization = classify + human-trust, not hand-maintained scope.** Authorization
  is two fail-closed dimensions (behavior **class** + per-fingerprint **trust**); a classifier
  *escalates restriction* but **never grants the safe class to an untrusted tool** — that is a
  human decision. This replaces the ~250-line hand-maintained capability matrix. (§4)
- **Preserve every invariant** in assessment §B.7/§B.8 (capability-scope fail-closed,
  TrustEngine 4×4 gate, fail-closed risk, workspace isolation, durable resume, turn-scoped
  MCP teardown, no-direct-status-mutation, immutable plans). (§5)
- **Keep the domain layer.** `TrustEngine`/`RiskAssessor`, `ContextBuilder`/memory/world
  model, perception/scheduler, A2UI surface builders, and the `Plan`/`Approval`/`TrustState`
  models are unchanged; only how they are *wired into the runtime* changes. `ToolRegistry`/
  `ToolExecutor` stay but gain the classification + trust fields (§4).

---

## 2. Target architecture (components)

Small, independently-testable units. ✅ = already built (commit `da8c459`).

| Unit | Purpose | Interface | Depends on |
|---|---|---|---|
| `deep_runtime/model_factory` ✅ | SubAgent → `ChatAnthropic` (adaptive thinking/effort) | `build_chat_model(agent)` | langchain-anthropic |
| `deep_runtime/agent_builder` ✅→✏️ | Compile a deep agent. **Adopts the native `SubAgent`/`CompiledSubAgent` spec shape** for per-role construction (scoped tools, per-role model, per-role middleware) — compiled agents are **invoked directly in code, NOT via the native `task` tool** (§12) | `build_deep_agent(agent, tools, *, extra_middleware, system_prompt, name)` | deepagents |
| `deep_runtime/middleware/capability_scope` ✅→✏️ | Per-call fail-closed eligibility — **rewired to the two-dimensional check: `tool.class ∈ ROLE_ALLOWED_CLASSES[agent.role]` AND tool fingerprint is `approved`** (§4) | `make_capability_scope_middleware(agent, workspace_id, db_factory)` | ToolRegistry, tool-trust |
| `deep_runtime/middleware/budget` ✅ | Per-model-call cost record | `make_budget_middleware(...)` | BudgetTracker |
| `deep_runtime/middleware/unavailable_server` ✅ | Per-turn auth_required breaker | `make_unavailable_server_middleware(...)` | provider_map / registry |
| `services/tool_classifier` ⬜ | Discovery-time behavior classifier (L0–L3, escalation-only, fail-closed) → persists `class`, `risk_level`, `requires_approval`, `idempotent`, `classification` JSONB. Reuses `manifest_inspector.classify_tool` + the `RiskAssessor` skeleton | `classify_tool(name, description, input_schema, annotations, *, trusted) -> Classification` | manifest_inspector, RiskAssessor |
| `models/mcp_tool_trust` ⬜ | Per-`(workspace, server, fingerprint)` trust state `{quarantined, approved, blocked}` | trust read/write + `approve_server(...)` | DB |
| `config: ROLE_ALLOWED_CLASSES` ⬜ | The ~8-line role→allowed-classes policy — **the only hand-maintained authz config** | constant | — |
| `services/capability_resolver` ✏️ | Reduced to a **thin deterministic router** (read→perceiver, write→operator, sentinel table for synthetic caps). Its scope role is deleted | `route(capability) -> agent` | catalog (domain tag + class) |
| `deep_runtime/tool_adapter` ⬜ | Wrap a Jarvis registry tool as a LangChain `StructuredTool` dispatching via `ToolExecutor.execute_tool` (keeps registry + class/trust gate authoritative) | `as_langchain_tool(tool_dict, tool_executor, user_id, workspace_id)` | ToolExecutor |
| `deep_runtime/event_serializer` ⬜ | **The one client-edge adapter.** `astream(stream_mode=["messages","updates"])` → the 7 existing SSE dict shapes | `astream_to_sse(compiled, input, cfg, *, agent, model, budget) -> AsyncIterator[dict]` | — |
| `deep_runtime/middleware/trust_interrupt` ⬜ | Autonomous-only: `TrustEngine.evaluate` (+ fail-closed `RiskAssessor`); on `approval_required` raise a LangGraph `interrupt` carrying `ApprovalContext`; persist `Approval`. **Reuses the native `interrupt`/checkpoint/`Command(resume)` substrate** (PatchToolCalls repairs resume) but is a custom `wrap_tool_call` — **NOT** `interrupt_on`/`when` (`when()` is boolean-only; native approve/edit/reject/respond can't encode the 4 verdicts). Interrupt-raise node **separate from the send node** (§5.1#1) | `make_trust_interrupt_middleware(...)` | TrustEngine, RiskAssessor, Approval |
| `deep_runtime/chat_driver` ⬜ | Build-per-turn native chat path (replaces `AgentInvoker.call_agent_stream` body): assemble system_prompt (soul + ContextPack), adapt tools, attach middleware (no trust gate), stream via serializer | same yielded dict shapes as today | the units above, ContextAssembler |
| `deep_runtime/durable_graph` ⬜ | Autonomous durable execution: LangGraph graph + `AsyncPostgresSaver` checkpointer + `interrupt`/`Command`, replacing `GraphExecutor`/`DagRunner`/`execution_state` | `execute_run(...)`, `resume_run(...)` | checkpointer, trust_interrupt, run projection |
| `deep_runtime/run_projection` ⬜ | Thin run/step record synced from graph state so A2UI surfaces + history keep working without the legacy `TaskRun`/`TaskStep` state machine | projection read/write | DB (Plan/run record), SurfaceService |

---

## 3. Data flow

**Chat turn (native, ungated by design):**
```
routes_chat/ws → ChatProcessor._process_core
  └ async with turn_scope(on_close=close_turn_sessions):       # unchanged outer wrapper
      chat_driver.call_agent_stream(agent, message, user_id, workspace_id, ...)
        ├ system_prompt = JARVIS_SOUL_CORE + role + ContextAssembler.assemble_context(...)
        ├ lc_tools = [tool_adapter(t) for t in resolver.route()-eligible, class-allowed tools]
        ├ mw = [capability_scope, unavailable_server, budget]   # NO trust gate (user-authorized)
        ├ compiled = build_deep_agent(agent, lc_tools, extra_middleware=mw, system_prompt=...)
        └ event_serializer.astream_to_sse(compiled, {messages:[Human(message)]}, cfg)
             → yields the 7 SSE dict shapes → frontend (unchanged)
```
`capability_scope` re-checks per call: deny unless `tool.class ∈ ROLE_ALLOWED_CLASSES[role]`
**and** the tool's fingerprint trust is `approved` (so a quarantined/unapproved tool is
uncallable even if a class would allow it).

**Autonomous turn (native, TrustEngine-gated):**
```
SchedulerLoop / PerceptionRunner / approval-resume → durable_graph.execute_run
  └ async with turn_scope(...):
      LangGraph graph (AsyncPostgresSaver checkpointer)
        per step → same native agent + mw=[capability_scope, unavailable_server, budget, TRUST_INTERRUPT]
          ├ trust_interrupt: TrustEngine.evaluate(class-derived risk, trust) →
          │     approval_required → raise interrupt(ApprovalContext); persist Approval; pause (checkpoint)
          │     auto_execute_* → proceed   (discovered-server `mutate` tools capped by low TrustCeiling)
          └ resume: scheduler calls execute_run with Command(resume={decision}) → durable continue
```

Both paths run the **same** compiled agent; the only difference is whether
`trust_interrupt` middleware is attached. One runtime, two configs — replacing today's two
separate code paths while preserving the gating invariant.

---

## 4. Tool Authorization model (classifier + per-fingerprint trust)

Replaces the hand-maintained capability matrix (`AGENT_CAPABILITY_SCOPES` ~250 lines, the
143-entry capability taxonomy's routing/scope role, the DB scope mirror, and the second
router) with a model that **scales to unbounded, user-supplied MCP tools**. Validated by an
adversarial red-team (see assessment doc's research artifacts).

### 4.1 Foundational principle (non-negotiable)

**Static metadata controlled by a third-party MCP server can never prove read-only
*behavior*.** A destructive tool can present a read-shaped name, description, arg schema, and
`readOnlyHint:true`. Therefore:

> The classifier **proposes and escalates restriction**; it **never grants the safe class**.
> The permissive, ungated `read` class is reachable only for a tool **fingerprint a human has
> vouched for**. Absence of write-evidence is never treated as presence of read-evidence.

### 4.2 Two fail-closed dimensions (both checked on the hot path; zero LLM calls there)

1. **Behavior `class`** — `read / mutate / destroy` (+ `quarantined` / `blocked`), persisted on
   the tool, drives **eligible roles**, **routing**, and **approval-risk**.
2. **Per-fingerprint trust** — `fingerprint = sha256(name + canonical(input_schema))`, state
   `{quarantined, approved, blocked}` keyed by `(workspace_id, server, fingerprint)`.

Eligibility = `tool.class ∈ ROLE_ALLOWED_CLASSES[agent.role]` **AND** `trust == approved`.
One bad classification cannot defeat both gates — that is the defense-in-depth the red-team
required.

### 4.3 Class taxonomy

| Class | Assigned when | Eligible roles | Gating |
|---|---|---|---|
| `quarantined` | **Default** for any fingerprint not yet human-approved, *regardless of how cleanly it classifies*; also classifier exception, unresolved capability, unresolved workspace | none (excluded from offered tools) | uncallable; exit only via human approval of the fingerprint |
| `read` | approved fingerprint **and** positive read-shape (no write/sensitive arg, read verb, concrete schema); annotations may only corroborate | perceiver, librarian, planner, presenter, persona, governor | none (the only ungated-broad class — human-gated at entry) |
| `mutate` | approved fingerprint + any write signal **or** residual ambiguity (fail-closed sink for trusted-but-not-provably-read) | operator | `risk=medium`, `requires_approval`; TrustEngine gates; **discovered-server tools get a low TrustCeiling** (§4.8) |
| `destroy` | approved fingerprint + irreversible/high-blast signal (destructive verb, `destructiveHint`, credential arg, LLM high-confidence) | operator | `risk=high` → `approval_required` at **every** trust level incl. autonomous |
| `blocked` | admin-disabled, or runtime-demoted malicious (§4.7) | none | terminal until admin re-enables |

### 4.4 Classifier signal pipeline (off the hot path; discovery / re-classification only)

Each layer emits `(class_vote, confidence, evidence)`; **most-restrictive-wins** (never
highest-confidence). Severity order `read < mutate < destroy < quarantined/blocked`.

- **L0 — MCP annotations (UNTRUSTED; today dropped — must start capturing `t.annotations`):**
  `destructiveHint` → escalate `destroy`; `idempotentHint` → populate the unused
  `ToolDefinition.idempotent` column; `readOnlyHint` → **may corroborate an L2 read vote,
  never establish one**; persisted raw for audit.
- **L1 — name/description verbs (WEAK, attacker-controlled):** reuse `manifest_inspector`
  verb scan. Destructive verb → `destroy`; write verb → ≥`mutate`; read verb → LOW-confidence
  read vote (corroboration only).
- **L2 — structural schema (PRIMARY trustworthy escalation):** write-shaped arg
  (`body/content/to/recipient/target/…`) → ≥`mutate`; credential arg → `destroy`; concrete
  read-style schema + no write arg → HIGH-confidence read vote; empty/open schema → no read
  vote possible → abstain-to-`mutate`.
- **L3 — LLM (ambiguous remainder; escalation-only):** generalize `RiskAssessor` (Haiku +
  `parse_llm_json` + Pydantic + fail-closed). System prompt frames input as untrusted
  third-party data; name/description delimited. **May vote `mutate`/`destroy` freely; its
  `read` vote is honored only for an already-approved fingerprint and only as L2
  corroboration** — neutralizing prompt-injection as an escalation vector.

Fail-closed default: any error / all-abstain / low-confidence → `mutate` for an approved
fingerprint, `quarantined` for an unapproved one. **No path yields `read` from ambiguity or
an untrusted source.** Classification is persisted, **admin-overridable** (`class_source =
admin` is terminal), audited, and re-swept on a `classifier_version` bump.
Re-classification is **sticky-downward, free-upward** (auto-increase restriction only; any
decrease needs the admin path). The manifest fingerprint **includes `input_schema`** (today
it hashes name+description only).

### 4.5 The irreducible policy (the only hand-maintained authz config)

```python
ROLE_ALLOWED_CLASSES: dict[str, frozenset[str]] = {
    "perceiver": frozenset({"read"}),
    "librarian": frozenset({"read"}),
    "planner":   frozenset({"read"}),
    "presenter": frozenset({"read"}),
    "persona":   frozenset({"read"}),
    "governor":  frozenset({"read"}),   # audit-only
    "operator":  frozenset({"read", "mutate", "destroy"}),
}
# "quarantined"/"blocked" appear in no role's set → callable by no one (fail-closed by omission).
```
A 200-tool unknown server needs **zero** new config: tools quarantine → a human approves
fingerprints → approved tools self-classify → eligibility is one set-membership test.

### 4.6 Trust boundary + per-server approval UX

- **Per-`(workspace, server, fingerprint)` trust.** **Built-in connectors are fingerprinted
  too** (uniform mechanism, no `if source=="seed": skip` branch) — seeded `approved` for the
  fingerprints present at release; their hand-authored catalog class stands *while the
  fingerprint matches*. An **upstream connector update that changes a tool's schema → fingerprint
  drift → re-quarantine** for team re-vetting at release (drift detection on third-party code).
- **User-added servers start fully `quarantined`.** **Per-server approval = one human decision
  that bulk-approves the fingerprints present now** — *not* a standing trust of the server name.
  A later new/changed tool (new fingerprint) re-quarantines and prompts only for that tool
  (incremental re-approval).
- **The human approves identity; the machine enforces behavior.** Approval surface (Integrations
  page) reuses `inspect_manifest` to show the server's aggregate risk + per-tool proposed
  classes, then one click. After trust, the classifier + TrustEngine handle per-tool
  granularity (reads flow, writes gated, destroys always ask).
- In Jarvis the workspace owner *is* the admin, so "admin review" = the founder vouching for a
  server they just connected.

### 4.7 How write-as-read is prevented (two stacked barriers)

1. **Trust barrier (primary, behavior-based):** `read` is reachable only for a human-approved
   fingerprint — so a read-shaped destructive tool from an untrusted server is `quarantined`
   (uncallable) until a human reviews the server's actual behavior.
2. **Structural barrier (secondary, for already-trusted tools):** even on an approved
   fingerprint, a write-shaped/sensitive arg or write/destructive verb is an escalation that
   most-restrictive-wins can never override — so a trusted server's `send_*`/`delete_*` tool
   can never be `read` regardless of name/description/`readOnlyHint`.

**Runtime demotion (residual, unavoidable):** static analysis cannot see behavior, so a
name/schema-stable behavior change on a *trusted* tool is invisible at classification time.
Mitigate at runtime — a `read`-classed tool observed emitting a write/side-effect demotes its
fingerprint to `blocked` + alerts admin (wire to existing audit/DLQ). Monitoring, not
prevention; explicitly an accepted residual (§10).

### 4.8 Discovered-server autonomy cap

A `mutate` tool persists `risk=medium`, and the matrix returns `auto_execute_notify` for
`medium`+`autonomous` (executes without approval once graduated). So discovered-server
`mutate` tools get a **low `TrustCeiling`** (reuse the existing model) — they never reach
`auto_execute_silent/notify` without an explicit per-server autonomy opt-in.

### 4.9 Migration prerequisites (in `services/tool_classifier` + discovery sink)

- **Capture `t.annotations` at discovery** (both entry points; today dropped) and add the
  `classification` JSONB column on `ToolDefinition` (annotations live there — no second column).
- **Forbid `workspace_id=None` discovered rows** — fail discovery closed if workspace can't be
  resolved (fixes the `_register_discovered_tools` `or None` multi-tenant foot-gun).
- **Stop swallowing discovery exceptions** — a classify/persist failure → visible
  `quarantined` row + audit, never a dropped tool.
- **Reuses, not net-new:** `manifest_inspector.classify_tool` (read/risk heuristic),
  `RiskAssessor` skeleton (L3), `inspect_manifest` (admin-review surface), the `idempotent`
  column, `TrustCeiling`.

---

## 5. Invariant preservation map

| Invariant (assessment §B.7/§B.8) | How preserved natively |
|---|---|
| Capability-scope enforced at tool time, fail-closed | `capability_scope` middleware re-checks **two dimensions** per call (`class ∈ role` AND fingerprint `approved`); tools dispatched only via `tool_adapter`→`ToolExecutor` (no MCP-discovery bypass) |
| **Untrusted tools can never auto-reach the safe class** | Classifier is escalation-only; `read` requires a human-approved fingerprint (§4.1/§4.7) |
| Two paths, different gating (chat ungated / autonomous gated) | Same agent; `trust_interrupt` middleware attached only on the autonomous build |
| TrustEngine 4×4 is the sole gate; risk fails closed to `high` | `TrustEngine.evaluate` + `RiskAssessor` unchanged, called inside `trust_interrupt`; class-derived `risk_level` feeds it with no matrix change; deny-by-default guard retained |
| TrustEngine stays external to the runtime | Middleware only *raises* `interrupt`; decision/Approval/resume stay in Jarvis (§B.8#4) |
| Durable resume across restarts | `AsyncPostgresSaver` checkpointer + `interrupt`/`Command` replace `GraphExecutor` resume. **Resume is also durable *replay*** (a Graph-API tool node re-runs from the top), so external writes need idempotency (§5.1#1) |
| No direct status mutation; transitions validated | `run_projection` is the only writer of the user-facing run/step record **and** enforces the status-transition allow-set (illegal `from→to` raises a typed error); not a permissive setter (§5.1#2) |
| Turn-scoped MCP teardown | `turn_scope` stays the **outer** `async with` around `astream`/`execute_run` (ContextVar propagation verified in Step 0) |
| workspace_id isolation | threaded into middleware, `tool_adapter`, classifier, trust-state, `durable_graph`; **no `workspace_id=None` tool rows** (§4.9) |
| Immutable plans, acyclic DAG | `PlanOutput` (frozen + cycle validation) unchanged |
| Per-tool / per-agent cost + budget | `budget` middleware (per-model-call); per-tool split a tracked follow-up |
| Bedrock/Opus-4.8 adaptive thinking | `model_factory` via `ChatAnthropic` (Phase 0 confirmed) |
| Client SSE/A2UI contract | `event_serializer` is the single boundary; emits the exact 7 dict shapes |

### 5.1 Execution-engine carry-forward (Step 4 — `durable_graph` / `run_projection`)

The legacy `GraphExecutor`/`DagRunner`/`execution_state` stack carries behavioral semantics that
LangGraph does **not** inherit for free. Each must be explicitly re-homed in `durable_graph`/
`run_projection` or it regresses silently. (Confirmed by code audit + LangGraph-docs grounding.)

1. **External writes are at-least-once across crash/resume — exactly-once is Jarvis's job, not the
   framework's.** Today the DAG is *flush-only* inside the loop (the only `commit()` is at the
   lifecycle boundary — `graph_executor.py:385/479/491/533`), so a kill mid-segment rolls back the
   flushed `completed` status and `get_ready_steps` re-picks the still-`running`/`pending` step →
   `run_step_action` re-fires → **double `email.send`**; there is **no idempotency key anywhere** in
   step execution. `AsyncPostgresSaver` adds durable resume but **also durable *replay***: a
   Graph-API tool node re-runs from the top on resume (docs: "side effects before the pause run
   again"), `pending-writes` spares only *completed sibling* nodes, `@task` result-caching is
   Functional-API-only (a `ToolNode` is plain Graph-API and gets none), and the **default
   `durability="async"`** can lose even a completed step's checkpoint. **Requirement:** put each
   external write in its own minimal node; guard it behind an idempotency ledger keyed
   `(workspace_id, plan_step_id)` (read-before-write / "already-done?") that short-circuits a replay;
   `durability="sync"` for irreversible-write nodes; keep the TrustEngine `interrupt` and the send in
   **separate** nodes so an interrupt-replay cannot re-send. (§8, §10 risk 7, §9 idempotency test.)

2. **Validated status transitions (illegal → RAISE), not a permissive setter.** Port
   `RUN_TRANSITIONS`/`STEP_TRANSITIONS`: `run_projection` checks every `from→to` against the
   allow-set and raises on an illegal one. Load-bearing today — `dag_runner` relies on a stale
   `awaiting_reauth→awaiting_reauth` being *invalid-and-swallowed* to break the ready-batch loop
   (`dag_runner.py:217-227`); the native path must preserve the guard or replace the logic that
   leans on it.

3. **Atomic OAuth re-auth deferral.** On `auth_required` (`McpAuthRequiredError` or a structured
   `auth_required` step-output envelope) the run is parked in a durable `awaiting_reauth` state, the
   integration row is flagged `needs_reauth`, and the provider's perception sources are paused —
   **all three in one transaction** (all-or-nothing; no orphan where the integration is flagged but
   the run never deferred). `awaiting_reauth` re-queues to `pending` on reconnect (**not** terminal
   failure); there is deliberately **no step-level `awaiting_reauth`**. Distinct from the in-turn
   `unavailable_server` breaker (§8), which only steers the model this turn. `ReauthService` is kept
   + re-wired.

4. **Advisory post-run verification + `partially_completed`.** With a verifier wired, a finished run
   goes `partially_completed` first; verification is **advisory-only** — it records a verdict for
   trust-reversal / memory writeback and **never** flips a completed run to `failed` — then promotes
   to `completed` when no step actually failed. `OutcomeLearner` + `Verifier` are kept + re-wired
   (Step 4's "trust reinforced/reversed" exit criterion already assumes them).

5. **Plan-status reconciliation.** On terminal run exit, mirror status onto the parent `Plan`
   (completed/partially_completed→completed, failed/timed_out→failed, cancelled→cancelled); skip
   non-terminal statuses; never resurrect an already-terminal Plan. Without it a Plan stays
   `created`/`executing` forever and stale `created` plans poison every daily briefing (the "phantom
   critical alert" regression — briefings read `Plan.status`).

6. **Inter-step reference resolution (Planner-contract coupling).** `PlanStep.input_data` may carry
   `{task_id}.output.field` placeholders resolved from upstream completed-step outputs before a step
   runs (today `StepGraphStore.resolve_step_references`, fail-soft on a missing task/field). Preserve
   the resolution natively or change the Planner contract — do **not** silently pass an unresolved
   placeholder to the tool. Surface as a Planner-output coupling in writing-plans.

7. **Run-level token/cost rollup.** Aggregate `input_tokens`/`output_tokens`/`cost_usd` onto the run
   record, accumulating across resume segments and **idempotent per segment** (re-rolling the same
   segment id must not double-count); history/detail endpoints read these directly off the run row.
   Distinct from the per-model-call `budget` middleware (§5 table).

---

## 6. Legacy inventory, marking & removal manifest

**Marking convention.** When a native replacement begins for a module, stamp it:

```python
# LEGACY — deep-agents hard replacement. Scheduled for COMPLETE removal.
# Replaced by: src/deep_runtime/<unit>. Do not extend; do not add callers.
# Removal trigger: <cutover step N>. Spec: docs/superpowers/specs/2026-06-22-deep-agents-hard-replacement-design.md
```

`grep -rn "LEGACY — deep-agents hard replacement" backend/src` lists everything on death row.
**Definition of done = that grep returns nothing AND the manifest files no longer exist.**

**Removal rule.** Each cutover step **deletes the legacy it supersedes in the same commit**
that lands its native replacement (structure/behavior commit separation applies: "add native
+ tests" may precede "delete legacy + retarget callers", but both land within the step).

**Inventory → replacement → removal trigger** (exact file set confirmed in writing-plans):

| Legacy component | Native replacement | Removed at |
|---|---|---|
| `orchestrator/agents.py` `AGENT_CAPABILITY_SCOPES` (~250 lines) | `ROLE_ALLOWED_CLASSES` (~8 lines) + classifier (§4) | Step 1 (authz) |
| Agent `capability_scope` JSONB **DB mirror** (`AgentRegistry.seed_defaults` force-sync) | none (drop, or repopulate read-only for introspection) | Step 1 |
| `chat_pipeline.resolve_plan_routing` synthetic-cap branches (the **second router**) | one `capability_resolver` router + typed sentinel table | Step 1 |
| 143-entry capability taxonomy's **routing/scope role** | `capability` demoted to a domain tag + `class` (§4) | Step 1 (role removed; tag retained) |
| `orchestrator/agent_loop.py` (loop + `LoopEvent`/`LoopDone` types) | deepagents agent + middleware + `event_serializer` | Step 5 (after both drivers native) |
| `orchestrator/agent_invoker.py` (legacy body) | `deep_runtime/chat_driver` | Step 3 |
| `orchestrator/api_circuit_breaker.py` | native `ModelRetry`/`ModelFallback` for the common case **+** the stateful breaker ported as a **process-global `@wrap_model_call` singleton** (not pure deletion — §12); fix the latent `is_open()` bug | Step 5 |
| `orchestrator/core_events.py` (internal `LoopEvent→CoreEvent` vocab) | `event_serializer` (boundary only). **Keep** the 7 SSE dict *shapes* | Step 3/5 |
| `services/graph_executor.py` + `dag_runner` + legacy `step_runner` + `step_graph_store.py` + `execution_support.py` | `deep_runtime/durable_graph` | Step 4 |
| `services/trust_gate.py` (side-effect helpers) | `trust_interrupt` middleware + `run_projection` | Step 4 |
| `services/execution_state.py` (TaskRun/TaskStep machine) | LangGraph graph state + `run_projection` (must re-home the **validated allow-set + raise-on-illegal**, not just single-writer — §5.1#2) | Step 4 |
| `dag_runner._defer_for_reauth` + `execution_support._detect_auth_required` | `durable_graph` re-auth deferral node (atomic 3-write defer + `awaiting_reauth` — §5.1#3) | Step 4 |
| `step_graph_store.resolve_step_references` (`{task_id}.output.field` wiring) | native graph resolution or Planner-contract change (§5.1#6) | Step 4 |

> Kept (re-wired, **not** legacy): `TrustEngine`, `RiskAssessor`, `TrustState`/`TrustCeiling`,
> `Approval`, `Plan`, `ToolRegistry`, `ToolExecutor`, `manifest_inspector`, `inspect_manifest`,
> `ReauthService` (§5.1#3), `OutcomeLearner`/`Verifier` (§5.1#4),
> `ContextBuilder`/memory/world model, perception/scheduler, A2UI builders.

---

## 7. Sequencing (staged hard cutover, no permanent flag)

> `agent_loop` is shared by both paths, so it can only be deleted once BOTH drivers are
> native. Each step lands tested-green; the integration branch is never half-migrated.

- **Step 0 — Live-probe spike** (resolve open runtime risks): (1) `ChatAnthropic` prompt-cache
  parity; (2) thinking-delta streaming for the deepagents Opus-4.8 body; (3) `turn_scope`
  ContextVar propagation into LangGraph tool execution; (4) `astream` messages v1/v2 +
  `langgraph_node` metadata keys. Exit: all four answered; mitigations scoped.

- **Step 1 — Tool authorization (do this FIRST — it closes a live safety hole).** The
  committed `capability_scope` middleware is **not yet wired** into `agent_builder`, so a
  native agent currently ships with **no** scope enforcement. Steps: wire `capability_scope`
  into `agent_builder.extra_middleware` on **every** agent behind a **startup assert** that
  refuses to build an agent lacking the guard; capture MCP `annotations` at discovery + add the
  `classification` JSONB column + forbid `workspace_id=None` rows; build `tool_classifier`
  (reusing `manifest_inspector`/`RiskAssessor`) and the `mcp_tool_trust` table; add
  `ROLE_ALLOWED_CLASSES`; rewire `capability_scope` to the two-dimensional check; consolidate
  the two routers into one `capability_resolver` (typed sentinel table). **Safety gate:**
  generate a **scope-delta report** (per agent: tools/capabilities gained or lost) under the
  new class model vs today's `AGENT_CAPABILITY_SCOPES`, review it as a security artifact, and
  assert the one hard property — **no read-only role gains a `mutate`/`destroy` class**. The
  *intended* deltas (e.g. operator gaining sibling writes like `doc.update_block` — the
  class-level-vs-per-tool coarsening we deliberately chose) are reviewed and accepted, not
  asserted away. Then delete `AGENT_CAPABILITY_SCOPES`, the DB mirror, and the second router.
  Exit: enforcement wired + startup-asserted; scope-delta reviewed; classifier red-team tests
  pass (§9).

- **Step 2 — Edge units:** `tool_adapter` + `event_serializer` (new, unit-tested, no
  integration). Exit: serializer maps a recorded `astream` trace → the 7 SSE dicts;
  tool_adapter dispatches through `ToolExecutor` with the class+trust re-check intact.

- **Step 3 — Chat path native + delete its legacy:** implement `chat_driver`; rewire
  `_process_core`'s three call sites; **characterization parity** vs legacy. Delete the legacy
  `AgentInvoker` body + internal `CoreEvent` vocab. Mark `agent_loop` LEGACY (still used by
  autonomous). Exit: chat parity green; chat no longer imports `agent_loop`.

- **Step 4 — Durable execution native + delete GraphExecutor/state machine:** implement
  `durable_graph` (LangGraph + `AsyncPostgresSaver` + `interrupt`) + `trust_interrupt` +
  `run_projection`; migrate approval pause/resume to `interrupt`/`Command(resume)`; rewire
  scheduler/perception/approval-route resume; route the autonomous step path through the shared
  router (pinned to static `read_only`, as its own characterized rollout). Delete
  `graph_executor`, `dag_runner`, legacy `step_runner`, `step_graph_store`, `trust_gate`,
  `execution_support`, `execution_state`. **This step must preserve the §5.1 carry-forward
  semantics** — per-step idempotency ledger, validated status transitions, atomic re-auth defer,
  advisory verification + `partially_completed`, plan-status reconciliation, inter-step reference
  resolution, and the run-level token/cost rollup. Exit: approval-gated autonomous run pauses,
  persists, **survives a worker restart**, resumes on approval, trust reinforced/reversed;
  fail-closed risk verified; **and a step whose external side effect already succeeded is NOT
  re-executed on resume** (idempotency ledger verified by a kill-mid-segment test, §9).

- **Step 5 — Final removal + cleanup:** delete `agent_loop` (+ `LoopEvent` types),
  `api_circuit_breaker`, remaining internal `CoreEvent` vocab; remove all LEGACY banners; update
  CLAUDE.md + engineering-standards + assessment doc. Exit: the LEGACY grep returns nothing;
  manifest files gone; full suite green.

---

## 8. Error handling & resilience

- **API retry / rate limits:** native `ModelRetryMiddleware` + `init_chat_model(max_retries=…)`
  for transient/rate-limit backoff and `ModelFallbackMiddleware(*models)` for per-model outage
  fallback. **But** the stateful `AnthropicCircuitBreaker` (OPEN/HALF_OPEN, 5-fail threshold, 120s
  cooldown, per-model fast-fail) is **not** covered by native stateless retry — port it as a custom
  `@wrap_model_call` closing over a **process-global, model-keyed singleton** (a per-turn-built
  middleware silently resets and never trips, since agents are now built per turn/step). Fix the
  latent `is_open()` bug in the same port (§12).
- **Step-level retry (distinct from API retry):** each step retries up to `max_retries` with
  exponential backoff `min(2**n, 30)s`, surfacing `retry_count`/`retry_after_seconds` on the step
  (legacy `failed→pending` edge); map onto a LangGraph node `RetryPolicy` or explicit
  `run_projection` logic — confirm `RetryPolicy` covers the backoff cap + per-node max.
- **Tool / step / run timeouts:** per-tool in `tool_adapter` (`asyncio.wait_for`, legacy 60s);
  **step-level (120s default, per-step overridable) and run-level (600s for `source=background`,
  unlimited for user-initiated) produce a distinct `timed_out` status (not `failed`)** with its own
  transition edges — preserve both the enforcement and the `timed_out` vs `failed` distinction
  (history + Plan reconciliation depend on it).
- **Idempotent side effects (REQUIRED):** treat LangGraph as at-least-once — each external-write
  node checks an idempotency ledger keyed `(workspace_id, plan_step_id)` before firing and records
  completion in the same node so a replay short-circuits; `durability="sync"` for irreversible
  writes; the send node is separate from the `interrupt` node (§5.1#1, §10 risk 7).
- **Tool errors / auth_required:** `unavailable_server` middleware (per-turn breaker + steer) — the
  **in-turn** breaker; the **run-level** durable re-auth deferral (atomic defer + `awaiting_reauth`)
  is §5.1#3.
- **Fail-closed gates:** class+trust eligibility and TrustEngine both deny-by-default; risk and
  classification both fail to the most-restrictive outcome.
- **Generator drain:** `event_serializer` is an async generator; `_process_core`'s `finally`
  (trace + `turn_scope` teardown) must still run on early-return/cancel — verified by test.
- **Cancellation:** cooperative (`asyncio.CancelledError` cancels the `astream` task); the outer
  `async with turn_scope` cleanup runs on cancel. Must still produce the correct **terminal fan-out
  via `run_projection`: run→cancelled, in-flight steps→cancelled, not-yet-started steps→skipped**.
  Map the `cancel_run()` API + per-run cancel registry (called by routes/scheduler) onto astream
  task cancellation, and verify LangGraph interrupts a tool-executing node only **between tool
  rounds** (not mid-tool-call).

---

## 9. Testing strategy

- **Characterization first:** before deleting any legacy path, capture golden traces of
  representative chat turns and one approval-gated autonomous run to assert native parity.
- **Authz scope-delta gate:** diff the new class-based scope vs today's
  `AGENT_CAPABILITY_SCOPES` per agent; **assert no read-only role gains `mutate`/`destroy`**;
  the intended coarsening deltas are reviewed + accepted (not asserted to zero) before
  deleting the literal.
- **Classifier red-team tests** (encode the verified attacks): a read-shaped destructive tool
  from an untrusted server is `quarantined` (not `read`); prompt-injection in description cannot
  flip an untrusted tool to `read`; a write-shaped arg forces ≥`mutate` even with `readOnlyHint`;
  a new tool on a trusted server re-quarantines; cross-workspace verdicts never leak.
- **Invariant tests:** two-dimensional fail-closed eligibility, trust 4×4 + deny-by-default,
  fail-closed risk, workspace isolation (no `None` rows), durable resume across a simulated
  restart, turn_scope teardown, generator-drain-on-cancel.
- **Idempotency-on-resume (§5.1#1):** simulate a hard kill *after* a write tool returns but
  *before* its checkpoint commits; on resume assert the write is NOT repeated (ledger
  short-circuits) — e.g. `email.send` fires exactly once.
- **Carry-forward tests (§5.1):** re-auth deferral atomicity (run→`awaiting_reauth` + integration
  flag + source pause are all-or-nothing; reconnect re-queues to `pending` and resumes without
  double-executing); state-machine validation (`run_projection` raises on `completed→running`,
  accepts `failed→pending` / `running→timed_out` / `awaiting_reauth→pending`); step-level retry
  (failed→pending up to `max_retries` with capped backoff, permanent-fail on exhaustion); run/step
  timeouts → `timed_out` (≠ failed), user-initiated runs uncapped; plan reconciliation (terminal
  run advances Plan; non-terminal leaves it; never resurrects a terminal Plan); advisory
  verification (a completed run with a negative verdict stays `completed`, verdict still drives
  trust reversal); cancellation fan-out (run=cancelled, in-flight=cancelled, not-started=skipped);
  reference resolution (`{upstream}.output.field` resolves; missing ref is fail-soft).
- **Edge serializer tests:** recorded `astream` events → exact SSE dict shapes (offline).
- **Suite hygiene:** the pre-existing red tests (`test_websocket`, `test_endpoint_rate_limits`,
  `test_briefing_feedback` — confirmed failing at parent `31ce42b`, env/infra-dependent) are out
  of scope; re-baseline before each cutover so new regressions stay visible.

---

## 10. Risks & open questions (resolve in plan / Step 0)

1. **Prompt-caching parity (HIGH)** — `ChatAnthropic` dropping `cache_control` → input-token
   cost regression. Step-0 spike; mitigate or accept-and-flag.
2. **Persistence model depth (HIGH)** — how much of `TaskRun`/`TaskStep` becomes checkpointer
   state vs. a thin `run_projection`. Detailed in writing-plans; affects scheduler, perception
   queueing, approval resume. Also: on resume **refresh the ContextPack if paused beyond a
   staleness threshold (today 30 min)** — the checkpointer restores graph state, not context
   freshness; retain the resumable-status guard. And several concerns currently co-tenant in
   `run.checkpoint` JSONB via non-clobbering merges (trace_rollup, the auto-executed trust trail,
   the verification verdict, the execution snapshot) — the checkpointer/`run_projection` split must
   preserve **all** of them.
3. **Behavior-blind classification (MEDIUM, accepted residual)** — static analysis can't see a
   trusted tool's runtime behavior change; mitigated by runtime demotion (§4.7), not prevented.
4. **Per-server approval is a product dependency (MEDIUM)** — the Integrations-page approval flow
   (aggregate risk + per-tool classes + one-click) must exist for user-added MCP to be usable.
5. **Live-probe unknowns (MEDIUM)** — thinking-delta streaming, turn_scope ContextVar, `astream`
   v1/v2, node-filter metadata.
6. **Per-tool cost attribution (LOW)** — deferred (analytics, not safety).
7. **Double side-effect on resume (HIGH)** — today's DAG is flush-only inside the loop (commit only
   at the lifecycle boundary), so a kill mid-segment rolls back the flushed `completed` status and
   `get_ready_steps` re-picks the still-`running`/`pending` step → re-execution → double
   `email.send`; there is no idempotency key anywhere in step execution. `AsyncPostgresSaver` adds
   durable resume but **also durable replay** (a plain Graph-API tool node re-runs from the top;
   `pending-writes` spares only completed *sibling* nodes; default `durability="async"` can drop a
   completed step's checkpoint) — it does **not** remove this risk. Closed only by the per-step
   idempotency ledger (§5.1#1, §8). Resolve the ledger design + `durability` mode in writing-plans.

---

## 11. Out of scope

- Frontend rebuild (assessment Option B) — the client SSE/A2UI contract is held stable.
- LangSmith adoption — optional, later.
- Per-tool `TokenUsage` split — tracked follow-up.

---

## 12. Native Deep Agents feature adoption decisions

Decision register from a doc-grounded re-research of the deepagents feature surface
(Skills, SubAgents, Memory/backends, TodoList, Summarization, HITL, model resilience,
the full customization scope) against every component the spec plans to build custom.
Verdicts are calibrated to an adversarial pass against the §5/§5.1 invariants — the
optimistic mapping was downgraded wherever a native feature would break an invariant.
**Net: native config can shrink the *plumbing*; the safety/correctness core stays custom.**

| Native feature | Component it could touch | Decision | Why (degree from adversarial pass) |
|---|---|---|---|
| `AsyncPostgresSaver` + `interrupt`/`Command` | `durable_graph` pause/resume | **adopt-as-config** | Native is the durable substrate (already in §3/§7). `store=` is NOT load-bearing (cross-thread memory, not run durability); thread `workspace_id` into `thread_id`/projection/ledger keys. All §5.1 carry-forward stays custom (durable *replay*). |
| HITL `interrupt`/checkpoint/`Command` + PatchToolCalls | `trust_interrupt` | **adopt-as-config (substrate only)** | Hosts pause/persist/resume. The 4×4 verdict stays a custom `wrap_tool_call`; **never** `interrupt_on`/`when` (boolean-only, no `notify`/`blocked` tier). Approval persistence + `ApprovalContext` stay custom; interrupt-node ≠ send-node (§5.1#1). |
| `SubAgent`/`CompiledSubAgent` spec shape | per-role agent construction (`agent_builder`) | **adopt-as-config (shape)** | Free per-role tool-scoping + per-role model + middleware. But invoke compiled agents **directly** — the `task` tool is LLM-routed by `description`, has no deterministic-mapping flag, and pulls a non-strippable `general-purpose` subagent that bypasses the `ToolExecutor` gate. Deterministic router + per-call `capability_scope` stay custom (subagent tool list is build-time, not a per-call gate). |
| `ModelRetry` + `ModelFallback` + `init_chat_model(max_retries)` | `model_resilience` / `api_circuit_breaker` | **adopt-as-config + custom port** | Native covers transient retry + per-model fallback. The **stateful** breaker (OPEN/HALF_OPEN, threshold, cooldown, fast-fail) must port as a **process-global `@wrap_model_call` singleton** — a per-turn middleware resets and never trips. Fix latent `is_open()` (defined as `is_available`; `jarvis.py:264` `hasattr`-guards a non-existent method → health always reports `False`). |
| Native graph state / `stream.values` | `run_projection` **read** source | **augment (read only)** | Native durably holds the state the projection reads. The validated transition allow-set (raise-on-illegal), the 12/10-status served record, `timed_out`≠`failed`, reconciliation (§5.1#2/#5) stay custom — no native transition concept; checkpointer state is NOT the served history record. |
| `StateBackend` (default) scratch + large-tool-result offload | (none today) | **augment (free)** | Comes free on the runtime; nothing to delete (`agent_loop` has no scratch/offload). Durable artifacts stay on the custom S3/MinIO + Postgres (`workspace_id` NOT NULL) store; do **not** route through `StoreBackend` (namespace-convention = **fail-open** vs the fail-closed FK). |
| `@dynamic_prompt` | ContextPack injection | **augment (optional seam)** | Optional per-turn `system_prompt` seam; static build-per-turn assembly is equally fine. ContextPack content + TriSearch stay custom. Do **not** route preferences through `MemoryMiddleware` — it namespaces on `user_id` → cross-workspace bleed (multi-tenant violation). |
| Skills (`SkillsMiddleware`) + `StoreBackend(namespace=workspace_id)` | role prompts, "procedures", capability summary, memory | **keep custom; Skills = augment-only, optional, post-cutover** | Activation is **model-chosen** (`read_file` on SKILL.md) → breaks per-role determinism ("only Planner sees PLANNER_PROMPT_V2") and adds a mid-turn instruction-injection surface (§4.1). Static markdown: no typed schema, no decay/TTL, no fail-closed isolation, load-once-per-thread. Role methodology stays in `system_prompt` (**role prompts are NOT a removal target**). Skills are only ever a NET-NEW home for *new* agent-authored playbooks. |
| `TodoListMiddleware` / `write_todos` | Planner / `PlanOutput` | **keep custom** | A Todo is flat free-form (3 statuses, whole-list-replace, no capability/edges/IDs/acyclicity); nothing executes/routes off it. At most a derived UI scratchpad → A2UI `StepList`. |
| `SummarizationMiddleware` | conversation-history load | **keep custom (chat); optional autonomous safety-net** | Wrong target for chat history (DB-backed Postgres SELECT, workspace-scoped, 4 non-agent consumers, no checkpointer on the chat path). MAY be enabled as an in-run context-overflow net on `durable_graph` only (must not compact away approval-relevant tool results). |
| native annotations / `allowed-tools` / `readOnlyHint` | `tool_classifier` + trust + `ROLE_ALLOWED_CLASSES` | **keep custom** | Adopting native would **invert §4.1**: `allowed-tools` is experimental prompt text (doesn't restrict), and MCP hints are exactly the untrusted signal the classifier refuses to let establish a `read` vote. |
| `SubAgent['tools']` / `FilesystemPermission` / HITL | per-call `capability_scope` | **keep custom** | No native per-tool-**call** authz gate (subagent tool list is build-time; `wrap_tool_call` is the only per-call seam). The fail-closed two-dimensional check is the compensating control on the ungated chat path. |
| native `astream` / `stream_events` | `event_serializer` | **keep custom** | The one deliberate client-boundary adapter; native shapes ≠ the exact 7 Jarvis SSE dicts. |
| `langchain-mcp-adapters` / native `StructuredTool` | `tool_adapter` | **keep custom** | Must dispatch through `ToolExecutor` so the registry + class/trust gate + TurnScope teardown stay authoritative; native loading bypasses all three. |
| `PatchToolCalls` / native ToolRetry | `ReauthService` atomic re-auth defer | **keep custom** | No native transactional construct for the atomic 3-write defer + durable `awaiting_reauth` re-queue (§5.1#3); PatchToolCalls only repairs dangling tool calls. |

**Validate in Step 0 (these adoptions hinge on installed-version behavior):**
- Can a `wrap_tool_call` raise `interrupt()` from inside a tool wrapper (vs only a graph node)? If not, `trust_interrupt` computes the verdict in `wrap_tool_call` but the interrupt moves to a dedicated node (changes its interface).
- Does `excluded_middleware` strip the auto-added `general-purpose` subagent / `SubAgentMiddleware` when we invoke compiled agents directly and never use `task`? If non-strippable, confirm the direct-invoke path never exposes the LLM-routed delegation surface.
- Is `AnthropicPromptCachingMiddleware` in the default stack and does `ChatAnthropic` preserve `cache_control`? Decides risk 1 **and** the `@dynamic_prompt` vs static-assembly cache-boundary choice (a dynamic ContextPack sits outside the cacheable prefix either way).
- Confirm `durability` (sync/async) semantics and whether a plain Graph-API `ToolNode` gets any resume result-caching — the §5.1#1 idempotency-ledger design depends on the verified replay behavior, not the docs assumption.
