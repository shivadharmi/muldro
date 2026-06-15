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
| 2 | **Agent prior-context** | injects from the whole `result` dict (incl. `trace_id`, `interaction_id`, `plan`, `summary`, system outputs) | injects only `step_outputs` (prior agent text) | Adopt the stream's narrow `step_outputs` injection | **Drift / latent bug** — batch leaks plan/trace metadata into downstream agent prompts | Med — changes agent inputs; may shift tool choices |
| 3 | **`direct_answer` pick** | suffix-match `k.endswith(f"_{read_step.capability}")` | `next(iter(step_outputs.values()))` | Use the explicit suffix-match (deterministic for multi-output) | Drift | Low — converges for the single-read case both guard on |
| 4 | **Runtime events** | `plan_generated` via `_publish_event`; `run_completed` **awaited** | `plan_created` via `_fire_event` (background) + SSE `plan` | Settle on one event name + one firing discipline (background) | **Drift** — `plan_generated` vs `plan_created` is almost certainly an accident | Med — telemetry/consumers keyed on event names |
| 5 | **Output contract** | `result` dict (returned verbatim by `routes_ws`) | SSE stream | Core emits a superset; batch adapter rebuilds the **exact** current dict (golden-pinned); SSE adapter unchanged | N/A (structural) | High — `routes_ws`/Telegram break if a key changes |
| 6 | **`mode` param** | none | `ask`/`execute`/`plan` (plan-mode skips, `requires_user_input`) | Core takes `mode`; batch adapter passes `mode="ask"` | Intentional (stream-only feature) | Low — additive for batch |

## 6. Execution plan (phased; each phase independently revertable)

1. **Golden characterization (no behavior change).** Drive both methods with mocked `_call_agent` /
   `_call_agent_stream` across a plan matrix: single read-only step; multi-step read→reason; a
   write step with a user-action step; a `system.*` step; an error/raise; (stream-only) each `mode`.
   Snapshot the batch `result` dict (3a/3b) and the SSE event order (3c). Land green on current code.
2. **Reconcile drift #1–#4, one behavior commit each**, *while the two methods still exist*, so the
   golden diff shows exactly what each decision changed. Each commit references this spec's row and
   carries the owner's sign-off. Some rows may resolve to "preserve current behavior" (e.g. keep #1
   per-method) — that's a valid decision, recorded here.
3. **Introduce `CoreEvent`** (typed union) + `_process_core` as the streaming method's new internals;
   `process_message_stream` becomes the pass-through adapter. Golden SSE snapshot stays green.
4. **Rewrite `process_message` as the accumulating adapter** over `_process_core`. Golden batch
   snapshot stays green (this is where #5 is proven a no-op).
5. **Migrate `routes_chat.py`** off bare-dict `evt.get("event")` onto the typed union (optional but
   recommended — it's the payoff that removes type-sniffing).

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
- **Q2 (#2):** Confirm the batch path injecting plan/trace metadata into downstream agent prompts is
  unintended before "fixing" it.
- **Q3:** Is migrating `routes_chat` to the typed union in-scope (phase 5) or a follow-up?
- **Timing:** Is this a now-milestone on this branch, or a backlog spec for after the OSS release push?

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
