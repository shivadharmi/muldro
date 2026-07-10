# Step 10D — Coordinated Live Cutover (Implementation Plan + RUNBOOK)

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans for **Part A** (the dormant build tasks, TDD, checkbox-tracked). **Part B is an operational RUNBOOK** — its `R`-steps are wall-clock prod operations (flag/gate flips, clean-week holds, a merge, a migration), **not code commits**. Do not "execute" Part B in a coding session; run it against production with a human operator on the rollback lever.

**Goal:** Take the first-principles rebuild LIVE. This is the **4th and final** Step-10 sub-plan and the **only live + irreversible** step in the entire rebuild. It (1) builds the last dormant activation pieces (B2/B3/B4+A2/B5/B6/B8/B12) still-dormant and forced-tested, then (2) runs the coordinated cutover: final whole-branch review → **merge dormant machinery to `main`** (byte-identical under default `legacy`) + the CLAUDE.md two-execution-paths rewrite → **incremental flip** chat → perception → autonomous under 10B's shadow-compare + one-directional auto-rollback (1 production-clean-week per surface) → **B7 agent-row-drop migration** (6→4) → retire the escape hatch.

**Architecture:** Merge-then-flip. Every prior step proved the deep machinery is byte-identical to `legacy` when `JARVIS_RUNTIME=legacy`, so the branch merges to `main` **dormant** — the flip becomes a config/gate operation in prod, not a deploy. The cutover lever is 10B's **per-surface effective-runtime gate** (durable manual kill-switch + Redis auto-breaker + static `settings.runtime` fallback; `JARVIS_RUNTIME` cannot hot-change, so the gate is the mechanism). Each surface flip is armed by 10B's shadow-compare (live reads, hard-suppressed writes) + one-directional auto-rollback (deep→legacy auto; re-enable manual). Legacy code is NOT deleted here — it is the rollback fallback (a later post-rebuild cleanup).

**Tech Stack:** Python 3.13, LangGraph/deepagents deep runtime, `AsyncPostgresSaver` checkpointer, Redis (write-lock + effective-runtime breaker + shadow sampling), Next.js/React frontend (A2UI renderer), pytest (custom `asyncio.run` hook — NO pytest-asyncio), vitest, ruff, alembic.

---

> # ⚠️ ANCHORS @ `a5ab52f` — RE-VERIFY AT EXECUTION
> Every `file:line` below was verified against `a5ab52f` code state (plan-write HEAD is `51db83e` = `a5ab52f` + two **doc-only** commits: the 10A plan `2c30d17` + the ledger-decompose `51db83e`; `git diff a5ab52f..HEAD -- backend/src frontend/src` is **empty**). **10A, 10B, and 10C each mutate many of these same files** (`agent_invoker.py`, `step_runner.py`, `chat_processor.py`, `trust_gate.py`, `stream_adapter.py`, `checkpoint_reaper.py`, the settings flags, and the alembic head). By the time 10D executes, all three will have landed — so **re-locate every symbol by name, not by line number**, and re-read this plan's dependency claims against the *then-current* tree. Anchors rot; that is the rebuild's recurring lesson.

---

## 0. Context — read before touching anything

### 0.1 Where this sits (Step 10 decomposition, resolved 2026-07-10)

Step 10 is split 4 ways along the build-vs-flip fault line. 10A/10B/10C are all no-flip, offline/forced-provable. **10D is the live closeout.**

| Sub-step | Contents | Flip? |
|---|---|---|
| 10A | Category-A security hardening (A1/A3/A4/A5/A6/A7 + NEW-1/NEW-2; A2 = invariant-guard only) | No |
| 10B | Cutover control plane: 4 net-new rollback metrics + shadow-compare harness + per-surface **effective-runtime gate** + one-directional auto-rollback watcher + escape hatch | No |
| 10C | Autonomous durable engine: autonomous step executor on `build_deep_agent` (`authorization_source=autonomous`) + B9 (`AsyncPostgresSaver` + single-flight lease + reconcile-from-event-log) + B10 autonomous reaper + B11-auto slim. DAG orchestrator stays. | No |
| **10D (this plan)** | Build B2/B3/B4+A2/B5/B6/B8/B12 dormant → merge to `main` + CLAUDE.md rewrite → incremental flip chat→perception→autonomous (clean-week holds) → B7 row-drop migration → retire escape hatch | **Yes** |

### 0.2 Hard dependencies (10D CANNOT start until these are DONE)

- **10A DONE** — every Category-A gate landed; the deep runtime cannot introduce a live vuln at flip. In particular 10D's **A2 read_fn** (Task A-2) *builds on top of* 10A's `read_fn=None`-never-CONTRADICTS invariant guard (`tests/deep_runtime/test_readback_readfn_none_invariant.py`), and B4's real read_fn must not regress it. 10A also renames `thread_id = generate_id("chat")` (`agent_invoker.py:536`) → `make_thread_id(workspace_id)` (`src/deep_runtime/thread_identity.py`); B6's perception deep branch must mint its thread_id the same way.
- **10B DONE** — the **effective-runtime gate**, the **shadow-compare harness**, the **5 rollback signals** (the 4 net-new metrics + the shadow-divergence rate — *exact names/thresholds are owned by the 10B plan; this runbook references them, it does not define them*), the **auto-rollback watcher**, and the **escape hatch** are the machinery Part B drives. If the gate/watcher is not live, Part B has no lever and no safety net — **STOP**.
- **10C DONE** — the autonomous path runs on `build_deep_agent` behind a flag, dormant, with B9 durable-resume + reconcile. R4 flips *that*. B12's phase adapter (Task A-1) is only *exercised* once autonomous runs deep (10C) — chat-deep produces no phases (chat short-circuits `trust_gate`; the Perceiver is read-only), so B12 is **built dormant here, first live at R4**.

> As of plan-write, **only the 10A plan exists** (`docs/superpowers/plans/2026-07-10-step10a-security-hardening.md`); 10B and 10C are not yet written. 10D is authored ahead of them deliberately (to fix the closeout shape), but its execution is gated on all three landing.

### 0.3 Baseline (VERIFY at start of execution)

- Branch `rebuild/first-principles`, off `main`, **NOT pushed** (10D is the first push/merge of the whole rebuild). Plan-write HEAD `51db83e`; code state `a5ab52f`.
- `docker compose up -d postgres redis qdrant`. Redis `:6379` may be `hyperlocal-redis` OR `jarvis-redis-1`; UUID-suffix all test Redis keys. `uv sync --all-extras` (NO pip).
- Full gate: `uv run pytest tests/ --ignore=tests/e2e` → **3292 passed / 18 skipped** at `a5ab52f` (will be higher after 10A/10B/10C land their tests). ~108 skipped = infra DOWN = NOT green.
- `uv run alembic heads` → single `1a2770a28c39`; `uv run alembic check` drift-free; `ruff check src tests` clean.
- Frontend gate (B12 only): from `frontend/` → `npm run lint && npm run build && npm run test` (`test` = `vitest run`; baseline 100 passed at `a5ab52f`).
- **10D expects EXACTLY ONE migration** — the B7 agent-row-drop (Task R5). **Any OTHER task that wants an alembic migration → STOP and re-check** (it means a schema change crept in that the inline/dormant shape was supposed to avoid).
- A live Anthropic key is in `backend/.env` (`JARVIS_USE_BEDROCK=FALSE`). Part A tests use fake/mock models; **Part B is the only place a live deep model call reaches production.**

### 0.4 Test harness conventions (this repo — do NOT assume defaults)

- **NO pytest-asyncio / NO `asyncio_mode`** — a custom `pytest_pyfunc_call` `asyncio.run` hook runs coroutines. Write `async def test_...` directly.
- `make_mock_settings()`, `TEST_USER_ID`, `TEST_WORKSPACE_ID` from `tests/conftest.py`. **MagicMock-truthy hazard:** any behavior newly gated on a `deep_*` flag must have that flag **explicitly** set in the test (the existing `deep_*` flags are already defaulted in `make_mock_settings`; if Part A adds a new flag, default it there or every `runtime="deep"` test trips truthy).
- Mock Anthropic via `@patch("src.orchestrator.jarvis.get_anthropic_client")`.
- Real-DB/real-Redis tests are self-contained: `_db_reachable`/`_redis_reachable` guards + NullPool + seed the User→Workspace FK chain (NO `db_session` fixture). UUID-suffix Redis keys.
- Do NOT edit `backend/` files while a `uvicorn --reload` worker runs (hangs the HTTP server).

### 0.5 What 10D is NOT

- **Not a big-bang flip.** Three surfaces, one at a time, each with a wall-clock clean-week hold. A rollback on any surface pauses the ladder, does not unwind the merge.
- **Not a legacy-code deletion.** Legacy `agent_loop` / the non-deep branches stay — they ARE the auto-rollback fallback. Deletion is a later post-rebuild cleanup (ledger note; NOT Step 10).
- **Not the standards track.** C12 (A2UI→SurfaceKit rename), C13 (AG-UI/MCP-Apps + phase-machine deletion, incl. dropping the two dead `planning`/`partial` arms), C14 (Presenter Markdown fallback) are explicitly **after** the cutover settles.
- **Not a schema redesign.** One data-only DELETE migration (B7). Nothing else.

---

## PART A — dormant activation builds (forced-tested, byte-neutral flag-off)

**Discipline (all Part A tasks):** each lands the deep behavior **behind its flag, default OFF/dormant**, byte-identical on the live `legacy` path, proven by a **forced test** (flag flipped True in the test only) plus a **negative control with teeth** (a one-line mutation the guard/branch must defeat — the Step-8/9 lesson). Full backend gate at every checkpoint (18 skipped, not ~108). **These are the 6/7/8/9 rhythm; NO flag default changes to production behavior in Part A — the production flip is Part B.**

### ⚠️ Hot-file contention (READ FIRST)

`src/orchestrator/agent_invoker.py` is edited by **five** Part-A tasks (A-1 approval-frame seam is adjacent, A-2 read_fn, A-3 lead-scope, A-4 delegate routing, A-5 `active=` flip, A-6 perception branch). **Assign ONE owner to `agent_invoker.py` across A-2..A-6 and run them SYNCHRONOUSLY (`run_in_background:false`), sequenced A-3 → A-4 → A-2 → A-5 → A-6** (the 6B F811 / Step-8 P4 single-owner lesson). `chat_processor.py` (A-5) and `stream_adapter.py` + the frontend renderer (A-1) are separate owners and may run in parallel with the `agent_invoker.py` chain. Main loop owns verify + commit; confirm each reported SHA + gate count yourself.

### File Structure (Part A)

| File | Responsibility | Task |
|---|---|---|
| `src/deep_runtime/stream_adapter.py` | Native deep-stream → `SurfaceUpdate` phase translation; approval-frame → `ApprovalContext` unification | A-1 (B12) |
| `src/services/execution_surface_emitter.py` | `SurfaceUpdate` producer contract (target shape the adapter emits into) | A-1 (B12) |
| `frontend/src/components/a2ui/components/inline-approval.tsx`, `frontend/src/lib/a2ui-types.ts` | One `InlineApprovalCard` for both WS `ApprovalContext` + persisted REST (P3.2 renderer unification) | A-1 (B12) |
| `src/deep_runtime/middleware/readback.py`, new `src/deep_runtime/readback_readfn.py`, `src/services/step_runner.py` | Real per-connector `read_fn` through `ToolExecutor.execute_tool`; reproduce `_READBACK_UNSERVABLE_CAPABILITIES` | A-2 (A2/B4) |
| `src/orchestrator/agent_invoker.py` (`_augment_system_blocks_for_inline`) | Lead-scope the Presenter voice | A-3 (B2) |
| `src/orchestrator/agent_invoker.py` (`_build_delegate_subagents` + call-site) | LIVE lead→delegate routing decision | A-4 (B3) |
| `src/orchestrator/chat_processor.py`, `agent_invoker.py:354` | `runtime==deep` branch: drop presenter step + InteractionLearner spawn, flip librarian middleware `active=True` | A-5 (B5) |
| `src/orchestrator/agent_invoker.py` (`call_agent` non-stream) | Perception/briefing deep branch | A-6 (B6) |
| `src/deep_runtime/middleware/trust_gate.py`, `src/deep_runtime/trust_increment.py` | Capture modified-vs-approved from the interrupt verdict | A-7 (B8) |

New tests under `tests/deep_runtime/` and `tests/` mirror `src/`; frontend tests under `frontend/src/**/__tests__` (vitest).

---

### Task A-1 — B12: native-stream → `surface_update` adapter (+ P3.2 renderer unification + one-interrupt-approval contract)

**Why first / why hardest:** biggest blast-radius seam (stream + renderer, two live transports), and the only new backend *contract*. Build it early so it gets the most review soak. It is **dormant here and first live at R4** (autonomous-on-deep) — chat-deep emits no phases.

**The problem (verified anchors):**
- The **only** `SurfaceUpdate` producer today is the autonomous DAG: `execution_surface_emitter.emit_surface_update(phase=...)` (`execution_surface_emitter.py:126`) → Redis `jarvis:a2ui:{user_id}` (`:168`). Legacy until 10C makes autonomous run deep.
- The deep runtime's native stream is `stream_deep_agent_events` (`stream_adapter.py:131`), which today emits deep-native SSE frames including `_approval_needed_frame` (`stream_adapter.py:68`) — **not** `SurfaceUpdate` phases.
- `SurfaceUpdate` (`src/contracts/__init__.py:450`) has phase `Literal["planning","plan_ready","executing","approval_needed","completed","failed","partial"]` (`:461-462`). **Note: 7 arms, not 5** — `planning` + `partial` are the two dead arms C13 drops *after* Step 10; the adapter maps into the live 5 (`plan_ready`/`executing`/`approval_needed`/`completed`/`failed`) and leaves the dead arms untouched.
- Frontend: `InlineApprovalCard` (`inline-approval.tsx:37`) consumes a rich typed `ApprovalContext` (`@/lib/a2ui-types`, also used by `surface-store.ts:22`). The **persisted REST/detail** approval path does NOT carry that typed context (the Step-9 P3 deferral reason). Unifying the two transports onto one card needs the unified contract first.
- **⚠️ SPLIT TRAP:** the WS message-type `surface_update` is **overloaded** on the same channel `jarvis:a2ui:{user_id}` — the phase-machine `SurfaceUpdate` AND the Presenter's `push_ui_update` both ride it. The adapter must not let a deep phase frame be mistaken for a Presenter surface push (or vice-versa). Add a discriminator (e.g. an explicit `kind`/`source` on the envelope) or a separate sub-channel — decide in Step 1, do not leave it implicit.

**Files:** `src/deep_runtime/stream_adapter.py` (translation seam — extend, don't fork), `src/contracts/__init__.py` (unified approval contract), `frontend/src/lib/a2ui-types.ts` + `inline-approval.tsx` (renderer unification); Tests `tests/deep_runtime/test_stream_adapter_surface_update.py`, `tests/test_approval_contract_unification.py`, `frontend/src/components/a2ui/components/__tests__/inline-approval.test.tsx`.

- [ ] **Step 1 — design the envelope + contract (write it down before code).** Decide (a) the discriminator that disambiguates phase-`SurfaceUpdate` from Presenter `push_ui_update` on `jarvis:a2ui:{user_id}`; (b) the **one unified typed approval contract** both the WS `approval_needed` frame and the persisted REST path emit — the superset `ApprovalContext` (risk_level, trust_level, capability, run_id, artifact_refs, approve/edit/reject affordances). Record it in the plan/ledger before implementing.
- [ ] **Step 2 — failing backend test.** Feed a captured deep native stream (plan-ready → tool call → interrupt → completion) through the adapter with `runtime="deep"`; assert it emits `SurfaceUpdate` frames with phases in the live 5, in order, carrying `run_id`/`user_id`, on the disambiguated envelope; and that a deep `_approval_needed_frame` maps to the unified `ApprovalContext` (not a bare dict). Assert the dead arms (`planning`/`partial`) are never emitted.
- [ ] **Step 3 — run → FAIL** (`stream_deep_agent_events` emits no `SurfaceUpdate` today).
- [ ] **Step 4 — implement** the translation in `stream_adapter.py` (map native lifecycle events → `SurfaceUpdate.phase`; map the approval frame → unified `ApprovalContext`), emitting into the same `execution_surface_emitter` Redis/WS transport with the discriminator. Keep it a **pure translation** over the existing native stream — do not re-plumb the transport.
- [ ] **Step 5 — frontend failing test + unify.** vitest: `InlineApprovalCard` renders from BOTH (a) a live WS `ApprovalContext` and (b) a persisted-REST-shaped approval mapped through the new unified contract. Then thread the unified type through `a2ui-types.ts` and delete any REST-only approval shape (**no** client-side conversion function — server emits the unified contract). Do NOT reintroduce `approvalToSurface()`/`useSurfaceState` (both deleted; CLAUDE.md rule).
- [ ] **Step 6 — negative controls (teeth):** (a) map a completion event to the wrong phase → ordering/assertion test fails; (b) drop the envelope discriminator → the "phase frame not mistaken for a Presenter push" test fails; (c) feed a REST approval missing a required `ApprovalContext` field → the unified-contract validation fails (fail-closed, not a silently half-rendered card). Restore.
- [ ] **Step 7 — gates:** backend full gate (18 skipped); frontend `npm run lint && npm run build && npm run test`.
- [ ] **Step 8 — review:** this is a **2-stage PARALLEL** review seam (spec + quality) on the frozen commit — stream + renderer + a new cross-language contract. Include a frontend-literate reviewer.
- [ ] **Step 9 — commit:** `feat(step10d): B12 native-stream→SurfaceUpdate adapter + unified approval contract + InlineApprovalCard renderer unification (dormant)`.

> **Open question (flag to the user at execution):** does B12 belong in 10D at all, or does its *exercise* fold into 10C (which owns making autonomous run deep, the only phase producer)? Recommendation: **build the adapter + contract in 10D Part A** (it is a UI-contract concern, not an engine concern) but treat its first-live validation as an **R4 acceptance gate**, not an R2/R3 one. The persisted-REST half of the renderer unification IS exercised regardless of runtime (any surface with an approval detail tab) and should be validated at R2.

---

### Task A-2 — A2/B4: real per-connector `read_fn` + flip `deep_readback_enabled`

**Why (verified anchors):** 7C wired read-back with `read_fn=None` (`agent_invoker.py:398`) → every irreversible write → UNVERIFIED, never CONTRADICTED. 10A locked that as an invariant. B4 replaces it with a **real** `read_fn` routing through `ToolExecutor.execute_tool` that **reproduces** `_READBACK_UNSERVABLE_CAPABILITIES` (`step_runner.py:38` = `frozenset({"calendar.get"})`) so the lone mock-only post-condition `calendar.create` (`post_conditions.py:54`, `read_capability="calendar.get"`, backed by `query_freebusy`) **cannot false-CONTRADICT**.

**Files:** new `src/deep_runtime/readback_readfn.py` (the connector read_fn factory), `src/deep_runtime/middleware/readback.py` (accept the real fn), `agent_invoker.py:398` (wire it), reuse `step_runner.py`'s unservable set (single source — import, do NOT re-list); Test `tests/deep_runtime/test_readback_real_readfn.py`.

- [ ] **Step 1 — failing test:** a real `read_fn` (over a fake `ToolExecutor`) that (a) for a servable capability returns the post-read and the verifier CONFIRMS/CONTRADICTS correctly; (b) for `calendar.get` (unservable) returns UNVERIFIED, **never CONTRADICTED**, so `calendar.create` cannot false-fail. Assert the unservable set is the **same object/source** as `step_runner._READBACK_UNSERVABLE_CAPABILITIES` (drift guard).
- [ ] **Step 2 → 4:** RED → implement the factory routing through `ToolExecutor.execute_tool`, importing the unservable set → GREEN.
- [ ] **Step 5 — negative controls:** (a) drop the unservable-set reproduction → the `calendar.create` false-CONTRADICT test fails; (b) re-point `read_fn` to a raw connector call bypassing the dispatcher → capability-scope/dispatch guard test fails.
- [ ] **Step 6 — full gate; Step 7 — commit:** `feat(step10d): B4 real per-connector read_fn through dispatcher + unservable-denylist reproduction (dormant, deep_readback_enabled off)`.

> `deep_readback_enabled` stays **default OFF** after this task. It is flipped in Part B (see the flag-consolidation note in R1). **B4a conscious call (7C gate d):** read-back sits INNER of write_lock, so the real read_fn + the trust-increment's fresh session execute while the cross-path lock is held (atomic write+verify — arguably correct). Record the sign-off in the ledger; do not "fix" it.

---

### Task A-3 — B2: lead-scope the Presenter voice + flip `deep_inline_format`

**Why (verified anchors):** `_augment_system_blocks_for_inline` (`agent_invoker.py:68`, called `:563`) appends `PRESENTER_VOICE` **agent-agnostically** today — every deep agent on a `call_agent_stream` turn gets it. The helper's own ACTIVATION NOTE (`:78`) says to scope it to the reply-producing **lead** only. (Idempotent guard at `:86` already prevents double-append.)

**Files:** `agent_invoker.py` (`_augment_system_blocks_for_inline` + its call at `:563` — thread through whether the current agent is the reply-lead); Test `tests/test_augment_inline_lead_scope.py`.

- [ ] **Step 1 — failing test:** with `deep_inline_format=True`, assert `PRESENTER_VOICE` is appended ONLY for the lead/reply-producing agent and NOT for a delegate/non-lead agent in the same turn; with the flag False, byte-identical to today (no append).
- [ ] **Step 2 → 4:** RED → pass a `is_reply_lead` (or equivalent role signal) into the helper and gate the append on it → GREEN.
- [ ] **Step 5 — negative control:** remove the lead gate → the "delegate does NOT get PRESENTER_VOICE" test fails. **Step 6:** full gate. **Step 7:** commit `feat(step10d): B2 lead-scope PRESENTER_VOICE augmentation (dormant, deep_inline_format off)`.

---

### Task A-4 — B3: LIVE lead→delegate routing + flip `deep_delegates_enabled`

**Why (verified anchors):** `_build_delegate_subagents` (`agent_invoker.py:445`) registers the Perceiver delegate on the `task` tool (and disables the general-purpose subagent, `:478-479`), but **nothing drives the lead to delegate** — the scaffolding exists, the live routing decision does not.

**Files:** `agent_invoker.py` (the lead's system prompt / delegation affordance + the call path that lets the lead invoke the `task` tool with the Perceiver delegate); Test `tests/test_lead_delegate_routing.py`.

- [ ] **Step 1 — failing test:** with `deep_delegates_enabled=True`, a lead turn whose query needs a read-only research sub-task actually routes to the Perceiver delegate (assert on the constructed `subagents=`/delegation call, NOT on model behavior — feed a fake model that emits the `task` tool call); with the flag False, no delegate is offered and the turn is lead-only (byte-neutral). Reuse 10A's A4 degrade-to-no-delegates hardening (a build failure must still yield a lead-only turn).
- [ ] **Step 2 → 4:** RED → build the routing (delegation instruction in the lead prompt + wiring the `task`-tool delegate as an available action) → GREEN.
- [ ] **Step 5 — negative control:** force the delegate build to raise → the turn still completes lead-only (10A A4), not a crash. **Step 6:** full gate. **Step 7:** commit `feat(step10d): B3 live lead→delegate routing (dormant, deep_delegates_enabled off)`.

---

### Task A-5 — B5: `chat_processor` runtime branch (drop presenter step + InteractionLearner spawn, flip librarian middleware `active=True`)

**Why (verified anchors):** on `runtime==deep` the deep lead produces the user-facing reply inline (B2) and the Librarian runs as **middleware** (`agent_invoker.py:354` wires it `active=False`, dormant). But `chat_processor.py` is runtime-agnostic and still, every turn: makes the **presenter** step call (`chat_processor.py:584-595`) and spawns the runtime-agnostic **InteractionLearner** (`:622-633`, injected `:117`/`:136`). On deep these **double-fire** — the deep lead already formatted the reply and the librarian-middleware already extracts. B5 adds a `runtime==deep` branch that DROPS the presenter step + the InteractionLearner spawn AND flips the librarian middleware `active=True`.

**Files:** `src/orchestrator/chat_processor.py` (the `process_message_stream` body around `:550-633`), `agent_invoker.py:354` (`active=` becomes runtime-derived, not hardcoded False); Test `tests/test_chat_processor_deep_branch.py`.

- [ ] **Step 1 — failing test:** with `runtime="deep"`, assert `process_message_stream` (a) does NOT invoke the presenter step (`build_presenter_message`/`call_agent("presenter")` not called), (b) does NOT spawn the InteractionLearner, (c) the librarian middleware is built `active=True`; and the surface push still happens (the deep lead's inline surface, not the presenter's). With `runtime="legacy"` (default), byte-identical to today: presenter step fires, learner spawns, middleware `active=False`.
- [ ] **Step 2 → 4:** RED → add the single runtime branch in `chat_processor` + make the `active=` flag runtime-derived → GREEN.
- [ ] **Step 5 — negative controls (teeth):** (a) leave the presenter step firing on deep → a "presenter double-fire" assertion fails; (b) leave the InteractionLearner spawning on deep → a "learner double-fire with librarian-middleware" assertion fails; (c) leave `active=False` on deep → a "librarian never extracts" assertion fails. Restore each.
- [ ] **Step 6 — full gate** (this is a **blast-radius seam** — `chat_processor` is the chat hot-path; deletions can surface latent presenter/learner consumers). Confirm 18 skipped.
- [ ] **Step 7 — review:** 2-stage PARALLEL (spec + quality) on the frozen commit. **Step 8 — commit:** `feat(step10d): B5 chat_processor deep runtime branch — drop presenter+learner, activate librarian middleware (dormant)`.

---

### Task A-6 — B6: perception/briefing deep branch in `call_agent` (non-stream)

**Why (verified anchors):** the deep branch lives ONLY in `call_agent_stream` (chat). `call_agent` (non-stream, `agent_invoker.py:762`) has **no** deep branch → perception/briefing always run legacy. Perception uses BOTH the Perceiver AND the Librarian: `perception_runner.py:143`/`:446` call `call_agent("planner"/...)` and `:277` calls `call_agent("librarian")`. B6 adds the deep branch to `call_agent` so perception runs deep when the perception surface is flipped.

**Files:** `agent_invoker.py` (`call_agent` — mirror the `call_agent_stream` deep branch: build the deep agent, run non-streamed, same tool-dispatcher/capability-scope invariants; mint `thread_id` via 10A's `make_thread_id(workspace_id)`); Test `tests/test_call_agent_deep_branch.py`.

- [ ] **Step 1 — failing test:** with `runtime="deep"`, `call_agent("perceiver"/"librarian", ...)` runs through `build_deep_agent` (assert the deep path is taken — fake model) with the **same** capability-scope outer guard + central `jarvis_tool_dispatcher` inner as `call_agent_stream`; with `runtime="legacy"` (default), byte-identical to today.
- [ ] **Step 2 → 4:** RED → add the deep branch to `call_agent` (factor the shared build with `call_agent_stream` to avoid a second drift point) → GREEN.
- [ ] **Step 5 — negative control:** point the deep branch's tool execution around the dispatcher → the capability-scope-enforcement test fails. **Step 6:** full gate. **Step 7:** commit `feat(step10d): B6 perception/briefing call_agent deep branch (dormant)`.

> **Confirmed at plan-write:** the **briefing** path (`perception_runner.py:387-406`, `:604-624`) does NOT call the presenter *agent* — it stores a briefing memory (`mem_svc.store_briefing_memory`) and surfaces via `SurfaceService`. So briefing has no presenter-double-fire concern; the Presenter *agent-row* drop (B7) is gated only on the **chat** flip, not perception. (Re-verify no `call_agent("presenter")` caller exists outside `chat_processor` at execution.)

---

### Task A-7 — B8: `decision_type` modified/approved on the deep gate

**Why (verified anchors):** `trust_increment.py:8` documents that the deep interrupt verdict is a bare "approve" and records `decision_type="approved"` (`:36`); `trust_gate.py:327`/`:375` compute `approved = verdict == "approve" or (...)`. The **modified/approved** distinction (user edited the action before approving vs approved as-is) is lost → trust graduation over-counts clean approvals. B8 captures the distinction from the interrupt verdict.

**Files:** `src/deep_runtime/middleware/trust_gate.py` (derive `modified` vs `approved` from the verdict payload), `src/deep_runtime/trust_increment.py` (thread `decision_type` through `record_approval_decision`); Test `tests/deep_runtime/test_trust_gate_decision_type.py`.

- [ ] **Step 1 — failing test:** an interrupt verdict that carries an edit/modification records `decision_type="modified"`; a bare approve records `"approved"`; a reject records `"rejected"` (unchanged). Assert the value that reaches `record_approval_decision`.
- [ ] **Step 2 → 4:** RED → derive + thread `decision_type` → GREEN. **Step 5 — negative control:** collapse modified→approved → the graduation-accuracy test fails. **Step 6:** full gate. **Step 7:** commit `feat(step10d): B8 capture modified/approved decision_type on deep trust gate`.

---

### Part A close-out

- [ ] Full backend gate (18 skipped) + frontend gate (B12) green. `uv run alembic check` drift-free, **single head still `1a2770a28c39` — ZERO migrations in Part A**.
- [ ] **Holistic opus review of Part A** (independent reviewer re-runs both gates + reproduces every negative control: RED → `git checkout` restore → GREEN, tree clean). Security-reviewer on A-1 (external UI contract) + A-2 (verification correctness).
- [ ] Confirm **every deep behavior is still dormant** (`JARVIS_RUNTIME=legacy`, all `deep_*` sub-flags default OFF) → live path byte-unchanged. This is the precondition for R0.

---

## PART B — the live cutover RUNBOOK

> **⚠️ These `R`-steps are OPERATIONAL, wall-clock, and (from R1) IRREVERSIBLE-ish.** They are prod flag/gate flips, a merge, a migration, and clean-week holds — **not code commits** (except R1's merge/doc commit and R5's migration commit). A human operator holds the rollback lever throughout. "1 production-clean-week" = a **wall-clock 7-day hold** with the effective-runtime gate on `deep` for that surface, watching 10B's 5 rollback signals + shadow-divergence — NOT a test suite.
>
> **Flag topology for Part B (recommended consolidation — decide with 10B at R1):** after R1's merge, flip the sub-flag **defaults** `deep_inline_format`/`deep_delegates_enabled`/`deep_readback_enabled` to **True** in `settings.py`. This is byte-neutral in prod because `JARVIS_RUNTIME=legacy` and every surface's effective runtime starts `legacy` — the deep path (hence those flags) is never read until a surface is gated to `deep`. Consolidating this way makes 10B's **per-surface effective-runtime gate the single cutover lever**, instead of juggling four flags per surface. (`deep_context_jit`/B11 is NOT in this set — it is a behavior-change quality-flip owned by 10C's autonomous cutover; keep it separate.) If 10B's gate design already subsumes the sub-flags, skip this and drive the gate only.

### R0 — Final whole-branch holistic review

**Precondition:** Part A closed out; 10A/10B/10C all DONE on the branch; full backend + frontend gates green.

- [ ] **The per-step reviews never saw the assembled whole.** Run an independent **holistic opus review of the ENTIRE `main..rebuild/first-principles` diff** (0→10), not a per-step re-review: cross-step invariant integrity (tools-are-schemas/execution-is-central on both paths; capability-scope outer / dispatcher inner; TrustEngine as the single autonomous gate; workspace-isolation on every new surface incl. checkpointer thread_id + reaper), the effective-runtime gate + auto-rollback wiring end-to-end, and that legacy is genuinely byte-neutral under default flags.
- [ ] **Security-reviewer pass** on the cross-path write-lock, checkpointer tenant-binding (A6/NEW-1), critique injection fence (A1), and the B12 external UI contract.
- [ ] Re-run BOTH gates from a clean checkout. Confirm single alembic head `1a2770a28c39`, drift-free.
- [ ] **Success criteria:** SHIP verdict with no open CRITICAL/HIGH. Any HIGH → fix on-branch, re-review, do not proceed to R1.

### R1 — Merge-then-flip to `main` + CLAUDE.md rewrite

**Precondition:** R0 SHIP.

- [ ] **This is the first push/merge of the entire rebuild.** Merge `rebuild/first-principles` → `main` **with all deep machinery dormant** (`JARVIS_RUNTIME=legacy` default; effective-runtime gate defaults every surface to `legacy`; sub-flag consolidation per the topology note). Behavior on `main` is **byte-identical** to pre-merge prod because nothing is on `deep`. This de-risks branch drift and turns the cutover into a config/gate op.
- [ ] **CLAUDE.md two-execution-paths rewrite (the durable doc edit, earned only at MERGE per the doc policy / 6B lesson).** Rewrite the "Two execution paths" / "Deep Agents runtime" sections so `deep` is the **documented** runtime (not "in-progress replacement, default legacy"): describe the **per-surface effective-runtime gate** as the cutover mechanism, the shadow-compare + one-directional auto-rollback, and that legacy remains as the rollback fallback (not yet deleted). Update the agent table / boundaries to reflect Presenter+Librarian collapsing to middleware/tools (foreshadow B7). Do NOT delete the legacy-path documentation — it is still live as fallback.
- [ ] Deploy `main` to prod (`sudo /opt/jarvis/infra/scripts/deploy.sh main`). **No behavior change expected** — this is a dormant deploy. Smoke: a chat turn + a perception tick still run **legacy** (assert via 10B's `AGENT_RUNTIME_CALLS` metric = legacy).
- [ ] **Success criteria:** `main` deployed, all surfaces reading `legacy`, prod green, gate + watcher live and reporting. **From here the branch is integrated; rollback is per-surface gate, not `git revert`.**

### R2 — Flip **chat** to deep (1 production-clean-week)

**Precondition:** R1 deployed dormant; 10B gate + watcher live; shadow-compare validated on chat reads.

- [ ] **The flip:** set the **chat** surface's effective-runtime gate → `deep` (the durable manual switch). Chat now runs `build_deep_agent` via `call_agent_stream` with B2/B3/B4/B5 active. (Chat is ungated by `trust_gate` **by design** — the user's message is authorization; capability-scope in the dispatcher is the compensating control. Do NOT add a chat trust gate.)
- [ ] **What to watch (7-day wall-clock hold):** 10B's **5 rollback signals** (owned/defined in 10B — the 4 net-new rollback metrics + the shadow-divergence rate) plus: presenter/learner double-fire counters at zero (B5 correctness), capability-scope-denial rate not spiking, surface-render errors on the unified approval contract (B12 REST half), latency/cost per turn within band, error-rate flat.
- [ ] **Rollback trigger:** any of the 5 signals crosses its 10B threshold → the auto-rollback watcher flips chat's gate `deep→legacy` **automatically** (one-directional). Re-enable is **manual** after root-cause. A rollback **pauses the ladder** (do not proceed to R3); it does NOT unwind the merge.
- [ ] **Success criteria:** 7 consecutive clean days on `deep` for chat, no auto-rollback, shadow-divergence within band. Then chat is "cleared."

### R3 — Flip **perception** to deep (1 production-clean-week)

**Precondition:** R2 cleared (chat stable on deep ≥7 days).

- [ ] **The flip:** set the **perception** surface's gate → `deep`. Perception now runs `call_agent` deep (B6) for both Perceiver AND Librarian (`perception_runner.py:277`).
- [ ] **What to watch:** the 5 signals + perception-specific: perception idempotency (no double-pick — `pending_run` atomicity holds on deep), briefing generation still fires (no double/false briefing), Librarian extraction rate on deep matches legacy baseline (shadow-divergence on entity/memory writes — suppressed in shadow, compared on flip), scheduler tick budget not blown by deep latency.
- [ ] **Rollback trigger:** same one-directional auto-rollback; a rollback here pauses before R4.
- [ ] **Success criteria:** 7 clean days on `deep` for perception. Then **both chat + perception are cleared** — the precondition for dropping the **Librarian** agent row (B7).

### R4 — Flip **autonomous** to deep (1 production-clean-week) — the 10C engine

**Precondition:** R3 cleared; 10C's autonomous durable engine (`build_deep_agent` step executor + B9 `AsyncPostgresSaver` + single-flight lease + reconcile-from-event-log + B10 reaper) DONE and dormant on `main`.

- [ ] **The flip:** set the **autonomous** surface's gate → `deep`. The autonomous step executor now runs `build_deep_agent` (`authorization_source=autonomous`) under the DAG orchestrator (which **stays**). This is the first time the deep `trust_gate` is a **live producer** (autonomous is the only gated producer) and the first time **B12's phase adapter is exercised** (deep autonomous runs emit `SurfaceUpdate` phases via the A-1 adapter). Validate B12 end-to-end here (R4 acceptance gate): live execution surfaces render, approval interrupts show the unified `InlineApprovalCard`, phases advance plan_ready→executing→approval_needed→completed/failed.
- [ ] **What to watch:** the 5 signals + autonomous-specific: **double-fire counter at zero** (single-flight lease + idempotency ledger — the highest-stakes signal, since this path does real writes), durable-resume correctness after an interrupt (reconcile-from-event-log replays exactly once), TrustEngine graduation counts sane (B8 modified/approved feeding correctly), checkpointer tenant-isolation (no cross-ws thread claim), reaper not over-reaping pending threads.
- [ ] **Rollback trigger + in-flight drain:** one-directional auto-rollback deep→legacy. **On any autonomous rollback, in-flight deep runs must be drained via reconcile** (B9's reconcile-from-event-log) — do NOT hard-kill a mid-execution durable run; let the reconcile settle it, then resume on legacy. This is the one R-step where rollback needs an explicit drain procedure (autonomous does irreversible external writes).
- [ ] **Success criteria:** 7 clean days on `deep` for autonomous, zero double-fires, B12 phase surfaces validated live. **All three surfaces now cleared.**

### R5 — B7 agent-row-drop migration (6→4)

**Precondition:** ALL THREE surfaces cleared (chat + perception + autonomous stable on deep). Presenter+Librarian are now dead as *agents*: chat uses the deep lead (Presenter collapsed to inline B2 + B5), perception+chat both extract via the librarian **middleware** (Librarian collapsed).

**⚠️ CODE-FIRST, THEN MIGRATION (else `seed_defaults` resurrects the rows on restart).** `AgentRegistry.seed_defaults` (`agent_registry.py:47`) iterates `AGENT_PROMPTS.items()` (`:59`) and CREATES/UPDATES, never deletes.

- [ ] **Step 1 — code prune (commit 1):** remove the `presenter` + `librarian` keys from `AGENT_PROMPTS` (`prompts.py:706`, keys at `:708`/`:711`). **Anchor correction:** also prune them from `AGENT_CAPABILITY_SCOPES` (**`src/orchestrator/agents.py:28`**, NOT prompts.py) and `AGENT_MODEL_TIERS` (**`agents.py:16`**) — the task brief named only AGENT_PROMPTS + AGENT_CAPABILITY_SCOPES; `AGENT_MODEL_TIERS` is a **third** dict with those keys. Strictly, removing the `AGENT_PROMPTS` keys is sufficient to stop seeding (the loop iterates `AGENT_PROMPTS`; the other two are `.get(name, default)` lookups at `agents.py:224-225`), but prune all three for hygiene so no dead keys linger. Keep `perceiver`/`planner`/`executor`/`persona` (Perceiver STAYS — it is the deep-chat delegate AND the legacy-perception agent). Remove `PRESENTER_PROMPT`/`LIBRARIAN_PROMPT` string constants only if nothing else imports them (grep first). Full gate green after the prune (seed now maintains 4 agents; the 2 rows are stranded).
- [ ] **Step 2 — data-only DELETE migration (commit 2):** copy the shape of `alembic/versions/1a2770a28c39_drop_governor_agent_row.py` (+ `574f6c145bca_drop_operator_agent_row.py`): `Revises = 1a2770a28c39` (the current single head), `upgrade()` = `op.execute("DELETE FROM agents WHERE name IN ('presenter','librarian')")` (idempotent; agents are referenced by **name-string only** — the governor migration's repo-wide sweep confirmed NO `ForeignKey` to `agents.agent_id`/`name`, so no cascade surprise), `downgrade()` = intentional NO-OP with the same rationale comment. This is **the ONLY Step-10 migration.**
- [ ] **Step 3:** `uv run alembic upgrade head` on prod; confirm 4 agent rows remain; full gate + `alembic check` drift-free (new single head).
- [ ] **Success criteria:** `agents` table = {perceiver, planner, executor, persona}; seed_defaults stable across a restart (does NOT re-create presenter/librarian).

> **Open question — split Presenter-drop vs Librarian-drop into two migrations by timing?** The two rows become droppable at **different** clean-week milestones: **Presenter** after **R2** (chat cleared — chat is the only presenter-*agent* caller; briefing does NOT use the presenter agent, confirmed A-6 note), **Librarian** only after **R3** (perception cleared — `perception_runner.py:277` `call_agent("librarian")`). **Options:** (a) **single migration after R3** dropping both (simplest — one code-prune + one migration + one restart; Presenter's row lingers harmlessly stranded for the R2→R3 week, exactly as governor's did before its cleanup); (b) **two migrations** — drop Presenter after R2, Librarian after R3 (two code-prunes, two migrations, two restart-coordinated deploys; matches timing precisely but adds a second head-advancing migration, mild churn). **Recommendation: (a) single migration after R3** — a stranded unrouted row is provably harmless (the governor/operator precedent), and one migration honors the "B7 is the ONLY Step-10 migration" constraint. Choose (b) only if an operator wants the Presenter row gone the moment chat clears. **Decide with the user at execution.**

### R6 — Retire the escape hatch

**Precondition:** R5 done; all three surfaces stable on deep past their holds; no rollback outstanding.

- [ ] Retire 10B's **escape hatch** (the emergency all-surfaces-to-legacy override) now that every surface has cleared — OR keep it one more cycle as belt-and-suspenders (operator call). The **auto-rollback watcher stays** (it is the standing safety net); only the manual blanket escape hatch is retired.
- [ ] **Legacy code stays** — it is the per-surface auto-rollback fallback. Its deletion is a **later post-rebuild cleanup** (ledger note), NOT Step 10. Same for C12/C13/C14 (standards track + phase-machine deletion + the two dead `planning`/`partial` arms).
- [ ] **Ledger + memory close-out:** in `docs/superpowers/plans/2026-07-08-activation-gate-ledger.md` check off ALL Category-B items (B1–B12) as DONE with their landing commit/date; update `project_first_principles_rebuild.md` + `MEMORY.md` with the "STEP 10D DONE = SHIP, rebuild COMPLETE, LIVE on `main`" block (merge SHA, per-surface clear dates, B7 migration head, escape-hatch status, what remains: legacy-code deletion + C-track).
- [ ] **Success criteria:** rebuild is LIVE on `main`, deep runtime default-on across all surfaces, auto-rollback armed, escape hatch decision recorded, docs closed.

---

## Review strategy

- **Part A:** each behavior lands behind its flag with a **negative control with teeth**. **Blast-radius seams get 2-stage PARALLEL review** on the frozen commit: A-1 (B12 stream+renderer+contract, incl. a frontend-literate reviewer + security-reviewer) and A-5 (B5 chat_processor hot-path). A-2 (verification correctness) + A-7 (trust graduation) = combined review. A-3/A-4/A-6 (agent_invoker chain) = combined review, but note the **single-owner + SYNCHRONOUS + sequenced** dispatch on `agent_invoker.py` (A-3→A-4→A-2→A-5→A-6).
- **R0:** independent **holistic opus** over the WHOLE 0→10 branch diff (not per-step) + security-reviewer. This is the review the per-step passes could not do.
- **Part B:** the "review" is operational — the shadow-compare + 5 rollback signals + clean-week holds ARE the acceptance gates. A human operator owns the rollback lever at every R-step.
- **Full gate at every Part-A checkpoint** (18 skipped, not ~108); frontend gate for B12. `alembic check` drift-free with **single head** until R5 (the one migration).

---

## Self-Review (run after drafting, before execution)

1. **Coverage vs the ledger Category B:** B1 (master switch) → subsumed by the effective-runtime gate + R1 default consolidation. B2✓(A-3) B3✓(A-4) B4✓(A-2, +B4a sign-off) B5✓(A-5) B6✓(A-6) B7✓(R5) B8✓(A-7) B12✓(A-1). **B9/B10/B11 are 10C**, not 10D (autonomous engine) — R4 flips them live; note the cross-dependency, don't rebuild them here. B11's `deep_context_jit` is a 10C-owned quality flip, deliberately OUT of the R1 sub-flag consolidation.
2. **Byte-neutrality:** every Part-A change is dormant-deep or flag-defaulted-to-today. The R1 sub-flag-default-True consolidation is byte-neutral ONLY because `JARVIS_RUNTIME=legacy` + gate-defaults-legacy means the deep path is never read. Confirm this holds against the then-current settings shape.
3. **One migration:** only R5 (B7 data-only DELETE). Any other alembic want → STOP.
4. **Anchors:** all `file:line` verified @ `a5ab52f`; RE-VERIFY at execution (10A/10B/10C mutate these files — esp. `agent_invoker.py`, `step_runner.py`, `trust_gate.py`, `stream_adapter.py`, the settings flags, and the alembic head).
5. **Irreversibility discipline:** R0→R6 is the only push/merge/live sequence in the rebuild. Every R-step names its rollback trigger; R4 alone needs the reconcile-drain. The clean-weeks are wall-clock, not test runs.
6. **CLAUDE.md edit is HERE** (R1, at merge) and nowhere earlier — the doc policy / 6B lesson.

---

## Execution Handoff

Plan complete. Execution is a LATER session (do NOT execute in the planning run) and is **gated on 10A + 10B + 10C being DONE** — 10D is authored ahead to fix the closeout shape. When executed: **Part A** subagent-driven (single-owner-per-file + SYNCHRONOUS dispatch on `agent_invoker.py`; 2-stage parallel review on the B12 + B5 seams; full gate every checkpoint), then **Part B** as an operations runbook with a human operator on the rollback lever, one surface per clean-week, B7 migration after R3, escape-hatch retirement + ledger/memory close-out last. Confirm reported SHAs + gate counts yourself; re-verify every anchor before touching code.
