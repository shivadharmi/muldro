# Step 10C — Autonomous Durable Engine (Implementation Plan)

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking. **Phase 0 is SPIKE-FIRST and can DISPROVE the approach — do not skip to P1.**

**Goal:** Cut the **autonomous step executor** from the legacy `agent_loop` onto `build_deep_agent` (`authorization_source=autonomous`) + LangGraph durable execution (`AsyncPostgresSaver`, `durability="sync"`), add a single-flight lease + a reconcile-from-event-log consumer + an autonomous checkpoint reaper + the autonomous context-slim — **all DORMANT behind a flag**, byte-identical to today's legacy autonomous path when the flag is off. The **DAG orchestrator stays** (`graph_executor`/`dag_runner`): `build_deep_agent` is a single react-agent, not a multi-step workflow engine, so the DAG cannot "become" a deep agent. This is the **third of four Step-10 sub-plans** (10A security / 10B control-plane / **10C autonomous-engine** / 10D live-cutover); **no flag is flipped here — 10D flips.**

**Architecture:** Introduce `run_step_via_deep_agent(...)` alongside the existing `run_step_via_agent_loop(...)` in `StepRunner`. A per-surface **effective-runtime gate** (reused from 10B, keyed `"autonomous"`) selects which one `run_step_action` calls. When deep: each step compiles a deep agent (the routed executor `SubAgent`) via the shared gated build path with `authorization_source=AUTONOMOUS`, streams/invokes it under a durable `AsyncPostgresSaver` on a **workspace-bound `thread_id`** (`make_thread_id`, from 10A), and the Step-1 idempotency ledger makes LangGraph's at-least-once replay exactly-once. The DAG orchestrator drives ready-step selection, approval interrupts, checkpointing, and resume unchanged; a new reconcile-from-event-log consumer lets `resume_run` (and 10D's auto-rollback drain) rebuild run state from the `runtime_events` log independent of which substrate produced the prior steps.

**Tech Stack:** Python 3.13, deepagents/LangGraph deep runtime, `langgraph-checkpoint-postgres` `AsyncPostgresSaver` (psycopg3), Redis lease, async SQLAlchemy/asyncpg, pytest (custom `pytest_pyfunc_call` `asyncio.run` hook — **NO pytest-asyncio**), ruff. **The AsyncPostgresSaver spike only proved the primitive on a minimal `StateGraph` — NOT on `build_deep_agent`-per-step within a DAG. Phase 0 extends the proof or DISPROVES it.**

---

> # ⚠️ ANCHORS @ a5ab52f — RE-VERIFY AT EXECUTION
> 10A and 10B land BEFORE this plan and **mutate the exact seams 10C builds on.** In particular 10A/10B:
> - **create** `src/deep_runtime/thread_identity.py` (`make_thread_id` / `workspace_of_thread_id`) — 10C's autonomous `thread_id` MUST be minted through it (A6 gate). **It does not exist at a5ab52f.**
> - **change** `agent_invoker.py:536` from `thread_id = generate_id("chat")` to `make_thread_id(workspace_id)`, add the resume-side workspace assertion, and touch `_build_delegate_subagents` (A4) + `write_lock`/`step_runner` (A3 `write_lock_require_redis`).
> - **build** the 10B per-surface **effective-runtime gate** (durable manual kill-switch + Redis auto-breaker + static `settings.runtime` fallback — `runtime` cannot hot-change) that 10C's step branch reads keyed `"autonomous"`. **It does not exist at a5ab52f** (only `AGENT_RUNTIME_CALLS` in `metrics_service.py:35/132` + the `settings.runtime` flag exist).
> - **add** `settings.write_lock_require_redis` (A3).
>
> Every `file:line` below was verified against **a5ab52f** by opening the file. RE-VERIFY each at execution — anchors rot across steps (the rebuild's recurring lesson). Where 10A/10B are assumed done, each task carries a **STOP-and-recheck** if the seam is absent.

---

## 0. Context — read before touching code

### 0.1 Where this sits (Step 10 decomposition; ledger `docs/superpowers/plans/2026-07-08-activation-gate-ledger.md`)

| Sub-step | Contents | Flip? |
|---|---|---|
| 10A | Category-A security hardening (thread_identity, write-lock fail-closed opt-in, reaper ws-scope, capability_scope build-assert, …) | No |
| 10B | Cutover control plane: 4 net-new rollback metrics + shadow-compare harness + **per-surface effective-runtime gate** + auto-rollback watcher + escape hatch | No |
| **10C (this plan)** | **Autonomous durable engine** (below): deep step-executor + B9 (saver + `durability="sync"` + lease + reconcile) + B10 autonomous reaper + B11-auto slim. **DAG orchestrator stays.** Spike-first. | **No** |
| 10D | Coordinated live cutover: final review → merge dormant to `main` + CLAUDE.md rewrite → incremental flip chat→perception→autonomous → B7 row-drop migration → retire escape hatch | **Yes** |

**Resolved decisions baked into this plan (do not re-litigate):**
- **Autonomous-executor-on-deep = YES.** The deep middleware chain's ONLY possible live producer is the autonomous path — chat short-circuits `trust_gate` (`direct_user_request`), Perceiver-on-deep is read-only, custom agents are a future §5. If autonomous does not run on deep, `trust_gate`(6B) / deep-`write_lock`(6C) / `read_back`(7C) / `governor_audit`(7A) / `critique`(7B2) are **dead-wired forever.** The spec backs it ("land the gate/collapse/context ONCE on the target runtime"; risk table "autonomous/custom/headless gated BY CONSTRUCTION").
- **DAG orchestrator STAYS.** `graph_executor.execute_run/resume_run` + `dag_runner.execute_dag/execute_step` (dependency ordering, ready-step selection, step-level approval interrupts, checkpointing, resume) remain the Python driver. `build_deep_agent` replaces only the **per-step executor body** (`run_step_via_agent_loop`).
- **B9 = 3-of-4 NET-NEW** on the autonomous path: (a) durable LangGraph checkpointer, (b) single-flight lease, (c) reconcile-from-event-log consumer, (d) `durability="sync"`. Only the Step-1 idempotency ledger (exactly-once) is pre-existing.

### 0.2 Baseline (VERIFY at start of execution)
- Branch `rebuild/first-principles`, off `main`, NOT pushed. HEAD at plan-write time: `a5ab52f`. (10A/10B commits will be on top by execution time.)
- `docker compose up -d postgres redis qdrant`. Infra gotcha: `:6379` may be `hyperlocal-redis` OR `jarvis-redis-1` — either is fine if published. `uv sync --all-extras` (NO pip; plain `uv sync` drops dev extras).
- Full gate: `uv run pytest tests/ --ignore=tests/e2e` → **3292 passed / 18 skipped** at a5ab52f (10A/10B add tests — take the post-10B count as your baseline). A gate with ~108 skipped = redis/postgres DOWN = NOT green; restore infra first.
- `uv run alembic heads` → single `1a2770a28c39`; `uv run alembic check` drift-free; `ruff check src tests` clean.
- **10C expects ZERO migrations.** Checkpointer tables are created by `saver.setup()` (already EXCLUDED from alembic via `alembic/env.py` `include_object`, Step 2); `TaskRunDetail.context_pack` already exists (Step 5). **If any task wants an alembic migration → STOP and re-check** (see the lease task P3 — it MUST be migration-free).
- A live Anthropic key is in `backend/.env` (`JARVIS_USE_BEDROCK=FALSE`). Phase-0 spikes use a **fake streaming/react model** wherever possible; the forced-on offline e2e (P7) uses a fake model. No live model call is required to prove structure.

### 0.3 Test harness conventions (this repo — do NOT assume defaults)
- **NO pytest-asyncio / NO `asyncio_mode`** — a custom `pytest_pyfunc_call` `asyncio.run` hook runs coroutines. Write `async def test_...` directly.
- `make_mock_settings()`, `TEST_USER_ID`, `TEST_WORKSPACE_ID` from `tests/conftest.py`. **MagicMock-truthy hazard:** any NEW bool/string settings field (e.g. a `deep_autonomous_durable` sub-flag, if you add one) MUST be explicitly defaulted in `make_mock_settings` or every `runtime="deep"`/effective-`"autonomous"` test trips it.
- Mock Anthropic via `@patch("src.orchestrator.jarvis.get_anthropic_client")`.
- Real-DB/real-Redis tests are self-contained: `_db_reachable`/`_redis_reachable` guards + NullPool + seed the User→Workspace FK chain (NO `db_session` fixture). **UUID-suffix all Redis keys** (a different project's `hyperlocal-redis` shares `:6379`).
- The AsyncPostgresSaver uses **psycopg3** (`postgresql://…`, strip `+asyncpg`); the app/ledger use asyncpg — both hit the same Postgres, fine. `saver.setup()` creates 4 checkpoint tables (idempotent CREATE-IF-NOT-EXISTS). Re-runnable spike model at `backend/spikes/postgres_saver/probe.py`.
- Do NOT edit `backend/` files while a `uvicorn --reload` worker runs (hangs the HTTP server).

### 0.4 What 10C is NOT
- **No flag flip.** The effective-runtime gate for `"autonomous"` stays `legacy`; no live deep autonomous run. Flag-off ⇒ `run_step_action` calls the **byte-identical** legacy `run_step_via_agent_loop`.
- **No CLAUDE.md edit** — dormant deep internals earn a durable doc edit only at MERGE (10D), per the doc policy / 6B lesson.
- **No real per-connector read-back `read_fn`** — A2's real `read_fn` rides **B4 (10D)**. 10C keeps the autonomous inline read-back seam (`dag_runner._finalize_with_verification`) and leaves the deep `read_back` middleware dormant (`read_fn=None`). See SQ3.
- **No B7 agent-row-drop, no legacy-code deletion** — those are 10D. Legacy `run_step_via_agent_loop` stays as the rollback fallback.

---

## Phase-0 DECISION GATES — the four deferred sub-questions

These are **not placeholders** and **not pre-decided.** Each is resolved by a Phase-0 spike whose outcome **branches the later phases**. Each gate documents its branches so the executor knows what a spike disproving the recommendation means. Record the resolution verbatim in the spike decision docs; P1–P6 cite the resolved branch.

### SQ1 — Durability granularity *(spikes 0.1 + 0.4)*
**Question:** per-step deep-agent thread (each step is its own durably-resumable `build_deep_agent` thread; ledger = exactly-once) **vs** a single DAG-level LangGraph `StateGraph` with one checkpointer.
- **Branch A — per-step deep-agent thread (RECOMMENDED).** Each step gets its own `make_thread_id(workspace_id)` deep-agent thread. The DAG orchestrator (`graph_executor`/`dag_runner`) stays the Python driver: on resume it re-selects ready steps (`get_ready_steps:129` already skips `TERMINAL_SUCCESS`), and each re-picked step's deep-agent thread resumes via `ainvoke(None, cfg)`; the idempotency ledger dedups the replay. **Minimal cut — DAG driver unchanged.** The checkpointer buys *intra-step* durability (a step killed mid-tool-call resumes its own react loop) on top of the DAG's existing *inter-step* durability (`run.checkpoint` + `TaskCheckpoint` rows).
- **Branch B — single DAG-level graph.** Re-express the whole DAG as one LangGraph `StateGraph`. This **replaces `dag_runner`'s Python loop** — categorically larger blast radius, and since `build_deep_agent` is a single react-agent you'd still wrap per-step deep agents inside a custom StateGraph. **Rejected as default**; the spike only falls here if Branch A cannot cleanly resume a per-step thread under the outer Python loop (e.g. an interrupt-paused step the DAG driver cannot re-drive).
- **Gate outcome → phases:** Branch A ⇒ P1 puts the deep-agent build + durable invoke *inside* `run_step_via_deep_agent` (per step); the DAG stays. Branch B ⇒ P1/P2 become a `dag_runner` rewrite and this plan's task breakdown is void — **STOP and escalate** (Branch B is a different plan).

### SQ2 — Gate reconciliation *(spike 0.3)*
**Question:** the `dag_runner` **STEP-level** `TrustEngine` gate (`dag_runner.py:337-346`, pauses the run via DB status + persists an `Approval(run_id, step_id)`, resumed by the scheduler/REST approval path) **vs** the deep **TOOL-CALL-level** `trust_gate` middleware (`interrupt()` + checkpointer, persists an `Approval(thread_id, tool_call_id)`, resumed by `resume_deep_turn`). Once the step-executor is a deep agent with `authorization_source=autonomous`, `is_gated_source("autonomous")==True` ⇒ the deep `trust_gate` **FIRES**, so both would gate → double approval.
- **Branch A — deep `trust_gate` owns approval (interrupt-based) + DAG bridges to run-pause (spec-aligned).** The step's deep-agent thread `interrupt()`s; the checkpointer persists; the DAG driver observes the `GraphInterrupt`, translates it into a run pause (`awaiting_approval`) so the existing scheduler-resume + REST approval infra keeps working, and on approve resumes via `ainvoke(None, cfg)`. Unifies on the finer gate that lands on the target runtime (the spec's intent), but the DAG must **bridge `GraphInterrupt`↔run-status** and the REST resume path must correlate a `(thread_id, tool_call_id)` approval to a `(run_id, step_id)` — real work.
- **Branch B — keep the step-level gate; suppress the deep `trust_gate` for autonomous steps.** Pre-gate at the step level (existing, clean run-pause/resume), and configure the deep chain so it does **not** re-prompt for that step. Because `is_gated_source(autonomous)==True`, this needs either a new provenance/flag ("step already gated") or leaving `trust_gate` dormant for the autonomous executor. **Preserves the entire existing resumable-approval infrastructure**, but risks re-dead-wiring the very gate the cutover exists to activate — acceptable only if the spike shows interrupt-based approval is infeasible inside a DAG-driven per-step deep agent.
- **Branch C — hybrid (RECOMMENDED to spike).** Keep the step-level `TrustEngine` gate as the **coarse pre-step pause** (its DB-status pause/resume is the durable, scheduler-driven mechanism the whole autonomous system relies on), AND run the deep chain's `write_lock`/`governor_audit`/`read_back` on the deep runtime, but make the deep `trust_gate` **not double-gate** the already-approved step (finest reconciliation TBD by the spike — e.g. the deep gate short-circuits when a step-level `Approval` already covers the capability for that run/step). Exercises the deep chain without a second prompt and without a bridge rewrite.
- **Gate outcome → phases:** the spike (0.3) runs a `read → write` plan through a per-step deep agent with `authorization_source=autonomous` and observes whether the deep `trust_gate` double-prompts on top of the `dag_runner` step gate. The chosen branch dictates P1's gate wiring and whether P2 must add a `GraphInterrupt`→run-pause bridge (Branch A only). Record the decision + the exact double-gate observation.

### SQ3 — Read-back unification *(mostly pre-resolved by the A2/B4 dependency; spike 0.3 confirms)*
**Question:** does the deep `read_back` middleware (7C, + a real A2 `read_fn`) replace the autonomous inline seam `dag_runner._finalize_with_verification` (`:552`)?
- **Branch A — keep the inline seam; defer deep read-back unification (RECOMMENDED).** `_finalize_with_verification` is **`TaskStep`-bound**: it drives `finalize_step` (status = `verdict_to_step_status`), world-model reconciliation (`reconcile_verdict`), trust reinforcement, and `_escalate_divergence`. The deep `read_back` middleware operates on tool results, not `TaskStep`, so it **cannot wholesale replace** the seam. Moreover the deep `read_back`'s real `read_fn` is explicitly **A2/B4 (10D)** — building it here is out of scope. 10C keeps the inline seam and leaves deep `read_back` dormant (`read_fn=None`, `deep_readback_enabled=False`).
- **Branch B — unify now.** Blocked on A2's real `read_fn` (B4). Rejected for 10C.
- **Gate outcome → phases:** Branch A ⇒ P1's deep step-executor returns the same step-output dict shape the DAG's `_finalize_with_verification` already consumes; no read-back change. Record in the ledger that read-back unification stays **B4/10D**.

### SQ4 — Provenance wiring *(spike 0.1 exercises it; design-resolved)*
**Question:** where does the autonomous deep-build live, and how is `authorization_source=AUTONOMOUS` supplied?
- **Branch A — reuse `AgentInvoker._build_deep_agent_for(..., authorization_source=AUTONOMOUS)` (RECOMMENDED).** That method (`agent_invoker.py:201`) already assembles the full gated chain (`capability_scope → governor_audit → trust_gate → write_lock → [read_back] → dispatcher`) and is **already** called with `AuthorizationSource.AUTONOMOUS` by the resume seam (`agent_invoker.py:736`). Inject the `AgentInvoker` (or a narrow `deep_step_builder` callable projecting `_build_deep_agent_for`) into `StepRunner`/`GraphExecutor` so the autonomous step-executor uses the **one** build path — DRY with chat, no middleware-chain drift.
- **Branch B — a separate build in `step_runner`.** Duplicates the middleware chain → drift risk. **Rejected.**
- **Gate outcome → phases:** Branch A ⇒ P1 adds a builder dependency to `GraphExecutor.__init__`/`StepRunner.__init__` (via a provider, matching the existing `db_factory_provider`/`active_traces_provider` pattern) and the seam captures the literal `AuthorizationSource.AUTONOMOUS` (never LLM-supplied). `thread_id` minted via `make_thread_id(workspace_id)` (10A). Reuse `src/deep_runtime/authorization.py`.

---

## Verified anchors @ a5ab52f (RE-VERIFY at execution)

| Fact | Location @ a5ab52f | Notes / corrections vs the grounding brief |
|---|---|---|
| Autonomous step executor (legacy) | `step_runner.py` `run_step_action:117` → `run_step_via_agent_loop:238`; `agent_loop(...)` call `:368`; `executor = AGENTS.get("executor")` `:294` | ✔ exact |
| Ephemeral context build (autonomous) | `step_runner.py` `build_step_context:419`, `context_builder.build(...)` call **`:427`** (renders to prompt, NO persist) | ✔ |
| Persisting context builds (autonomous) | `step_graph_store.py` build `:67` → `RunDetailStore.upsert_context_pack` `:78`; `graph_executor.py` build `:449` → `upsert_context_pack` `:454` | ✔ Both persist to `TaskRunDetail.context_pack` **via `RunDetailStore.upsert_context_pack`**. NB `graph_executor.py:449` is the **resume-time stale-context refresh** in `resume_run` (guarded `pause_duration > 1800`), not a fresh build. |
| Context-pack render read contract | `surface_detail_builders/plan.py` `_load_context_pack(db, run)` **`:87`** (reads `ctx["memories"]`, `ctx["entities"]`); `summary.py` **`:103`** (reads `ctx["memories"]`) | ✔ B11-auto slim MUST preserve `memories`/`entities` keys |
| DAG resume + checkpoint-vs-DB CHECK (warns, does NOT reconcile) | `graph_executor.py` `resume_run:422`; mismatch check `:466-476` (WARN only) | ✔ |
| DAG step-level TrustEngine gate | `dag_runner.py` `assess_step_risk` `:337`, `evaluate` `:338`, `approval_required` branch **`:342-346`** | ✔ |
| Inline read-back seam | `dag_runner.py` `_finalize_with_verification:552` (drives `finalize_step`, reconcile, escalation) | ✔ |
| Per-step checkpoint row | `dag_runner.finalize_step` **def `:778`**; `store.checkpoint(...)` call **`:816`** (one `TaskCheckpoint` row per step) | ⚠ correction: `finalize_step` is DEFINED at `:778`; `:816` is the `checkpoint` call inside it. |
| Ready-step selection (skips terminal-success) | `step_graph_store.py` `get_ready_steps:129-159` | ✔ |
| Idempotency ledger (exactly-once, permanent `in_flight`, NO lease/TTL) | model `src/models/idempotency_ledger.py` — UNIQUE `ix_idempotency_ledger_ws_key (workspace_id, identity_key)`, `status` default `in_flight`; service `src/services/idempotency/ledger.py` `reserve`/`_resolve_existing` — `in_flight` → `in_flight_conflict` (fail-closed, never re-fires); wrapper `src/services/idempotency/wrapper.py` `make_idempotent_execute_tool_fn` | ⚠ correction: brief said `idempotency_ledger.py` — the UNIQUE index is in the **model**; the permanent-`in_flight` fail-closed behavior is in the **service** (`ledger.py`). Both confirmed. |
| Only lease-like today | Redis 120s write-lock (`src/services/write_lock.py`, wrapped in `step_runner.make_lock_wrapped_execute_tool_fn:41`); `FOR UPDATE SKIP LOCKED` per-tick row-claim **`src/services/scheduler/run_health_tick.py:156`** | ⚠ path: `run_health_tick.py` is under `src/services/scheduler/`. |
| Reconcile-from-event-log SEAT (only test callers) | `runtime_projection.py` `rebuild_run_projection:275` (folds seq-ordered `runtime_events` into run status). Callers: **only** `tests/test_replay_rebuild_db.py`. Docstring: "the seat Step 10's reconcile-from-event-log builds on." | ✔ genuinely net-new for production use |
| `runtime_events.seq` | `src/models/runtime_event.py` `seq: BigInteger, Identity(always=False)` **`:43-47`**; index `ix_revt_ws_seq (workspace_id, seq)` | ✔ |
| Chat durable saver (chat-only seam) | `src/deep_runtime/checkpointer.py` `build_async_postgres_saver:27` (psycopg3 pool + `saver.setup()`); reaches only chat via `AgentInvoker._checkpointer_provider` | ✔ |
| B10 reaper (CHAT/DEEP-only) | `src/deep_runtime/checkpoint_reaper.py` `reap_thread` + `sweep_decided_approval_checkpoints` (sweeps by `Approval.thread_id`); tick **`src/services/scheduler/checkpoint_reaper_tick.py:27`** hard-returns on `runtime != "deep"` | ⚠ path: tick is under `src/services/scheduler/`. |
| Provenance literal | `src/deep_runtime/authorization.py` `AuthorizationSource.AUTONOMOUS = "autonomous"`; `is_gated_source(autonomous)==True` | ✔ |
| Shared gated deep-build | `agent_invoker.py` `_build_deep_agent_for:201` (params `thread_id`, `authorization_source`); chat call `:550-556` (`DIRECT_USER_REQUEST`, `thread_id=generate_id("chat"):536`); resume call `:727-736` (`AUTONOMOUS`) | ✔ NB: 10A rewrites `:536` to `make_thread_id(workspace_id)`. |
| Zero deep-runtime refs in autonomous files | grep of `graph_executor.py`/`dag_runner.py`/`step_runner.py`/`step_graph_store.py` for `build_deep_agent`/`checkpointer`/`langgraph`/`AsyncPostgresSaver`/`astream`/`MemorySaver` → **NONE** | ✔ clean cut |
| Settings flags | `settings.py` `runtime:str="legacy":172`; `deep_inline_format`/`deep_delegates_enabled`/`deep_readback_enabled`/`deep_context_jit` all `False`; **no** `write_lock_require_redis` yet (10A adds) | ✔ |
| Runtime metric (10B context) | `metrics_service.py` `AGENT_RUNTIME_CALLS:35`, `.labels(runtime=...).inc():132`; used `agent_invoker.py:528` | ✔ no `effective_runtime`/`runtime_gate` yet (10B builds) |

---

## File structure

| File | Change | Phase |
|---|---|---|
| `backend/spikes/deep_autonomous/probe_per_step_durable.py` + `docs/superpowers/spikes/2026-07-10-deep-agent-per-step-durable-resume.md` | **Create** — SQ1/SQ4 spike: `build_deep_agent`-per-step durable resume + ledger exactly-once across crash+replay, ws-bound thread_id | 0.1 |
| `backend/spikes/deep_autonomous/probe_reconcile.py` + doc | **Create** — SQ (reconcile) spike: `rebuild_run_projection` rebuilds run state on resume across substrates | 0.2 |
| `backend/spikes/deep_autonomous/probe_gate_reconcile.py` + doc | **Create** — SQ2/SQ3 spike: double-gate observation + read-back seam interaction | 0.3 |
| decision docs (above) | **Create** — SQ1/SQ2/SQ3/SQ4 resolutions | 0.4 |
| `src/services/step_runner.py` | Add `run_step_via_deep_agent(...)` (+ effective-runtime branch in `run_step_action`); deep builder + checkpointer providers | P1, P2 |
| `src/services/graph_executor.py` | Inject deep builder + checkpointer + lease providers; thread into `StepRunner`; `resume_run` reconcile hook | P1, P2, P3, P4 |
| `src/services/autonomous_lease.py` | **Create** — single-flight lease (Redis `SET NX PX` or SKIP-LOCKED reuse; **NO migration**) | P3 |
| `src/services/run_reconcile.py` | **Create** — reconcile-from-event-log consumer wrapping `rebuild_run_projection`, applies to `TaskRun`/`TaskStep` | P4 |
| `src/services/scheduler/checkpoint_reaper_tick.py` (+ `checkpoint_reaper.py`) | Autonomous durable-checkpoint retention sweep (by run/thread, not `Approval`) | P5 |
| `src/services/step_graph_store.py`, `src/services/graph_executor.py`, `src/services/step_runner.py` | B11-auto: slim the 2 persisting `ContextBuilder.build` callers behind `deep_context_jit` + `jit=(...)`, preserve plan/summary render | P6 |
| worker lifespan wiring (`run.py --worker` / worker service builder) | Build a worker-side `AsyncPostgresSaver` + lease Redis, inject into `GraphExecutor` | P2 |
| `docs/superpowers/plans/2026-07-08-activation-gate-ledger.md` | Mark B9/B10-auto done; annotate B11-auto/read-back deferrals | P8 |

New test files under `tests/deep_runtime/` and `tests/` mirror `src/`.

---

## Phase 0 — SPIKES (offline/forced; can DISPROVE the approach)

> **Gate discipline:** each spike is re-runnable, self-contained (seeds + tears down its own FK chain + checkpoint rows in a `finally`, like `spikes/postgres_saver/probe.py`), and writes a DECISION line to its doc. If a spike DISPROVES the recommended branch, **STOP and escalate** with the probe output before P1 — do not fake the executor around a broken primitive.

### Task 0.1 (SPIKE, SQ1+SQ4): `build_deep_agent`-per-step durable resume + ledger exactly-once
**Files:** create `backend/spikes/deep_autonomous/probe_per_step_durable.py`, `docs/superpowers/spikes/2026-07-10-deep-agent-per-step-durable-resume.md`.

**Goal:** extend the Step-1 minimal-`StateGraph` proof to a **real `build_deep_agent`**: a single step compiled via the shared gated build path (`_build_deep_agent_for`-equivalent, `authorization_source=AUTONOMOUS`), invoked under `AsyncPostgresSaver` with `durability="sync"` on a `make_thread_id(workspace_id)` thread, is killed mid-tool-call, then resumed on the same `thread_id` and fires its external write **exactly once** (the idempotency ledger dedups the mandatory replay).

- [ ] **Step 1:** Build a fake react/chat model (reuse `spikes/deep_stream/probe.py`'s scripted `_astream` fake — turn 1 emits a `tool_call` to a trivial ledger-guarded `write_effect` tool, turn 2 a final text chunk). Compile a deep agent over it with a real `capability_scope` (`db_factory` stub) + a `jarvis_tool_dispatcher` whose `execute_tool` is guarded by the **real** `make_idempotent_execute_tool_fn` (fixed semantic `identity_key`, stable across resume) writing to a `spike_effects` row. `checkpointer=AsyncPostgresSaver(...)` via `build_async_postgres_saver` (reuse `checkpointer.py`). `thread_id = make_thread_id(ws)` (10A helper — **STOP-and-recheck if absent**).
- [ ] **Step 2:** Pass 1 — `await agent.ainvoke(graph_input, cfg, durability="sync")`, and make the tool raise **after** the effect fired + `record_success` but **before** the graph checkpoints (mirror the Step-1 probe's crash point). Pass 2 — `await agent.ainvoke(None, cfg, durability="sync")` on the same `thread_id`. Assert: `spike_effects` count == 1 (exactly-once), the tool body ran twice (replay evidence), `workspace_of_thread_id(thread_id) == ws`, and the checkpoint blobs are `msgpack` (non-pickle).
- [ ] **Step 3 (SQ4):** confirm the build path supplies `authorization_source=AUTONOMOUS` and the gated chain composes with the checkpointer (no exception at compile). Confirm a **read-only** delegate/tool bypasses the ledger.
- [ ] **Step 4:** DECISION line: "per-step `build_deep_agent` durable resume + ledger exactly-once = `<CONFIRMED|DISPROVEN>`; ws-bound thread_id = `<CONFIRMED>`; SQ1 → Branch `<A|B>`." Commit `spike(step10c): per-step deep-agent durable resume + ledger exactly-once (SQ1/SQ4)`.
- [ ] **GATE:** DISPROVEN (a per-step thread cannot resume under the outer driver, or the ledger cannot dedup the deep replay) ⇒ SQ1 falls to Branch B ⇒ **STOP and escalate** (Branch B is a `dag_runner` rewrite = a different plan).

### Task 0.2 (SPIKE, reconcile): `rebuild_run_projection` rebuilds run state on resume across substrates
**Files:** create `backend/spikes/deep_autonomous/probe_reconcile.py` + doc.

**Goal:** prove the reconcile-from-event-log consumer can rebuild a run's `{status, completed_steps}` from the `runtime_events` log ALONE (seq-ordered) after a mid-run kill, **independent of which substrate produced the steps** — the primitive 10D's auto-rollback drain needs (deep in-flight → legacy resume).

- [ ] **Step 1:** Seed a run + steps + a seq-ordered `runtime_events` sequence (`step_started`/`step_completed`/`run_*`) as BOTH the legacy DAG and a deep step would emit them (both go through `SurfaceEmitter.emit_event` → `runtime_events`; confirm the deep step-executor P1 emits the same event types). Call `RuntimeProjectionService.rebuild_run_projection(run_id)`; assert it matches the live `get_active_runs` count.
- [ ] **Step 2:** Simulate a substrate flip: mark the run's steps' `runtime_events` as produced under `deep`, then rebuild and assert a legacy driver could re-pick the correct ready steps (the fold is substrate-agnostic — it reads event types, not checkpoint state).
- [ ] **Step 3:** DECISION line: "reconcile-from-event-log rebuilds run state across substrates = `<CONFIRMED|GAPS>`; net-new consumer wraps `rebuild_run_projection`." Commit `spike(step10c): reconcile-from-event-log cross-substrate rebuild (B9c)`.
- [ ] **GATE:** GAPS (e.g. the deep step-executor does not emit `step_started`/`step_completed` into `runtime_events`) ⇒ P1 must add those emissions so the log stays a faithful system-of-record; record the required emission points.

### Task 0.3 (SPIKE, SQ2+SQ3): double-gate observation + read-back seam interaction
**Files:** create `backend/spikes/deep_autonomous/probe_gate_reconcile.py` + doc.

**Goal:** observe, on a `read → write` plan run through a per-step deep agent with `authorization_source=autonomous`, whether the deep `trust_gate` prompts **on top of** the `dag_runner` step-level gate (double approval), and how the inline `_finalize_with_verification` seam interacts with the deep step output.

- [ ] **Step 1:** Drive a 2-step plan (read then a write capability that the trust matrix would gate) through P1's `run_step_via_deep_agent` (or a spike stand-in) with the `dag_runner` step gate live. Instrument both gates. Observe: does the deep `trust_gate` reach `interrupt()`/persist a second `Approval` after the step gate already paused/approved?
- [ ] **Step 2 (SQ3):** Confirm the deep step-executor returns a step-output dict of the shape `_finalize_with_verification` already consumes (`{"status","result","tools_called","errors"}`, plus `auth_required` passthrough) so read-back finalization is unchanged. Confirm the deep `read_back` middleware stays dormant (`read_fn=None`).
- [ ] **Step 3:** DECISION line: "double-gate observed = `<YES|NO>`; SQ2 → Branch `<A|B|C>`; read-back unification → **DEFER to B4/10D** (SQ3 Branch A)." Commit `spike(step10c): gate reconciliation + read-back seam (SQ2/SQ3)`.
- [ ] **GATE:** if the ONLY safe reconciliation is Branch A (interrupt-based, requires a `GraphInterrupt`→run-pause bridge), P2 grows a bridge task — flag the added scope prominently before continuing.

### Task 0.4 (DECISION synthesis)
- [ ] Record SQ1/SQ2/SQ3/SQ4 resolutions in the three decision docs and cross-link them into this plan's P1–P6 headers ("per SQ2 Branch C, …"). This closes the gates; P1 onward assumes the recorded branches. Commit `docs(step10c): Phase-0 decision-gate resolutions`.

---

## P1 — the autonomous deep step-executor (dormant, alongside legacy)

> Cite the resolved SQ1 (Branch A), SQ2 (chosen branch), SQ4 (Branch A) at execution.

**Files:** `src/services/step_runner.py` (add `run_step_via_deep_agent` + branch), `src/services/graph_executor.py` (inject providers), test `tests/test_step_runner_deep_executor.py`.

- [ ] **Step 1 — Failing test.** With the effective-runtime gate for `"autonomous"` resolving `deep` (mock/inject), `run_step_action` calls `run_step_via_deep_agent` (patch it, assert awaited) and NOT `run_step_via_agent_loop`; with `legacy` (default), it calls `run_step_via_agent_loop` and NOT the deep one, and the yielded step-output dict is **byte-identical shape** to today. A read-only step and a write step both route correctly. Provenance asserted `AuthorizationSource.AUTONOMOUS` (captured at the seam, never from `step.input_data`).
- [ ] **Step 2 — Run → FAIL** (`run_step_via_deep_agent` does not exist; no branch).
- [ ] **Step 3 — Implement.**
  - Add `deep_step_builder_provider` + `checkpointer_provider` to `StepRunner.__init__` (via `GraphExecutor` providers, matching `db_factory_provider`/`active_traces_provider`). The builder is `AgentInvoker._build_deep_agent_for` projected (SQ4 Branch A) — inject the `AgentInvoker` (or a narrow callable) into `GraphExecutor.__init__` and forward.
  - `run_step_action`: branch on the **10B effective-runtime gate keyed `"autonomous"`** (`from src.services.runtime_gate import ...` — **STOP-and-recheck if 10B's gate is absent**; interim fallback: `get_settings().runtime == "deep"` behind the same call site, but DO NOT ship without the gate). When deep → `run_step_via_deep_agent`; else the existing dependency check → `run_step_via_agent_loop`.
  - `run_step_via_deep_agent`: build the same `message`/prior-step-injection/context as the legacy path (reuse the helpers), compile the deep executor via the injected builder (`authorization_source=AUTONOMOUS`, `thread_id=make_thread_id(run.workspace_id)`, per-step scope tools via `build_executor_tools`), invoke under the checkpointer with `durability="sync"`, translate the terminal result into the SAME step-output dict (`status/result/tools_called/errors` + `auth_required` passthrough) `_finalize_with_verification` consumes (SQ3 Branch A). **Emit `step_started`/`step_completed` into `runtime_events`** if not already covered by the DAG (per 0.2 gate).
  - Keep the idempotency ledger + write-lock wrapping (already in the deep chain via `write_lock` middleware; do NOT double-wrap — the deep `jarvis_tool_dispatcher` path already routes through `ToolExecutor.execute_tool`, and the deep `write_lock` middleware + a ledger-guarded dispatcher cover exactly-once; confirm no double-ledger).
- [ ] **Step 4 — Run → PASS.**
- [ ] **Step 5 — Negative control (teeth):** force the effective-runtime gate `"autonomous"`→`deep` and delete the branch → the legacy-only test fails / the deep path is skipped; restore. Also: with gate `legacy`, diff the legacy code path is **untouched** (byte-neutral).
- [ ] **Step 6 — Full gate + ruff. Commit** `feat(step10c): autonomous deep step-executor (run_step_via_deep_agent, provenance=autonomous, dormant behind effective-runtime gate)`.

> **Single-owner-per-file + SYNCHRONOUS dispatch** for `step_runner.py`/`graph_executor.py` (hot files; the 6B F811 / Step-8 P4 lesson).

## P2 — AsyncPostgresSaver wiring on the autonomous path (+ `durability="sync"`, ws-bound thread_id)

**Files:** `src/services/graph_executor.py` (checkpointer provider), worker lifespan wiring, `src/services/step_runner.py` (invoke kwargs), test `tests/deep_runtime/test_autonomous_checkpointer.py` (REAL Postgres, `_db_reachable` + NullPool).

- [ ] **Step 1 — Failing test (real `AsyncPostgresSaver`):** (a) the autonomous checkpointer's `thread_id` embeds workspace (`workspace_of_thread_id(tid)==ws`) and a resume asserts the embedded workspace == run workspace (reuse 10A's `test_checkpointer_workspace_isolation` pattern — the A6 gate for the autonomous checkpointer); (b) a deep step invoked with `durability="sync"` persists a checkpoint that a same-`thread_id` resume continues; (c) cross-ws resume is REFUSED (generic not-found, no existence leak).
- [ ] **Step 2 — Run → FAIL.**
- [ ] **Step 3 — Implement.**
  - Build a **worker-side** `AsyncPostgresSaver` at worker lifespan (reuse `build_async_postgres_saver`; the chat saver reaches only the chat seam via `AgentInvoker`). Inject it into `GraphExecutor.__init__` (new `checkpointer=`/provider); forward to `StepRunner`. `saver.setup()` is idempotent (no migration; alembic-excluded).
  - `run_step_via_deep_agent`: `config = {"configurable": {"thread_id": make_thread_id(run.workspace_id or "")}}`; `await agent.ainvoke(graph_input, config, durability="sync")` (the spike API fact: `durability` is an **ainvoke kwarg**, not a compile arg); resume path `ainvoke(None, config, durability="sync")`.
  - Resume-side workspace assertion in the reconcile/resume path (P4) — reject a `thread_id` whose embedded ws != run ws (defense-in-depth on top of the existing `Approval.workspace_id` guard).
  - **[SQ2 Branch A ONLY]** add the `GraphInterrupt`→run-pause bridge in the DAG driver.
- [ ] **Step 4 — Run → PASS.**
- [ ] **Step 5 — Negative control:** (a) mint the autonomous `thread_id` without the ws prefix → resume refuses; (b) revert the resume-side ws assertion → cross-ws resume test fails. Restore.
- [ ] **Step 6 — Full gate + ruff. Commit** `feat(step10c): durable AsyncPostgresSaver on the autonomous path (durability=sync, ws-bound thread_id)`.

## P3 — single-flight lease (NO migration)

> **STOP-and-recheck (migration):** the lease MUST be migration-free. **Do NOT add a `lease_owner`/`lease_expires_at` column** to `TaskRun` — that is an alembic migration and violates the 10C zero-migration invariant. Use a **Redis lease** (`SET NX PX`, the write-lock idiom) keyed on `run_id`/`thread_id`, or reuse the existing `FOR UPDATE SKIP LOCKED` row-claim (`run_health_tick.py:156`). If a task argues for a DB lease → STOP and escalate.

**Files:** create `src/services/autonomous_lease.py`, wire into `graph_executor.execute_run`/`resume_run`, test `tests/test_autonomous_lease.py` (real Redis, UUID-suffixed keys).

- [ ] **Step 1 — Failing test:** two concurrent `execute_run`/`resume_run` attempts on the same `run_id` — exactly one acquires the lease and drives the durable run; the other backs off (no double-replay of the same `thread_id`). Lease has a TTL (auto-expires so a crashed holder does not deadlock the run — the reaper/next tick re-acquires). Read-only/legacy runs unaffected.
- [ ] **Step 2 — Run → FAIL.**
- [ ] **Step 3 — Implement** `acquire_run_lease(redis, run_id, *, ttl_s)` (context manager, `SET NX PX` + safe release via the value-token pattern, mirroring `write_lock.py`). Gate its use behind the effective-runtime `"autonomous"`==`deep` branch so the legacy path is untouched. Rationale note in the docstring: the lease prevents two scheduler workers (or a resume-reaper + a live resume) from both `ainvoke(None, cfg)`-replaying the same durable thread — the idempotency ledger already makes the EFFECT exactly-once, but the lease avoids wasted concurrent replay + checkpoint contention.
- [ ] **Step 4 — Run → PASS. Step 5 — Negative control** (drop the `NX` → both acquire → double-drive test fails). Restore.
- [ ] **Step 6 — Full gate + ruff. Commit** `feat(step10c): single-flight lease for durable autonomous runs (Redis NX, no migration)`.

## P4 — reconcile-from-event-log consumer (wire into `resume_run`; enables 10D drain)

**Files:** create `src/services/run_reconcile.py`, wire into `graph_executor.resume_run` (replacing the WARN-only `:466-476` check with an actual reconcile on the deep path), test `tests/test_run_reconcile.py` (real DB).

- [ ] **Step 1 — Failing test:** given a run whose `runtime_events` log (seq-ordered) is ahead of / diverges from `TaskRun`/`TaskStep` rows (simulating a crash where the checkpoint-vs-DB mismatch `:466-476` fires), the consumer rebuilds `{status, completed_steps}` from the log (via `rebuild_run_projection`) and **reconciles** the run/step rows so `get_ready_steps` re-picks the correct set — instead of only WARNing. **Cross-substrate:** a run whose prior steps were produced under `deep` reconciles so a `legacy` resume can continue (the 10D auto-rollback drain).
- [ ] **Step 2 — Run → FAIL** (today `resume_run:466-476` only logs a warning).
- [ ] **Step 3 — Implement** `reconcile_run_from_events(db, run)` wrapping `RuntimeProjectionService.rebuild_run_projection` (the seat) + applying it to the state rows via `transition_run`/`transition_step` (NEVER direct mutation) where the log is authoritative and the row is behind. Wire into `resume_run` **on the deep path only** (behind the effective-runtime `"autonomous"`==`deep` branch) so the legacy resume keeps its existing WARN behavior byte-identical. Keep it read-your-writes-safe: reconcile is for the resume boundary, not live control-flow (§4.8 — projections are not execution truth; this consumer applies the log to the truth rows at a resume checkpoint, which is legitimate).
- [ ] **Step 4 — Run → PASS. Step 5 — Negative control:** feed a log that is BEHIND the rows (rows ahead) → reconcile must NOT regress a completed step to pending (log is authoritative only where it is ahead; never downgrade terminal-success). Prove the guard. Restore.
- [ ] **Step 6 — Full gate + ruff. Commit** `feat(step10c): reconcile-from-event-log consumer on durable resume (enables 10D cross-substrate drain)`.

> **10D handoff note (record in ledger):** this consumer is what lets 10D's auto-rollback drain in-flight deep autonomous runs onto legacy — the fold is substrate-agnostic (reads `runtime_events` types, not checkpoint state).

## P5 — B10 autonomous checkpoint reaper

**Files:** `src/services/scheduler/checkpoint_reaper_tick.py` (+ `src/deep_runtime/checkpoint_reaper.py`), test `tests/deep_runtime/test_autonomous_reaper.py`.

- [ ] **Step 1 — Failing test:** `durability="sync"` writes a checkpoint every superstep; the autonomous durable run's checkpoints must be reaped after the run reaches a terminal status (or after a retention window for stranded runs), **scoped by workspace** (reuse 10A NEW-1's ws-scope discipline) and keyed by **run/thread** (NOT `Approval.thread_id` — the existing sweep is chat-approval-centric). A still-running / awaiting-resume run's checkpoints are NEVER reaped.
- [ ] **Step 2 — Run → FAIL** (the existing `sweep_decided_approval_checkpoints` is `Approval`-keyed/chat-only; the tick hard-returns on `runtime != "deep"` at `checkpoint_reaper_tick.py:27`).
- [ ] **Step 3 — Implement** an autonomous sweep (`reap_thread` on the run's `make_thread_id`) gated on effective-runtime `"autonomous"`==`deep` + a reachable durable saver (mirror the tick's 3-way no-op guard). Reap on terminal-run completion (`reap_thread` after `execute_run`/`resume_run` finalizes a terminal status without pausing) + a periodic retention backstop for stranded threads. Never reap a run in a resumable status.
- [ ] **Step 4 — Run → PASS. Step 5 — Negative control** (reap a still-awaiting run → resume test fails). Restore.
- [ ] **Step 6 — Full gate + ruff. Commit** `feat(step10c): autonomous durable-checkpoint reaper (run/thread-keyed, ws-scoped, deep-gated)`.

## P6 — B11-auto: slim the 2 persisting autonomous context builds (preserve plan/summary render)

**Files:** `src/services/step_graph_store.py` (`:67`), `src/services/graph_executor.py` (`:449`), `src/services/step_runner.py` (`:427` ephemeral — slim without persist concern), `tests/test_autonomous_context_slim.py`.

- [ ] **Step 1 — Failing test:** with `deep_context_jit=True` + effective-runtime `"autonomous"`==`deep`, the two **persisting** callers (`step_graph_store.populate_steps:67`→`upsert_context_pack:78`; `graph_executor.resume_run:449`→`upsert_context_pack:454`) build a **slim** pack, but the persisted `TaskRunDetail.context_pack` STILL carries the `memories`/`entities` keys the render contract reads (`surface_detail_builders/plan.py:87` reads `ctx["memories"]`+`ctx["entities"]`; `summary.py:103` reads `ctx["memories"]`). With the flag off (default), the pack is byte-identical to today. The ephemeral caller (`step_runner.build_step_context:427`) slims with no persist contract to preserve.
- [ ] **Step 2 — Run → FAIL.**
- [ ] **Step 3 — Implement** a `jit=(...)` arg on `ContextBuilder.build` (the Step-8 pattern; reuse `JIT_ENABLED_AGENTS` discipline) threaded through the 2 persisters + the 1 ephemeral caller, gated behind `deep_context_jit` + the effective-runtime `"autonomous"` branch. The slim pack MUST retain `memories`/`entities` (possibly reduced counts) so `plan.py`/`summary.py` render non-empty. **Do NOT** create surfaces/detail tabs with empty children.
- [ ] **Step 4 — Run → PASS. Step 5 — Negative control:** slim the pack to DROP `memories` → the plan/summary render test fails (proves the contract is guarded). Restore.
- [ ] **Step 6 — Full gate + ruff. Commit** `feat(step10c): B11-auto slim autonomous context builds behind deep_context_jit (preserves plan/summary render)`.

> **Note:** B11-auto's *live quality validation* (slim-core + JIT retrieval doesn't regress autonomous agent output — a behavior change that cannot be proven byte-identical) is **10D**, not here. 10C proves the render contract + dormant byte-neutrality only.

## P7 — forced-on offline e2e + negative controls

**Files:** `tests/test_autonomous_deep_e2e.py` (fake react model; forced effective-runtime `"autonomous"`==`deep`).

- [ ] **Step 1 — Integration test:** a full autonomous run (a `read → write` plan, background source) executed through `graph_executor.execute_run` with the effective-runtime gate forced to `deep`: the write step runs via `run_step_via_deep_agent`, the deep chain gates per the resolved SQ2 branch, the write fires exactly once (ledger), the durable checkpoint is written + reaped on completion, and the run reaches a terminal status. Kill + `resume_run` mid-run → reconcile-from-event-log + lease → exactly-once preserved. Assert `runtime_events` are emitted so `rebuild_run_projection` matches.
- [ ] **Step 2 — Negative controls WITH TEETH (each a one-line mutation the guard must defeat):**
  - effective-runtime `legacy` ⇒ the run executes the byte-identical legacy `agent_loop` path (deep never called).
  - drop the ws-bound `thread_id` ⇒ cross-ws resume refused.
  - drop the lease `NX` ⇒ double-drive detectable.
  - drop the ledger ⇒ replay double-fires (proves the ledger is load-bearing).
  - reconcile downgrades a terminal step ⇒ guarded.
- [ ] **Step 3 — Full gate + ruff. Commit** `test(step10c): forced-on offline e2e autonomous deep durable run + negative controls`.

## P8 — holistic review + ledger + memory

- [ ] **Step 1 — Full gate:** `uv run pytest tests/ --ignore=tests/e2e` → post-10B baseline + new tests, **18 skipped** (NOT ~108); `uv run alembic check` drift-free, single head `1a2770a28c39` (**ZERO migrations** — the lease is Redis/SKIP-LOCKED, the checkpointer tables are `saver.setup()`/alembic-excluded); `ruff check src tests` clean.
- [ ] **Step 2 — Holistic opus review** that re-runs the gate + INDEPENDENTLY reproduces every negative control (RED → `git checkout` restore → GREEN, tree clean). **security-reviewer** on the A6 autonomous-checkpointer ws-binding (P2) + the gate reconciliation (SQ2). Confirm the **legacy autonomous path is byte-neutral** with the gate off (diff `run_step_via_agent_loop`'s call path).
- [ ] **Step 3 — Ledger update** (`docs/superpowers/plans/2026-07-08-activation-gate-ledger.md`): check off **B9** (saver + `durability="sync"` + lease + reconcile) and the **B10 autonomous** reaper; annotate **B11-auto** as "slim landed dormant; live quality-validate → 10D"; annotate read-back unification (SQ3) as staying **B4/10D**; note the resolved SQ1/SQ2 branches.
- [ ] **Step 4 — Memory:** update `project_first_principles_rebuild.md` + `MEMORY.md` with the "STEP 10C DONE (dormant)" block (commits, gate counts, resolved sub-questions, carries).
- [ ] **Step 5 — NO CLAUDE.md edit** (dormant; the two-execution-paths + durable-autonomous rewrite is 10D at merge).
- [ ] **Step 6 — Commit** `docs(step10c): ledger + memory — autonomous durable engine DONE (dormant)`.

---

## Review strategy
- **Phase 0 spikes:** a single combined review per spike confirming the DECISION line is real (re-run the probe) before the dependent phase builds on it. **0.1 DISPROVEN ⇒ STOP/escalate** (Branch B = different plan).
- **Blast-radius seams** (`step_runner.py` + `graph_executor.py` + `dag_runner.py` — hot files touched by P1/P2/P4): **single-owner-per-file + SYNCHRONOUS (`run_in_background:false`) implementer dispatch** (6B F811 / Step-8 P4 lesson); sequence P1→P2→P3→P4 on the same files. 2-stage PARALLEL spec+quality review on the frozen commit if a reviewer flags cross-touch. The quality reviewer MUST confirm the gate-off legacy path is byte-unchanged.
- **security-reviewer** on P2 (autonomous checkpointer A6 ws-binding) + SQ2 gate reconciliation (tenant-isolation + no-double-gate/no-ungated-write).
- **Per load-bearing guard: a negative control with teeth** (the one-line mutation it must defeat). Full gate at EVERY checkpoint (18 skipped NOT ~108). No FE gate (10C is backend-only; B11-auto's render contract is tested at the builder level).
- Main loop owns verify + commit; confirm reported SHA + gate counts yourself.

## Self-Review (run after drafting, before execution)
1. **Spike-first honored:** Phase 0 (0.1–0.4) can DISPROVE (0.1 → Branch B ⇒ different plan). P1–P6 cite resolved branches, not placeholders. ✓
2. **B9 coverage:** (a) saver P2, (b) lease P3, (c) reconcile P4, (d) `durability="sync"` P2 — all NET-NEW, all mapped. ✓ B10-auto P5, B11-auto P6.
3. **Byte-neutrality:** every change is behind the effective-runtime `"autonomous"`==`deep` branch (or `deep_context_jit`); flag-off ⇒ `run_step_action` → legacy `run_step_via_agent_loop` verbatim. The 4 autonomous files have zero deep refs at a5ab52f — confirm the branch is the ONLY new call edge. ✓
4. **ZERO migrations:** checkpointer tables via `saver.setup()` (alembic-excluded); `TaskRunDetail.context_pack` exists (Step 5); lease is Redis/SKIP-LOCKED (P3 STOP-and-recheck). If any task wants alembic → STOP. ✓
5. **A6 dependency:** autonomous `thread_id` via 10A's `make_thread_id`; resume/reconcile asserts embedded ws (P2/P4). STOP-and-recheck if 10A's helper/10B's gate absent. ✓
6. **Read-back / real read_fn NOT built** (SQ3 Branch A) — deferred B4/10D; inline seam kept. Not a gap. ✓
7. **Anchors:** every file:line verified @ a5ab52f (see table); path corrections applied (`scheduler/` subdir for run_health_tick + checkpoint_reaper_tick; `finalize_step` def `:778` vs checkpoint `:816`). RE-VERIFY at execution (10A/10B mutate the seams). ✓
8. **Type/name consistency:** `run_step_via_deep_agent`, `make_thread_id`/`workspace_of_thread_id`, `acquire_run_lease`, `reconcile_run_from_events`, `AuthorizationSource.AUTONOMOUS`, effective-runtime gate keyed `"autonomous"` — consistent across tasks. ✓

## Execution Handoff
Plan complete. Execution is a LATER session (do NOT execute in the planning run), and **after 10A + 10B land** (they create `thread_identity`, the effective-runtime gate, `write_lock_require_redis`, and mutate `agent_invoker`/`step_runner`). When executed: run Phase 0 spikes first and record the DECISION lines (0.1 can send this whole plan back to escalation); then subagent-driven, single-owner-per-file + SYNCHRONOUS dispatch on the hot files, per-phase review (P2/SQ2 = opus + security-reviewer), full gate at every checkpoint, holistic opus reproducing every negative control. Then update memory + ledger and STOP before 10D. **No flag flip, no CLAUDE.md edit, no migration.**
