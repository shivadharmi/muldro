# Chat Permission Model — single-lead chat + action-time permission modes

> **Status:** DESIGN LOCKED (2026-07-13), pressure-tested (2 adversarial critics, findings applied).
> Supersedes A-5/B5 (the "single lead WITH Planner, mode==ask" design) and the transient
> "planless P1" draft. Implement via superpowers:subagent-driven-development, dormant behind flags,
> legacy byte-neutral. On the branch `rebuild/first-principles`.

## 1. Vision (end state)
Replace the chat path's per-step loop + separate presenter step with ONE deep "lead" agent that
plans its own tactics (deepagents `write_todos`), acts, and replies inline. Safety moves from an
upfront plan-bounded scope to **action-time confirmation** driven by a **permission mode** the user
chooses — the Claude Code model: `bypass` (never ask) | `ask` (confirm every write) | `auto`
(confirm only risky writes, via `RiskAssessor`). The old `mode` (ask/plan/execute) is retired.
The **autonomous path is unchanged** (Planner → DAG → TrustEngine async approval; no synchronous
user to confirm).

## 2. Why phased (the pressure-test finding that set the order)
The Planner does THREE jobs on chat: (a) the plan surface, (b) the **plan-union capability_scope**
(the write bound that keeps the ungated path safe), and (c) it **emits the `system.*` steps**
(set_goal/schedule_reminder/set_instruction/add_to_brief — which have NO backing tool; they run only
as Planner-produced steps via `SystemCapabilityHandler`). Dropping the Planner removes (b) and (c)
with no replacement until later phases (the write-bound's replacement is P2's **gate**; `system.*`'s
replacement is **tools**). ⟹ **Keep the Planner until its jobs are re-homed.** Sequencing:

| Phase | Delivers | Planner |
|---|---|---|
| **P1** | single-lead chat execution + `bypass` mode (ungated like today, plan-union scope) | **KEPT** |
| **P2** | synchronous approval subsystem (spike + interrupt→approve→resume) → `ask`/`auto` gate at action time | kept |
| **P2.5** | drop the Planner (planless) — the gate now bounds writes; `system.*` promoted to tools | **DROPPED** |
| **P3** | todos surface + retire old `mode` → `permission_mode` (frontend) | — |

---

## 3. P1 — single-lead chat + `bypass` (the immediate build)

### 3.1 Shape
On `runtime=="deep"` AND `deep_single_lead` AND `permission_mode=="bypass"`, `_process_core` runs the
**single lead** instead of the per-step loop + presenter step. The Planner still runs (plan surface +
plan-union scope + `system.*` steps). Same safety posture as today's ungated chat (plan-bounded,
ungated) — P1 is a **execution restructure** (one lead vs N per-step calls + presenter), NOT a new
authority model. Any other `permission_mode` (the default) stays on the legacy per-step path — because
`ask`/`auto` need P2's gate to be safe; honoring them by executing ungated would fail OPEN.

### 3.2 Control flow (in `_process_core`)
Unchanged prefix: intent classify → `_bump_perception_for_sources(sources)` → (fast_plan | Planner) →
`persist_plan_record` → `log_interaction` → `PlanReady` → `resolve_plan_routing` (for `user_steps`).
Then:
```
runtime = await self._invoker.effective_chat_runtime()
if runtime == "deep" and self._settings.deep_single_lead and permission_mode == "bypass":
    # SINGLE-LEAD PATH
    # (a) deterministic system.* steps (Planner-produced; plan-parameterized, no data dep) — KEPT
    for step in plan.steps:
        if step.actor != "user" and step.capability.startswith("system."):
            yield SystemStepResult(key=..., output=await handle_system_capability(step, plan, ...))
    if user_steps: yield UserActionsReady(...)
    # (b) build the lead (plan-union scope via 5a derive_lead_scope) + assemble context
    lead = await self._invoker.build_chat_lead(plan.steps, workspace_id)
    context_block = assemble_context("lead", message, ...) + history + plan summary
    # (c) stream + RE-HOME the presenter-output block (C-CORR2):
    async for frame in self._invoker.stream_deep_lead(lead, tools=None, message=message,
                                context_block=context_block, user_id, workspace_id, intent, trace):
        yield agent_event_from_sse(frame)
        if frame["event"] == "agent_done":
            presenter_text = frame.get("text", "")
            yield Presentation(text=strip_surface_blocks(presenter_text))   # else: no reply persisted, empty bubble
else:
    ... legacy per-step loop (UNCHANGED) ...
# SHARED TAIL (unchanged): run_completed, extract_surface_spec+push_presenter_surface, learner, shadow
```

### 3.3 Pressure-test fixes baked in
- **C-CORR2 (Critical) — re-home the presenter output.** `stream_deep_lead` emits NO `Presentation`
  frame (only agent_start/thinking/text_delta/tool_call/tool_result/agent_done/…). The branch MUST
  synthesize `Presentation(strip_surface_blocks(agent_done.text))`, and the shared tail MUST run
  `extract_surface_spec` + `push_presenter_surface` + the learner spawn on `presenter_text`. Without
  this the reply is never persisted (`routes_chat` persists only on `Presentation`), the chat bubble
  is empty (frontend sets content only on `response`), and no surface/learning fires.
- **C-CORR1 (Critical) — DISSOLVED by keeping the Planner.** `system.*` steps are still produced and
  run deterministically in the branch (§3.2a). Verified: `handle_system_capability(step, plan, …)`
  has no dependency on prior agent-step outputs, so running them before the lead is behavior-equivalent.
- **C-SEC1 (Critical) — DISSOLVED by keeping plan-union scope.** P1 uses 5a's `derive_lead_scope`
  (plan-bounded, fail-closed), NOT a coarse whole-catalog scope. No `derive_workspace_scope` in P1.
- **C-SEC2 (Important) — write_lock fail-closed on the bypass write path.** `write_lock` no-ops when
  `redis is None and not require_redis` (default false). For the ungated bypass single-lead write
  path, treat the effective posture as fail-closed: if Redis is unavailable, writes must not proceed
  unserialized. (Implementation: pass a per-path require-redis, or assert redis presence for the
  bypass build; do NOT rely on the global default.) Also: the idempotency LEDGER is autonomous-only;
  `stream_deep_lead` has no ledger — acceptable for a non-pausing bypass turn (LangGraph never replays
  a write), but do not claim "idempotency" in the narrative — it is serialization only.
- **C-SEC3 (Important) — `permission_mode` plumbing.** New INDEPENDENT field; **exact-equality**
  allowlist (`== "bypass"`); **default is a non-bypass value**; NEVER derived/computed from the legacy
  `mode`. Regression tests must pin: `schedule_dispatch` `custom_agent_task` (mode="execute", NO user
  present) and `routes_ws` surface actions (mode="ask") to the **legacy** path (never bypass).
- **I3 (Important) — keep intent classification** for the `_bump_perception_for_sources` signal (the
  whole `_process_core` prefix is unchanged in P1, so this holds by construction).
- **M7 — keep `log_interaction`** (Persona learning reads `InteractionLog.plan_summary`); the prefix
  is unchanged, so retained.

### 3.4 permission_mode field
`permission_mode: str` — new. P1 is backend-only (no frontend yet; the frontend permission UI is P3).
Default `"auto"` (a non-bypass value → legacy in P1; becomes the safe gated default in P2). Only
`"bypass"` activates the single-lead path in P1. Source: per-turn API param + a per-workspace default
(setting); resolved in `_process_core`. Do NOT reuse the `mode` slot.

### 3.5 Reused vs built
- **Reused (5a):** `build_chat_lead`, `derive_lead_scope` (plan-union), `stream_deep_lead`,
  `effective_chat_runtime`, `LEAD_PROMPT`, always-on PRESENTER_VOICE (surface contract).
- **Built (P1):** the `_process_core` bypass branch (with output re-homing + system.* + fixes);
  `permission_mode` field + resolution; `AgentInvoker.build_chat_lead(steps, workspace_id)` wrapper
  (uses `self._agents`/cheap_mode/db_factory); the write_lock-fail-closed posture for the bypass build;
  add `"lead"` to `CONTEXT_ENRICHED_AGENTS`.

### 3.6 P1 decomposition (subagent-driven TDD)
- **P1.1** `permission_mode` field (settings + conftest pin) + resolution in `_process_core` (exact-
  equality, non-default, independent). Tests: schedule_dispatch/routes_ws pinned to legacy.
- **P1.2** `AgentInvoker.build_chat_lead(steps, workspace_id)` + `stream_deep_lead(tools=None →
  internal resolve)` refinement + write_lock fail-closed posture for the bypass build. Add `"lead"` to
  `CONTEXT_ENRICHED_AGENTS`.
- **P1.3** the `_process_core` bypass branch: system.* deterministic + build lead + assemble context +
  stream + re-home Presentation/surface/learner. SECURITY-CRITICAL review (ungated write path).
- **P1.4** reply-coverage tests (single-read, knowledge-only, system.set_goal, read+write, pure-write,
  fast reason/respond) + legacy byte-neutral + mode-guard + capability-scope-denial.
Full gate + 2-stage review (spec+quality) + security-reviewer at P1.3. NO migration (unless the
per-workspace default needs a column — prefer settings default in P1).

---

## 4. P2 — synchronous approval subsystem (sketch; own design pass + spike)
The chat pause→approve→resume round trip is **complete-but-UNWIRED** (verified): `resume_deep_turn`
has no endpoint; the frontend has no `approval_needed` consumer; `/approve` re-executes via a NEW
autonomous run instead of resuming the checkpoint. P2 builds: **SPIKE** the interrupt→approve→resume
round trip for a chat lead + real model + SSE reconnect FIRST; then a `permission_gate` middleware
(mode×risk → interrupt; SEPARATE from the autonomous `trust_gate`; reuses `_assess_risk` +
`get_or_assess_risk`); a resume endpoint/WS action → `resume_deep_turn`; the SSE pause/resume protocol
(turn stream ends on `approval_needed` → user approves → new stream continues from checkpoint);
frontend `approval_needed` consumer + `InlineApprovalCard`-in-chat; A-7 `decision_type`
(approved/modified) on the chat gate. `auto` predicate: interrupt iff
`not reversible OR blast_radius ∈ {external_single,external_multiple,public} OR risk_level=="high"`.
Heavy adversarial pressure-test before build.

## 5. P2.5 — drop the Planner (planless)
Once P2's gate bounds writes at action time and `system.*` are promoted to internal tools
(catalog+schemas+intelligence_server), the Planner's chat jobs (b)+(c) are re-homed → drop the Planner
call from the chat path; the lead self-plans via `write_todos`; scope becomes the coarse
connected-connector allow-list (NOT whole-catalog — scope to genuinely-authenticated connectors).

## 6. P3 — UX + retire `mode`
Surface the lead's `write_todos` (adapter reads the `todos` channel + a Claude-Code-style todo list);
retire `mode` (ask/plan/execute) → `permission_mode` across API + frontend (command-store, composer,
launcher, api.ts, chat-panel). CLAUDE.md two-paths invariant rewrite at merge (R1).

## 7. Invariant change (recorded at merge)
New: the chat path is gated **at action time** by the user's `permission_mode` (P2+); P1 `bypass` is
an explicit opt-in that keeps today's ungated-but-plan-bounded posture with single-lead execution.
The autonomous path remains gated by TrustEngine async approval. This supersedes the old "chat is
ungated by design; never add a gate" invariant.

## 8. Accepted risks
- P1 `bypass` = single-lead ungated with plan-union scope (== today's chat safety posture, just
  single-lead). No regression vs today.
- `bypass` in P2+ = broad write authority within connected connectors, no confirmations (the explicit
  opt-in contract; perception-injection → write is possible in bypass — inherent to "no gate"; not
  default). Revisit provenance-taint at P2.5.
