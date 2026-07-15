# P2 — Synchronous Approval Subsystem (chat `ask`/`auto`) — DESIGN

> Chat Permission Model, Phase 2. Spec: `docs/superpowers/specs/2026-07-13-chat-permission-model.md` §4.
> Spike CONFIRMED (`spikes/deep_permission_gate/`, commit `0b24402`). This is the design pass
> (own pressure-test before build). Dormant behind flags, legacy byte-neutral, NO migration.
> On `rebuild/first-principles`. **STATUS: APPROVED FOR BUILD (user go-ahead 2026-07-16). Pressure-
> tested (2 adversarial critics, both SHIP-WITH-FIXES; grounding verified accurate; all fixes folded
> §10). §9-A resolved = JSONB `allow_bypass` + fallback-to-auto. Building P2.1-first, dormant/byte-
> neutral, no migration, no flip.**

## 0. What the spike settled (so the design can lean on it)
- A `permission_gate` that interrupts on **mode × risk** (auth-source-INDEPENDENT, unlike
  `trust_gate` which short-circuits `DIRECT_USER_REQUEST`) **composes** with the fresh-rebuild
  resume machinery on the durable `AsyncPostgresSaver` substrate.
- The paused write fires **exactly once on approve / is skipped on reject WITHOUT a ledger**
  (interrupt() pauses BEFORE the tool node; replay re-runs only the idempotent pre-interrupt
  gate body). Spec §3.3 C-SEC2's ledger-free posture EXTENDS to the pausing ask/auto turn.
- Real sonnet emits a terminal reply on both branches; auto+safe passes through un-paused.
- Design inputs: **ask skips the risk classifier** (assess only in auto); the reject ToolMessage
  must carry a **quotable reason** (the model confabulated on a bare `{"rejected":true}`).

## 1. Grounded current-state (all re-verified by name @ `996d4ff`)
- **Chat is one-shot SSE-over-fetch.** `POST /v1/jarvis/chat` → `StreamingResponse`
  (`routes_chat.py:137`, `:389`); each call mints a fresh `thread_id`; **no continue/resume
  path**. A paused deep turn emits `approval_needed` (`stream_adapter.py:210`) and the stream
  simply ends. Frontend consumes via `fetch`+`ReadableStream` (`api.ts:219-276`), switch in
  `chat-panel.tsx:269-438` — **no `approval_needed` case**.
- **The re-entry primitive exists but is UNWIRED.** `AgentInvoker.resume_deep_turn`
  (`agent_invoker.py:955`) `Command(resume=)`s a paused turn from the Approval's stored
  `thread_id`/`agent_name`/`context_block`. Every caller is a test.
- **The `/approve` deep-gate branch does the WRONG thing for chat.** `routes_approvals.py:286-351`
  detects a deep-gate approval (`run_id=None` + `artifact_refs.tool_name`) and **spins a NEW
  autonomous `Plan`+`TaskRun` that re-executes just the tool** — it IGNORES `thread_id`/
  `agent_name`/`context_block` and never resumes the checkpoint. The WS `approve` action
  (`routes_ws.py:254-313`) bridges straight into this same handler.
- **The chat single-lead branch** (`chat_processor.py:523-527`) runs only on
  `permission_mode=="bypass"` + `deep_single_lead` + `runtime=="deep"`, ungated
  (`DIRECT_USER_REQUEST`; trust_gate dormant). It re-homes `Presentation` from the deep stream's
  `agent_done` (`:576-584`). `permission_mode` is a per-turn `Literal["auto","ask","bypass"]`
  param, default `"auto"`, INDEPENDENT of `mode` (`routes_chat.py:53`).
- **The synthetic lead is NOT registered in `self._agents`** and its `capability_scope` is
  **plan-derived per turn** (`lead_builder.derive_lead_scope` = union of each step's authority;
  `_make_lead(scope)`). The gate persists `{thread_id, tool_call_id, capability, reversible,
  blast_radius, tool_name, agent_name, context_block}` — **no scope/plan** (`trust_gate.py:198-209`).
- **A-7 is half-present.** `approve_action` stamps `artifact_refs["decision_type"]` =
  `"modified" if reason else "approved"` (`routes_approvals.py:187`). A chat resume won't flow
  through `approve_action`, so the resume endpoint must stamp it itself.
- **No per-workspace settings table.** `Workspace.settings` JSONB (`users.py:42`) + `Workspace.plan`
  (`free/pro/enterprise`) exist; `OrgAllowlist` (`org_allowlist.py`) is the closest per-workspace
  entitlement precedent. Per-USER policy lives in `user_settings`.
- **`InlineApprovalCard`** (`inline-approval.tsx`) takes an `ApprovalContext`, submits via the WS
  action store (`sendAction("approve"|"reject"|"edit_before_approve", {id, reason})`), and renders
  ONLY in execution surfaces + history — never in chat.

## 2. Architecture (the 7 pieces + how they compose)
```
chat turn (ask/auto) ── _process_core single-lead branch ── stream_deep_lead
                                                              │ _build_deep_agent_for(permission_mode=…)
                                                              │   installs permission_gate (SEPARATE from trust_gate)
   write tool call → permission_gate: mode×risk → interrupt()  ← persists Approval{+lead_scope,+mode,+reason-slot}
                                                              │
   approval_needed frame ── _process_core yields ApprovalRequired (new CoreEvent) ── SSE approval_needed
        (stream ENDS; shared tail SKIPPED — no reply yet)
                                                              │
frontend: approval_needed case → InlineApprovalCard-in-chat → approve/reject
                                                              │
   POST /v1/jarvis/chat/resume {approval_id, decision, reason?} ── StreamingResponse
        → resume_deep_lead: rebuild lead FROM persisted scope + permission_gate
        → Command(resume=decision) → write once / skip → terminal reply
        → stamps decision_type (A-7); runs the shared tail (surface/learner) on the reply
```

### 2.1 permission_gate middleware — `src/deep_runtime/middleware/permission_gate.py` (NEW)
A `@wrap_tool_call` middleware, **SEPARATE** from `trust_gate` (spec: do not disturb the
autonomous `trust_gate.evaluate` path). Auth-source-INDEPENDENT — it never calls
`is_gated_source`. Logic:
1. skip `DEEPAGENTS_BUILTIN_NAMES`; resolve capability via the shared resolver (fail-CLOSED like
   trust_gate); reads never gate.
2. **replay-safe short-circuit (CF-2 analog):** on a resume replay the Approval already exists →
   read its persisted decision and go STRAIGHT to `interrupt()` (skip re-assessment), exactly as
   trust_gate does.
3. first pass:
   - `bypass` → never installed here (see 2.2), so N/A.
   - `ask` → interrupt EVERY write; **no risk call** (spike-proven efficiency).
   - `auto` → `assess_risk(capability, args)` (reuse the `_assess_risk` closure already in
     `_build_deep_agent_for`, which wraps `get_or_assess_risk`, 24h Redis-cached → **stable across
     replay**); interrupt iff `not reversible OR blast_radius ∈ {external_single,external_multiple,
     public} OR risk_level=="high"`; else execute.
   - on interrupt: **idempotently persist an Approval** (get-or-create on
     `(workspace_id, thread_id, tool_call_id)`, replay-safe) with `artifact_refs` carrying
     **the lead's `capability_scope`** (NEW — for the resume rebuild), `permission_mode`,
     `reversible`/`blast_radius` (for the card), `context_block`, and a `chat: true` marker.
     Then `interrupt({approval_id, capability, risk_level, thread_id})`.
4. on resume: `approve` → `handler(request)`; `reject` → a rejection `ToolMessage` carrying a
   **quotable reason** (`decision_reason` from the Approval, default "the action was declined")
   so the lead explains the rejection accurately (spike finding).

### 2.2 install seam — `_build_deep_agent_for(..., permission_mode: str | None = None)`
ADDITIVE param, default `None` → **no permission gate installed → byte-identical for every existing
caller**. Installed iff `permission_mode in ("ask","auto")` (bypass keeps the P1 ungated posture —
no gate). Placement: same slot as the (chat-dormant) `trust_gate` — OUTER of `write_lock`. On chat
`trust_gate` stays installed but dormant (`DIRECT_USER_REQUEST`); the permission_gate is the active
gate. `stream_deep_lead` threads `permission_mode` through. The gate needs the lead's scope → pass
`agent.capability_scope` into the gate factory for persistence.

### 2.3 chat routing — `_process_core`
- **Effective-mode resolution (entitlement §2.7 + durability + batch guard):**
  1. `bypass` non-entitled → `auto` (§2.7).
  2. **[Sec-I3] durable-checkpointer precondition:** if `effective_mode in ("ask","auto")` and the
     deep checkpointer is absent/degraded (`app.py` sets `deep_checkpointer=None` +
     `deep_checkpointer_degraded=True` when `AsyncPostgresSaver` init fails; `_build_deep_agent_for`
     silently falls back to a per-build `MemorySaver` at `agent_invoker.py:570`) → **fall back to
     legacy**. A pause spans two HTTP requests; a non-durable/per-build saver's checkpoint is
     unreachable from `/chat/resume` → resume finds nothing → undefined re-exec. Durable + the
     status-consume interlock (§2.6) are what make "exactly-once without a ledger" hold.
  3. **[Corr-I3] batch guard:** the batch entry `process_message` (no synchronous user —
     `custom_agent_task` mode=execute, `routes_ws` surface actions) must NEVER honor `ask`/`auto`
     (nobody can approve → orphaned checkpoint; the `ApprovalRequired` event is silently dropped by
     the batch fold `case _: pass`). The widened branch is enabled ONLY on the streaming entry
     (`process_message_events`/`_stream`); batch pins `effective_mode`→legacy. Spec §3.3 C-SEC3
     pinned these by `mode`; P2 keys on `permission_mode`, so this is the P2 extension of that pin.
- **Widen the single-lead branch:** `if effective_mode in ("bypass","ask","auto") and
  deep_single_lead and runtime=="deep":` → single-lead. `stream_deep_lead` gets `effective_mode`;
  the gate installs for ask/auto. `bypass` unchanged (P1). **Default `auto` + flags off → legacy**
  (byte-neutral; the `deep_single_lead`/runtime guards keep P2 dormant exactly like P1).
- **On `approval_needed` frame:** set an explicit `paused=True`, yield `ApprovalRequired` (new
  CoreEvent) instead of `Presentation`, and **early-return BEFORE the shared tail** (`chat_processor.py:
  709-780` runs unconditionally today — [M1] needs a real seam, not just "skip"). The tail runs on
  the RESUME's terminal reply (§2.6). The typed `ApprovalRequired` **replaces** the raw
  `approval_needed` passthrough (`agent_event_from_sse`→`AgentStreamEvent`) so the frame isn't
  emitted twice [I-note].

### 2.4 the pause event — `core_events.py`
New frozen `ApprovalRequired(approval_id, capability, risk_level, reversible, blast_radius,
thread_id)` + `core_event_to_sse` → `approval_needed` SSE frame + the `process_message` batch fold.

### 2.5 resume seam — a 3-LAYER stack (the pressure-test reshaped this)
**The load-bearing new work.** `resume_deep_turn` cannot rebuild the lead (`self._agents.get("lead")`
→ None; scope is plan-derived) AND it hardcodes `AUTONOMOUS` auth (would double-gate — [Corr-C2]).
Three layers:

- **(invoker) `AgentInvoker.resume_deep_lead(approval_id, decision, reason, user_id, workspace_id)`**
  — a CHAT-specific resume, sibling to `stream_deep_lead`. Shares the 3 tenant guards with
  `resume_deep_turn` via an EXTRACTED helper (load approval, `approval.workspace_id==workspace_id`,
  `workspace_of_thread_id(thread_id)==workspace_id`, `status=="pending"`) so no guard is dropped
  [Sec-N5]. Then: rebuild the lead from `_make_lead(frozenset(artifact_refs["lead_scope"]))` —
  **graceful fail-CLOSED deny if `lead_scope` is missing** (no `KeyError`/500, no whole-catalog
  fallback — `capability_scope` already denies empty scope) [Sec-N4]; auth = **`DIRECT_USER_REQUEST`**
  (trust_gate dormant); **ALWAYS install the permission_gate FAIL-CLOSED** — do NOT key install on
  the persisted mode; a pending chat Approval proves the first pass interrupted, so resume must
  re-interrupt. Missing/invalid persisted `permission_mode` → treat as `ask` [Sec-C1]. Flip status +
  stamp `decision_type` (A-7) + `Command(resume=decision)` → re-stream frames.
- **(processor) `ChatProcessor.resume_message_events(...)`** — drives `resume_deep_lead`, and on
  `agent_done` **synthesizes `Presentation(strip_surface_blocks(text))`** and runs the FULL shared
  tail (surface extraction + `push_presenter_surface` + learner + `RunCompleted`) — WITHOUT this the
  approved write fires but the reply is never persisted, no surface, nothing learned = the C-CORR2
  failure P1 fixed for the initial turn, un-fixed on resume [Corr-C1]. This is the missing owner the
  bare invoker method cannot be.
- **(route) `POST /v1/jarvis/chat/resume`** — §2.6.

### 2.6 SSE resume endpoint — `POST /v1/jarvis/chat/resume`
New route → `StreamingResponse` (same SSE vocabulary) → `ChatProcessor.resume_message_events` →
re-streams the continuation AND emits the synthesized `Presentation`/`response` so `routes_chat`
persists the reply. Auth: `get_current_user_id` + `get_current_workspace_id`.
**Guard BOTH `approve_action` AND `reject_action` at the TOP** (before any status mutation / trust
feedback) against chat approvals (`artifact_refs.get("chat")`) [Sec-I2]: a chat approval hitting the
REST/WS decision handlers must be short-circuited (directed to `/chat/resume`) — otherwise it flips
`status` (→ `/chat/resume` refuses `status!=pending` → stranded empty bubble) and `reject_action`
`record_approval_decision` **pollutes the autonomous `TrustState`** with a chat decision (chat is not
trust-graduated). The old `/approve` deep-gate branch (`routes_approvals.py:286`, which reads only
`tool_name`/`tool_params` — chat has none → would execute with EMPTY args [M4]) is thus never reached
for chat approvals.

### 2.7 bypass entitlement gate (P2 prerequisite)
Per-workspace `allow_bypass` in `Workspace.settings` JSONB (default absent=False), resolved in
`_process_core`. Non-entitled `bypass` → falls back to `auto` (safe gated default) with a logged
warning — **not a 403** (fail-safe, dormant-friendly). See fork §9-A (storage + fallback).

### 2.8 frontend — `chat-panel.tsx`, `api.ts`, `inline-approval.tsx`
- Add `approval_needed` case to the chat SSE switch → attach `ApprovalContext` to the message →
  render `InlineApprovalCard`-in-chat (new render site; the component already exists).
- The in-chat card's approve/reject call a **new `streamResume(approval_id, decision, reason)`**
  (mirrors `streamChat`) → opens a NEW fetch-SSE stream to `/chat/resume`. **[Corr-I2] single-bubble
  continuity:** `streamResume` must REUSE the pre-pause `assistantId` and SUPPRESS the resume stream's
  fresh `message_id` (else a split bubble: pre-pause text in A, continuation in B); and its `onEvent`
  must carry the `approval_needed` case itself so a CHAINED pause (a 2nd write in the resumed turn)
  re-shows a card (recursion). **NOT** the WS action store (which spins the wrong autonomous run).
- The permission-mode selector UI is **P3** (spec §6). P2 does NOT add `permission_mode` to
  `streamChat` (`api.ts:226-228` sends only `mode`) — so the shipped UI cannot trigger `ask`/`auto`
  in P2; the approval consumer is dormant/unreachable via real UI until P3 (QA note — exercise via a
  direct API call with the flags on).

## 3. Dormancy / byte-neutrality
Gated behind `deep_single_lead` + `runtime=="deep"` (same as P1) + `effective_mode in ("ask","auto")`.
Default `permission_mode="auto"` but flags off → legacy per-step path. New CoreEvent, endpoint,
frontend case are additive + dormant. **NO migration** (`Workspace.settings`/`artifact_refs` are
JSONB; the checkpoint tables already exist). Legacy path byte-identical.

## 4. Decomposition (subagent-driven TDD; single-owner + SYNCHRONOUS for hot files)
- **P2.1** permission_gate middleware + `_build_deep_agent_for(permission_mode=…)` install.
  **SECURITY-CRITICAL** (the gate IS the chat safety boundary). Tests: ask-interrupts-every-write,
  auto-interrupts-iff-risky, auto-safe-passthrough, bypass-not-installed, reads-never-gate,
  fail-closed-on-lookup-error, **replay-safe idempotent approval persist** — mirror
  `trust_gate._decide_and_maybe_persist` find-existing-first + IntegrityError re-select; the spike
  used a synthetic id so double-persist-on-replay is UNPROVEN → mandatory test [M3] (incl. lead_scope
  + reason + `chat:true`).
- **P2.2a** (invoker) `resume_deep_lead`: extract the shared tenant-guard helper (shared with
  `resume_deep_turn` [Sec-N5]); rebuild lead from persisted scope (graceful deny if missing [Sec-N4]);
  `DIRECT_USER_REQUEST` + ALWAYS-install permission_gate fail-closed (mode default `ask` [Sec-C1]);
  stamp `decision_type`. **SECURITY-CRITICAL.** Model on `test_deep_gate_durable_resume_db`.
- **P2.2b** (processor) `resume_message_events`: drive `resume_deep_lead`, synthesize
  `Presentation(strip_surface_blocks)` + run the FULL shared tail (surface + learner + RunCompleted)
  [Corr-C1]. Tests: reply-persisted-on-resume, surface-built-on-resume, learner-spawned-on-resume.
- **P2.3** `_process_core`: effective-mode resolution (§2.7 entitlement + **durable-checkpointer
  precondition [Sec-I3]** + **batch-path→legacy pin [Corr-I3]**) + widen branch + explicit
  `paused` seam that yields `ApprovalRequired` and early-returns BEFORE the tail [M1]; new CoreEvent +
  SSE mapping + batch fold, `ApprovalRequired` REPLACES the raw passthrough [I-note]. Tests:
  pause-yields-ApprovalRequired, legacy byte-neutral, tail-skipped-on-pause, batch-never-gated,
  degraded-checkpointer→legacy.
- **P2.4** SSE resume endpoint `POST /v1/jarvis/chat/resume` (drives `resume_message_events`) + guard
  BOTH `approve_action` AND `reject_action` at the TOP against chat approvals [Sec-I2].
  **SECURITY-CRITICAL** (auth + the decision-endpoint short-circuit + no trust pollution).
- **P2.5** bypass entitlement gate (`Workspace.settings["allow_bypass"]`) — the P2 prerequisite.
- **P2.6** frontend: `approval_needed` case + `InlineApprovalCard`-in-chat + `streamResume`
  (assistantId reuse + chained-pause recursion [Corr-I2]).
- **P2.7** coverage: reply-either-way, reject-reason-quotable, legacy byte-neutral, capability-scope
  denial on resume, full gate + 2-stage review (spec+quality) + security-reviewer at P2.1/2.2a/2.4.

**Hardest seams — PAUSE for user before building:** P2.1 (permission_gate) + P2.2/P2.4 (resume/SSE
protocol). Design-first-investigation on those two before implementation.

## 5. Invariants preserved
- `permission_gate` is SEPARATE from `trust_gate`; the autonomous evaluate path is untouched.
- Autonomous path unchanged (Planner→DAG→TrustEngine async approval).
- `permission_mode` stays INDEPENDENT of `mode`; exact-equality; non-bypass default.
- Fail-CLOSED: capability lookup error → block; risk unavailable → high → interrupt (auto).
- Exactly-once via interrupt-before-tool (no ledger); write_lock still serializes the executed write.

## 6. Accepted risks (revisit at chat-flip / P2.5)
- `bypass` in P2+ = broad write authority within connected connectors, no confirmations — now gated
  by the workspace `allow_bypass` entitlement (§2.7). Perception-injection→write in bypass remains
  inherent to "no gate" (not default; provenance-taint revisited at P2.5).
- `auto` relies on `get_or_assess_risk` accuracy + its cache for replay-stability. Risk outage →
  fail-closed high → interrupt (never silent execute).

## 7. What P2 does NOT do (deferred)
- Permission-mode selector UI + retire `mode` → P3. Per-workspace default `permission_mode` → P3.
- Drop the Planner (planless) + `system.*`→tools → P2.5.
- `resolve_plan_routing` perf on the bypass path (M1); error-path fallback reply (M2) → later-P.

## 8. Open forks for user review (§9)
See §9 — decided-with-recommendation, surfaced for confirmation before build.

## 9. FORKS
- **§9-A bypass entitlement (storage + fallback) — RESOLVED (user go-ahead 2026-07-16).**
  `Workspace.settings["allow_bypass"]` JSONB (no migration) + non-entitled bypass **falls back to
  `auto`** (fail-safe) with a warning. (ALT — typed `OrgAllowlist` row + hard 403 — declined:
  heavier + user-hostile for a dormant opt-in; a silent downgrade to gated `auto` is *more* secure
  than a dead-end 403.) Built at P2.5.
- **§9-B resume method shape — RESOLVED by pressure-test.** Neither pure option: a chat resume must
  use `DIRECT_USER_REQUEST` + always-install permission_gate ([Corr-C2]+[Sec-C1]), so generalizing
  `resume_deep_turn` verbatim (AUTONOMOUS) is WRONG. Resolution = a **separate `resume_deep_lead`**
  that SHARES the tenant-guard helper with `resume_deep_turn` ([Sec-N5]) — focused body, no dropped
  guard. See §2.5.
- **§9-C `/approve` for chat approvals — RESOLVED (sharpened).** Guard is necessary AND must be at the
  TOP of BOTH `approve_action` and `reject_action` (before status mutation + trust feedback), not just
  the autonomous-run branch — else the decision consumes `pending` (stranded turn) and reject pollutes
  the autonomous `TrustState` ([Sec-I2]). See §2.6.
- **§9-D side-effect parity on chat resume — RESOLVED.** Resume stamps `decision_type` + emits the
  `approval_resolved` audit/event, but does NOT run autonomous trust-feedback/graduation (chat is not
  trust-graduated) — confirmed by [Sec-I2].

## 10. Pressure-test log (2 adversarial critics, both SHIP-WITH-FIXES; grounding verified accurate)
Both critics independently re-verified §1's current-state claims against code — **no false factual
claims found**. All fixes are ADDITIVE (no redesign). Folded above:
- **[Corr-C1 / Critical]** tail-on-resume owned by nobody → reply/surface/learning LOST → §2.5 P2.2b
  `resume_message_events` + §4.
- **[Sec-C1 / Critical]** resume gate-composition unspecified → a REJECTED write could fire ungated →
  §2.5 always-install permission_gate FAIL-CLOSED, mode default `ask`, pinned `DIRECT_USER_REQUEST`.
- **[Corr-C2 / Critical]** generalizing `resume_deep_turn` (AUTONOMOUS) double-gates → §2.5 separate
  `resume_deep_lead`, §9-B RESOLVED.
- **[Sec-I2 / Important]** stray decision consumes `pending` + pollutes autonomous TrustState → §2.6
  guard BOTH decision endpoints at the TOP.
- **[Sec-I3 / Important]** degraded→MemorySaver makes a pause unresumable → §2.3 durable-checkpointer
  precondition (else legacy).
- **[Corr-I3 / Important]** batch path silently enters the gate (no user to approve) → §2.3 batch pins
  to legacy.
- **[Corr-I2 / Important]** frontend split-bubble + chained-pause recursion + `permission_mode` not
  sent → §2.8.
- **[I-note / Important]** catch-all folds silently DROP a missing `ApprovalRequired` mapping →
  checkpoint hangs → §2.3 both mappings + replace raw passthrough.
- **[M1]** tail runs unconditionally → explicit `paused` early-return seam (§2.3).
- **[M2 — confirmation]** scope-only resume is SUFFICIENT (set is order-invariant; `system.*` ran
  pre-pause; context via CF-1, capped 8000 chars — caveat: long context truncates on resume).
- **[M3 — obligation]** exactly-once TOOL proven, but approval-persist idempotency UNPROVEN by the
  spike (synthetic id) → mandatory test in P2.1.
- **[Sec-N4/N5, M4]** graceful deny on missing scope; shared guard helper; `/approve` guard is
  load-bearing (chat has no `tool_params`).

**Net:** the 7-piece architecture holds; the resume path became a 3-layer stack; two fail-closed
rules added (always-install gate on resume; durable-checkpointer precondition). One live fork for the
user: §9-A.
