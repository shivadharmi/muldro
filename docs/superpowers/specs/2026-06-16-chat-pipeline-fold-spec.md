# Spec: Fold `process_message` / `process_message_stream` into one core (ORCH-P1-1, full)

**Status:** Draft — for approval or deferral. **Not** started. No code changes implied by this doc.
**Author context:** Follow-up to M5 / ORCH-P1-1 (safe-extraction scope, landed in commit `7b174a1`).
The structural extraction of the byte-identical blocks into `src/orchestrator/chat_pipeline.py`
is done. This spec covers the part that was deferred because it is **behavior-changing**: collapsing
the two methods' *divergent* logic into a single control flow.

**Branch rule that gates this work:** `docs/engineering-standards.md` §5 — *structure and behavior
never change in the same commit; characterization tests before risky structural change.* This spec
exists because the fold cannot be done as a structure-only commit: the two methods have **drifted**,
and reconciling drift is a behavior change requiring sign-off.

---

## 1. Why this exists

`JarvisOrchestrator` exposes two public entry points that run the same
intent → plan → route → execute → present → surface → learn **sequence**:

- `process_message` — **batch**. Accumulates a `result` dict and returns it.
- `process_message_stream` — **streaming**. Yields SSE-shaped event dicts.

CLAUDE.md already records the failure mode this creates: *"Do not add handlers only to
`process_message` — always wire into BOTH."* The safe-extraction pass removed the byte-identical
duplication; what remains is **divergent** duplication — the two methods do subtly different things
at six points. Every new feature still has to be implemented twice and kept in sync by hand, and the
six existing divergences are evidence that sync-by-hand has already failed.

The target end state (engineering-standards §2): *"a batch result folded from the streaming
pipeline. Two public methods must not own two control flows."*

```
_process_core(...) -> AsyncGenerator[CoreEvent]          # single control flow, typed events
        │
        ├── process_message_stream  = translate CoreEvent → SSE dict, yield      (pass-through adapter)
        └── process_message         = drive core to exhaustion, fold → result dict  (accumulating adapter)
```

## 2. Who depends on each path (do not break these)

| Surface / trigger | Method | Consumes |
|---|---|---|
| Web chat textbox — `routes_chat.py` | `process_message_stream` | the SSE event stream |
| ~~Telegram bot — `interface/telegram.py:231`~~ **(being removed — see §11)** | `process_message` | n/a after removal |
| Scheduler/background — `scheduler/schedule_dispatch.py` (meeting_prep ×3) | `process_message` | nothing (fire-and-forget side effects) |
| WS surface actions (default dispatch) — `routes_ws.py:316` via `_handle_orchestrator_action` | `process_message` | the whole `result` dict, returned verbatim in `action_result` |
| WS execute-insight — `routes_ws.py:450` | `process_message` | the whole `result` dict |

**Both paths are load-bearing.** Streaming serves the one surface that can render a token stream;
batch serves every surface that gets a single answer (Telegram, background jobs, WS action
callbacks). Neither can be deleted. `routes_ws` returns the batch `result` dict **verbatim** to
clients — so the batch dict's *key set* is a public contract, not an internal detail.

## 3. Current contracts the golden tests must freeze

### 3a. Batch `result` dict (success)
`trace_id`, `run_id` (always `None` on this path today), `interaction_id`, `plan` (dict),
`summary`, then per executed step one of `system_{capability}` / `error_{step_id}` /
`step_{idx}_{capability}`, plus `user_actions` (list), `presentation` (surface blocks stripped),
and `surface_id` (only when a surface was pushed).

### 3b. Batch `result` dict (failure)
`trace_id`, `decision="error"`, `summary` (safe message), `code`, `correlation_id`.

### 3c. Stream SSE events (in order)
`trace` → `intent` → `plan` → per step the agent-loop events from `_call_agent_stream`
(`agent_start`, `thinking`, `tool_call`, `text_delta`, `agent_done`) and/or `step_error` /
`plan_ready` → `user_actions` (if any) → `response` → `done`. Error path yields `error` /
`safe_error_event`.

> The golden layer must capture 3a–3c for a representative plan matrix (see §6) **before any
> reconciliation commit**, on today's code, and keep them green through every step except where a
> decision in §5 explicitly authorizes a change.

## 4. Target design

- **`CoreEvent`** — a frozen Pydantic **discriminated union** (engineering-standards §1: *contracts at
  every boundary; discriminated unions over `event["type"]` string-matching*). Replaces the bare-dict
  `evt.get("event")` sniffing in `routes_chat.py` and the ad-hoc `result[...]` keys. The vocabulary
  must be a **superset** of both shells' needs, e.g.:
  - lifecycle: `TraceStarted`, `IntentClassified`, `PlanReady`, `RunCompleted`, `RunFailed`
  - execution: `StepStarted`, `StepResult{key, output}`, `StepError`, `AgentDelta` (token stream),
    `ToolCall`, `Thinking`
  - terminal: `Presentation{text, raw}`, `UserActions{steps}`, `SurfacePushed{surface_id}`,
    `InteractionLogged{id}`
- **`process_message_stream`** — translate each `CoreEvent` to its SSE dict shape and `yield`.
  Token-level events (`AgentDelta`, `Thinking`, `ToolCall`) pass through; lifecycle events map to the
  existing SSE names.
- **`process_message`** — consume the core to exhaustion, folding events into the `result` dict:
  `InteractionLogged` → `interaction_id`; `StepResult` → `step_{idx}_{cap}` / `system_{cap}`;
  `Presentation` → `presentation`; `UserActions` → `user_actions`; `SurfacePushed` → `surface_id`;
  drop the token-level deltas. This is the only way to reproduce the batch dict — the core must *emit*
  every field the dict carries.

## 5. Drift decision table (each row = one behavior commit + sign-off)

For each divergence: current behavior on both sides, recommended canonical behavior, whether it looks
**intentional** or **accidental drift**, and the risk. **These are the decisions the reviewer/owner
must sign off on** — they are not mechanical.

| # | Divergence | Batch today | Stream today | Recommendation | Intentional? | Risk |
|---|---|---|---|---|---|---|
| 1 | **Presenter prompt** | `"Format this for the user ({surface})… Plan: {json}"` then `Analysis: {plan_text}` | `"Respond to the user ({surface})… Intent: {intent}"` then `Plan: {json}\nAnalysis:` | **PRESERVE both** — thread a `prompt_style` (conversational vs. structured-one-shot) into the core, selected by adapter | **INTENTIONAL (confirmed by owner 2026-06-16):** stream = conversational (live chat); batch = structured one-shot (WS surface-action callbacks + background scheduler runs) | Low — preserving current behavior, not changing it. The `surface == "telegram"` length-hint branch (`build_telegram_hint`) becomes **dead** once Telegram is removed (§11) and should be deleted then |
| 2 | **Agent prior-context** | injects from the whole `result` dict (incl. `trace_id`, `interaction_id`, `plan`, `summary`, system outputs) | injects only `step_outputs` (prior agent text) | **Adopt the stream's narrow `step_outputs`** — **CONFIRMED ACCIDENTAL DRIFT (owner 2026-06-16)**: archaeology (`5b2aa70`) shows batch reused `result` as both output contract *and* prior-step scratchpad, so metadata leaked as a side effect — never designed. The fold's separate accumulator removes the leak structurally | **Drift / latent bug** — batch leaks plan/trace metadata into downstream agent prompts | Med — changes agent inputs; may shift tool choices |
| 3 | **`direct_answer` pick** | suffix-match `k.endswith(f"_{read_step.capability}")` | `next(iter(step_outputs.values()))` | Use the explicit suffix-match (deterministic for multi-output) | Drift | Low — converges for the single-read case both guard on |
| 4 | **Runtime events** | `plan_generated` via `_publish_event`; `run_completed` **awaited** | `plan_created` via `_fire_event` (background) + SSE `plan` | **Batch converges onto canonical `plan_created` fired background via `_fire_event`; drop orphan `plan_generated` — CONFIRMED STALE-LEGACY DRIFT (owner 2026-06-16)**: archaeology shows `plan_generated` (origin `c0883e9`, agent-stream bus) predates the durable runtime-event vocabulary and is consumed by **nothing** (not whitelisted in `routes_realtime`, not metered, not durable); `plan_created` is the canonical event (`runtime_events.py`, `routes_realtime.py` whitelist, `metrics_service.record_plan_created`). Batch *gains* the durable record + metrics it currently lacks | **Drift** — `plan_generated` vs `plan_created` is almost certainly an accident | Med — telemetry/consumers keyed on event names |
| 5 | **Output contract** | `result` dict (returned verbatim by `routes_ws`) | SSE stream | Core emits a superset; batch adapter rebuilds the **exact** current dict (golden-pinned); SSE adapter unchanged | N/A (structural) | High — `routes_ws`/Telegram break if a key changes |
| 6 | **`mode` param** | none | `ask`/`execute`/`plan` (plan-mode skips, `requires_user_input`) | **Core takes `mode`; batch adapter default is `mode="plan"` (CHANGED from `"ask"` by owner 2026-06-16)** — safe-by-default for the non-interactive batch path (risky writes surfaced for approval, never silently auto-run, closing the latent ungated-background gap). **Per-caller override map:** WS default-dispatch → `ask`, WS execute_insight → `ask` (interactive: the user's click authorizes), scheduler meeting_prep / wake_agent → inherit `plan` (read-heavy, unaffected), scheduler **custom_agent_task → `execute`** (pre-authorized automation must keep running; the clean long-term fix is routing scheduler writes through GraphExecutor+TrustEngine — out of scope) | Intentional (stream-only feature) | Low — additive for batch; **Med for the `plan` default** since it changes batch behavior for risky-step plans (golden-pinned, updated in the mode commit) |

## 6. Execution plan (phased; each phase independently revertable)

1. **Golden characterization (no behavior change).** Drive both methods with mocked `_call_agent` /
   `_call_agent_stream` across a plan matrix: single read-only step; multi-step read→reason; a
   write step with a user-action step; a `system.*` step; an error/raise; (stream-only) each `mode`.
   Snapshot the batch `result` dict (3a/3b) and the SSE event order (3c). Land green on current code.
2. **Reconcile drift #2 and #4, one behavior commit each**, *while the two methods still exist*, so the
   golden diff shows exactly what each decision changed. Each commit references this spec's row and
   carries the owner's sign-off. Rows #1 (prompt) and #6 (mode) resolve to **preserve** and are
   threaded later as `prompt_style`/`mode` params (no Phase-2 commit); #3 converges structurally.
3. **Introduce `CoreEvent`** (typed union) + `_process_core` as the streaming method's new internals;
   `process_message_stream` becomes the pass-through adapter. Golden SSE snapshot stays green.
4. **Rewrite `process_message` as the accumulating adapter** over `_process_core`, threading
   `prompt_style="structured"` and the new `mode="plan"` default (#1, #6). Golden batch snapshot stays
   green for the structural parts (#5 proven a no-op); the #6 `mode` rows update in this commit. Apply
   the per-caller `mode` override map (§5 #6) to the 5 batch callers in the same commit.
5. **Migrate `routes_chat.py`** off bare-dict `evt.get("event")` onto the typed union — **in scope**
   (owner 2026-06-16). It's the payoff that removes type-sniffing.

## 7. Test strategy

- **Golden snapshots** (§6.1) are the primary safety net — they must stay byte-identical except where
  a §5 row authorizes a change, in which case the snapshot is updated *in that row's commit only*.
- **`CoreEvent` unit tests** — each event serializes to the expected SSE dict and folds to the
  expected `result` key.
- **Adapter equivalence tests** — for the same mocked core event sequence, assert the SSE adapter
  yields the current event order and the batch adapter builds the current dict.
- **Live-path smoke** — exercise `routes_ws` action dispatch and the Telegram reply extraction
  against the new batch adapter (they read `result["presentation"]` / the whole dict).

## 8. Risks & rollback

- **Primary user path.** Both surfaces are the main chat experience; a regression is user-visible.
  Mitigation: golden snapshots + phased commits, each revertable.
- **`routes_ws` verbatim contract.** Any `result` key drift breaks WS clients silently. Mitigation:
  #5 is golden-pinned and proven a no-op in phase 4.
- **Background-vs-awaited event firing (#4).** Changing `run_completed` from awaited to background
  could reorder observable side effects. Mitigation: decide explicitly; cover with an event-order test.
- **Rollback:** each phase is a separate commit; phases 3–4 can be reverted to restore the two
  independent methods without touching the §5 behavior commits.

## 9. Out of scope / open questions for sign-off

- ~~**Q1 (#1):**~~ **ANSWERED (2026-06-16): intentional** — conversational (stream) vs.
  structured one-shot (batch). The core preserves both via a `prompt_style` parameter; do NOT
  reconcile to one prompt.
- ~~**Q2 (#2):**~~ **ANSWERED (2026-06-16): accidental drift, reconcile.** Archaeology confirmed the
  batch metadata injection is an unintended side effect of `result` doubling as the prior-step
  scratchpad (see row #2). Adopt the narrow `step_outputs`.
- ~~**Q3:**~~ **ANSWERED (2026-06-16): in-scope.** Migrating `routes_chat` to the typed union is part
  of this work (now phase 4 in the revised §6 plan, after Telegram removal collapsed the old phase 2).
- ~~**Timing:**~~ **ANSWERED (2026-06-16): now-milestone on `review/architecture-remediation`.**
- **NEW (#6, 2026-06-16):** batch adapter default `mode` is `plan`, not `ask` (owner decision). See
  row #6 for the per-caller override map. The latent ungated-background-write gap (scheduler callers
  bypass GraphExecutor+TrustEngine via the inline batch path) is acknowledged but not closed here —
  `custom_agent_task` overrides to `execute` to keep pre-authorized automation working.

## 10. Effort

L. Phase 1 (golden) is the bulk of the safety value and is low-risk. Phases 2–4 are gated by the §5
sign-offs. Recommend not bundling with release-packaging work — it wants its own focused window.

## 11. Dependency: Telegram removal precedes this fold

The Telegram integration is being removed completely (owner decision, 2026-06-16). It is its own
cleanup (a feature deletion, lower-risk than this fold) and **should land first** because:

- It removes one `process_message` caller (batch still has 5: WS surface-action default dispatch,
  WS execute-insight, scheduler meeting_prep ×3) — so batch stays load-bearing; the fold is
  unaffected in shape.
- It makes the `surface == "telegram"` branch and `build_telegram_hint` dead, simplifying the
  presenter-prompt logic the fold has to preserve (§5 #1).
- Doing it first keeps the fold's golden snapshots from having to encode soon-deleted Telegram
  behavior.

Telegram removal is cross-layer (interface, the `telegram.send` communication MCP tool +
catalog/schemas, notifier delivery surface + rate limits, surface_registry, settings, surface enums
in models). It warrants its own removal spec/plan; see the separate Telegram-removal planning.
