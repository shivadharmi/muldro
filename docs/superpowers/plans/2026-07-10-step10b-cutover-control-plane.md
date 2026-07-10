# Step 10B — Cutover Control Plane (Implementation Plan)

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Build the observability + safety control plane the live deep-runtime cutover (10D) will fly on — four net-new rollback-gate metrics, a shadow-compare harness (write-suppressed, spike-first), a per-surface effective-runtime gate, and a one-directional auto-rollback watcher with a manual escape hatch — with **NO flag flip**, everything dormant/observability, byte-neutral on the live `legacy` path.

**Architecture:** The spec requires the rollback metrics to *exist* and the shadow/rollback machinery to be *proven* before any surface flips. 10B builds all of it against the live paths without activating the deep runtime: metrics are pure observation, the shadow harness runs the NON-authoritative engine on a throwaway session with every write hard-suppressed (never a real side effect), the effective-runtime gate resolves to today's static `legacy` when no override keys are set, and the auto-rollback watcher is a no-op while every surface is still `legacy`. This is the **second of four Step-10 sub-plans** (10A security / **10B control-plane** / 10C autonomous-engine / 10D live-cutover). 10C consumes the gate + extends the shadow harness to the autonomous path; 10D flips surfaces behind the gate + watcher this plan builds.

**Tech Stack:** Python 3.13, LangGraph/deepagents deep runtime, `prometheus_client`, Redis (breaker/override state), pytest (custom `asyncio.run` hook — NO pytest-asyncio), ruff.

---

> ## ⚠️ ANCHORS @ `a5ab52f` — RE-VERIFY AT EXECUTION
> Every `file:line` below was confirmed against `a5ab52f` at plan-write time, but anchors rot — re-grep before editing. **Especially re-verify the files 10A mutates** (10A lands BEFORE 10B and shifts these lines): `src/orchestrator/agent_invoker.py` (10A A4 `_build_delegate_subagents`, A6 `thread_id`), `src/deep_runtime/middleware/write_lock.py` (10A A3/A7), `src/services/step_runner.py` (10A A3/A7), `src/config/settings.py` (10A A3 adds `write_lock_require_redis`), and the NEW `src/deep_runtime/thread_identity.py` (10A A6). The Phase-4 seam edit at `agent_invoker.py:528-529` **collides** with 10A A6's `thread_id` mint at `:536` and A4's delegate build — treat `agent_invoker.py` as single-owner-per-file and sequence 10B Phase 4 AFTER 10A is merged.

---

## 0. Context — read before touching code

### 0.1 Where this sits (Step 10 decomposition, resolved with the user 2026-07-10)

Step 10 (autonomous-path runtime cutover — the LAST rebuild step) is split **4 ways** along the build-vs-flip fault line. Everything provable-dormant is built first; only 10D goes live/irreversible.

| Sub-step | Contents | Flip? |
|---|---|---|
| **10A** | Category-A security hardening (A1/A3/A4/A5/A6/A7 + NEW-1/NEW-2; A2 invariant-guard) | No |
| **10B (this plan)** | Cutover control plane: 4 net-new rollback metrics + `shadow_divergence` metric + shadow-compare harness (write-suppressed, spike-first) + per-surface effective-runtime gate + one-directional auto-rollback watcher + escape-hatch kill-switch | No |
| **10C** | Autonomous durable engine: cut the autonomous **step executor** onto `build_deep_agent` (`authorization_source=autonomous`) + B9 (`AsyncPostgresSaver` + single-flight lease + reconcile) + B10 reaper + B11-auto slim. **DAG orchestrator stays.** Extends the shadow harness to autonomous. Spike-first. | No |
| **10D** | Coordinated live cutover: final whole-branch review → **merge dormant to `main`** + CLAUDE.md two-execution-paths rewrite → incremental flip chat→perception→autonomous (clean-week holds, **10B's watcher armed**) → B7 row-drop migration (6→4 agents) → **retire the escape hatch**. | **Yes** |

**Resolved decisions baked into this plan (2026-07-10, one-by-one with the user):**
- **Shadow-compare = live reads + hard-suppressed writes** at the single `ToolExecutor.execute_tool` choke-point, **sampled + async + throwaway session**, **spike-first** (a Phase-0 offline spike can DISPROVE the approach). Chose LIVE reads over replay (replay under-detects tool-choice divergence). Compares READ-ONLY decision outputs; NEVER shadow-runs writes.
- **Per-surface effective-runtime gate** — `settings.runtime` is a plain pydantic field read at process start and CANNOT hot-change, so a gate `effective_runtime(surface)` is the mechanism the flip needs. Priority: manual override → auto-breaker → static `settings.runtime`. Keyed per surface (`chat`/`perception`/`autonomous`). **Resolved ONCE per turn/run.**
- **Auto-rollback = one-directional, fail-safe** — a scheduler tick evaluates 5 signals vs thresholds; on breach trips ONLY the affected surface's breaker to `legacy` (proven-safe direction) with cooldown. Re-enabling `deep` is MANUAL (no auto-flip-back → no flapping).
- **Escape hatch = the manual kill-switch** (deliverable 3's override), forcing a surface (or all) to `legacy`. Retired in 10D after each surface clears its clean week.
- **Kill-switch storage — OPEN DECISION, see Phase 4 box.** Recommendation: Redis-durable override key + the static `settings.runtime` fail-safe fallback → **zero migration**, preserves "a Redis outage never strands a surface on deep" (outage → falls back to static `legacy`). A tiny DB table is the flagged alternative if the manual override must force `legacy` even when static settings say `deep` (not the merge-then-flip model). See Phase 4.

### 0.2 Baseline (VERIFY at start of execution)

- Branch `rebuild/first-principles`, off `main`, NOT pushed. HEAD at plan-write time: `a5ab52f`. **10B executes AFTER 10A is committed** (10A hardens the same seams 10B wires) — rebase/confirm 10A landed first.
- `docker compose up -d postgres redis qdrant`. Infra gotcha: `:6379` may be served by `hyperlocal-redis` OR `jarvis-redis-1` — either is fine if published. `uv sync --all-extras` (NO pip; plain `uv sync` drops dev extras).
- Full gate: `uv run pytest tests/ --ignore=tests/e2e` → **3292 passed / 18 skipped** (+10A's new tests once 10A lands). A gate with ~108 skipped = redis/postgres DOWN = NOT green; restore infra first.
- `uv run alembic heads` → single `1a2770a28c39`; `uv run alembic check` drift-free; `ruff check src tests` clean.
- **10B expects ZERO migrations** under the recommended Redis-override design. IF execution picks the DB-table kill-switch variant, that is the ONE 10B migration and the baseline head moves off `1a2770a28c39` — see Phase 4 and update the expected head in every subsequent gate.
- A live Anthropic key is in `backend/.env` (`JARVIS_USE_BEDROCK=FALSE`); 10B needs no live model call (spike + all tests use fake/mock models). Shadow runs are budget-gated and default-off, so no live cost is incurred by the plan.

### 0.3 Test harness conventions (this repo — do NOT assume defaults)

- **NO pytest-asyncio / NO `asyncio_mode`** — a custom `pytest_pyfunc_call` `asyncio.run` hook runs coroutines. Write `async def test_...` directly.
- `make_mock_settings()`, `TEST_USER_ID`, `TEST_WORKSPACE_ID` from `tests/conftest.py`. **MagicMock-truthy hazard:** every NEW settings field 10B adds (`shadow_sample_rate: float`, the rollback thresholds, `runtime_breaker_cooldown_s`, any override flag) MUST be explicitly defaulted in `make_mock_settings` or a `MagicMock` attribute reads truthy/garbage and silently arms the shadow/gate in unrelated tests.
- Mock Anthropic via `@patch("src.orchestrator.jarvis.get_anthropic_client")`.
- Real-DB/real-Redis tests are self-contained: `_db_reachable`/`_redis_reachable` guards + NullPool + seed the User→Workspace FK chain (NO `db_session` fixture). **UUID-suffix all Redis keys** (a different project's `hyperlocal-redis` shares `:6379`) — this bites hard here because the gate/breaker/override are all Redis-keyed.
- **Prometheus counter tests must reset or read deltas** — `prometheus_client` counters are process-global singletons that persist across tests in one process. Assert on `generate_latest()` substring presence or read the counter's value delta, never an absolute count (another test may have already incremented it).
- Do NOT edit `backend/` files while a `uvicorn --reload` worker runs (hangs the HTTP server).

### 0.4 What 10B is NOT
- **No flag flip** (`JARVIS_RUNTIME` stays `legacy`; no `deep_*` flag flipped; no surface set to `deep`). The gate resolves to today's static `legacy` for every surface until 10D writes an override key in prod.
- **No CLAUDE.md edit** — control-plane machinery is dormant/observability; the two-execution-paths + rollback-runbook doc rewrite is 10D at merge (doc policy / 6B lesson).
- **No autonomous shadow wiring** — 10B wires the shadow at the CHAT seam only. Autonomous shadow needs 10C's deep autonomous executor to shadow against (cross-referenced; the harness is built reusable so 10C only adds a caller).
- **No live verification-FN signal** — the FN proxy metric + sampler scaffold are built now (spec: "the metrics must exist before this step ships"), but the real per-connector `read_fn` rides A2/B4 (10D). Until then the sampler runs with `read_fn=None` → every re-read is UNVERIFIED (dormant, emits no FN). Fully activates in 10D.
- **No B-item check-off** — 10B checks off NO Category-B ledger item (those are the flips, done in 10D). It builds the "behind shadow-compare + auto-rollback + escape hatch" scaffolding named in the Category-B header. B1 (`JARVIS_RUNTIME=deep`) stays unflipped; B12 (native-stream→`surface_update` adapter) is 10D, but the shadow comparator's deep-path decision-capture touches the same phase surface — the boundary is called out in Phase 3.

---

## File Structure

| File | Responsibility | Phase |
|---|---|---|
| `backend/spikes/step10b_shadow/` (new) | Offline shadow spike: write-never-dispatched, mid-loop continuation survives suppression, decision output capturable | 0 |
| `src/services/metrics_service.py` | +5 metrics (double-fire, verification-FN, double-prompt, ungated-perception-write, shadow-divergence) + `MetricsService` methods + a value-read helper for the watcher | 1 |
| `src/services/idempotency/wrapper.py` | Increment double-fire counter at the two existing LOG-ONLY hooks (`:81` already_done, `:84` in_flight_conflict) | 1 |
| `src/orchestrator/shadow_tool_executor.py` (new) | `ShadowToolExecutor` — suppress WRITES (synthetic `{"shadow_suppressed": true}`), delegate READS to the real executor; fail-closed on unknown capability | 2 |
| `src/orchestrator/divergence.py` (new) | `DivergenceComparator.compare(auth, shadow) -> list[Divergence]` (plan/route, gate verdict, read-synthesis, final text, write-intent SET); labels by kind | 3 |
| `src/orchestrator/shadow_runner.py` (new) | `ShadowRunner.maybe_run_shadow(...)` — sample + budget-gate + spawn + capture + compare + emit `shadow_divergence` | 3 |
| `src/orchestrator/agent_invoker.py` | (3) additive `run_shadow_turn(...)` build path; (4) wire the seam `:528-529` to `effective_runtime("chat")` resolved once | 3, 4 |
| `src/orchestrator/chat_processor.py` | Spawn `ShadowRunner.maybe_run_shadow` via `_spawn_background` post-`RunCompleted` (mirror `interaction_learner.learn` at `:624`) | 3 |
| `src/services/runtime_gate.py` (new) | `effective_runtime(surface) -> str` (override → breaker → static), per-turn resolve-once cache | 4 |
| `src/services/runtime_breaker.py` (new) | Surface-keyed breaker + manual override state (Redis), trip/cooldown/read; borrows `AnthropicCircuitBreaker` CLOSED/OPEN/cooldown shape | 4, 5 |
| `src/config/settings.py` | `shadow_sample_rate: float = 0.0`, rollback thresholds, `runtime_breaker_cooldown_s`, override key prefix — all defaulted to today's behavior | 3, 5 |
| `tests/conftest.py` | Default every new settings field (MagicMock-truthy hazard) | 1, 3, 5 |
| `src/services/scheduler/runtime_rollback_tick.py` (new) + `src/services/scheduler/service.py` | Auto-rollback watcher tick mixin + MRO wire into `SchedulerLoop` | 5 |
| `src/api/routes_admin_runtime.py` (new, or a function) | Manual override set/clear (the escape hatch) — `/v1/`-prefixed admin endpoint | 5 |
| `docs/superpowers/plans/2026-07-08-activation-gate-ledger.md` + memory | Record 10B control-plane BUILT (no B-item checked) | 6 |

New test files under `tests/` mirror `src/`; deep-adjacent tests under `tests/deep_runtime/`.

---

## Phase 0 — Shadow-compare offline spike (SPIKE-FIRST — could DISPROVE)

**Why first:** the whole shadow harness rests on three claims that a spike can falsify offline, before any wiring is built (the Step-8 P0 precedent). If claim (b) fails, the "run the whole engine, suppress writes" design is DISPROVEN and the fallback is per-step decision-point comparison — a different Phase 2/3. Do NOT build Phases 2–3 until this spike is green + opus-reviewed.

**Claims to prove (fake model, no infra flip, no live API):**
- **(a) write NEVER reaches real dispatch** under `ShadowToolExecutor`: a write-capability tool call returns the synthetic suppressed shape and the real `ToolExecutor.execute_tool` is never invoked.
- **(b) suppressing a write mid-agent-loop does NOT derail continuation:** a fake deep/legacy loop that (1) calls a read, (2) calls a write (suppressed), (3) continues to a final answer must still produce a coherent final decision — the suppressed write's synthetic result must be a well-formed tool result the agent can reason past. If the agent stalls/errors on the synthetic result → DISPROVEN.
- **(c) the decision output is capturable + comparable:** the loop's plan/route + tool-intent set + final text can be captured into a `ShadowDecision`-shaped structure suitable for diffing.

**Files:** `backend/spikes/step10b_shadow/spike_shadow_suppression.py` (+ a thin `README.md` recording the verdict).

- [ ] **Step 1: Write the spike** — a self-contained script with a FAKE model that emits a scripted tool sequence (read → write → final). Wire a throwaway `ShadowToolExecutor` prototype (write→synthetic, read→real-stub) around a minimal agent loop. Capture the final `ShadowDecision`. Assert (a) the real-dispatch spy count for the write == 0; (b) the loop reaches a final answer; (c) the captured decision has non-empty route + write-intent set + final text.
- [ ] **Step 2: Run the spike** — `uv run python backend/spikes/step10b_shadow/spike_shadow_suppression.py`. Expected: all three claims print PASS.
- [ ] **Step 3: Negative control (teeth)** — flip the write-classification off (treat write as read) → the write reaches the real-dispatch stub → claim (a) FAILS. Restore. This proves the suppression is load-bearing, not incidental.
- [ ] **Step 4: DISPROVE gate** — if claim (b) fails (agent derails on the synthetic result), STOP. Record in the spike README: "whole-engine shadow DISPROVEN — fall back to per-step decision-point comparison." Re-scope Phase 2/3 to compare at each decision point instead of running the full engine. Do not proceed to Phase 2 on a red spike.
- [ ] **Step 5: Independent opus review of the spike** — a reviewer re-runs the spike + the negative control from a clean tree and confirms the verdict. The spike's synthetic-result shape becomes the contract Phase 2 implements.
- [ ] **Step 6: Commit** — `git commit -m "spike(step10b): shadow write-suppression offline spike — <PROVEN|DISPROVEN>"`. Spikes are throwaway; keep the file + README for the audit trail, do NOT wire it into the suite.

---

## Phase 1 — The 4 rollback metrics + `shadow_divergence` metric

**Why:** spec-required — "the metrics must exist before this step ships." Only `AGENT_RUNTIME_CALLS` (`metrics_service.py:35`) exists today; **all four rollback signals + `shadow_divergence` are net-new.** The double-fire signal already has two LOG-ONLY detection points (`wrapper.py:81` `if outcome.already_done`, `:84` `if outcome.in_flight_conflict`) — Phase 1 hooks a counter there (live observability, byte-neutral). The other emitters land in later phases / activate at flip (10C/10D); Phase 1 DEFINES all five metrics + their `MetricsService` methods so the watcher (Phase 5) can read them.

> **Metric-count note (anchor correction):** the task brief said "13 metrics today"; the actual `metrics_service.py` registry at `a5ab52f` holds **18** (10 Counters, 5 Gauges, 3 Histograms). It is still the ONLY metric registry (`routes_metrics.py` exposes it via `MetricsService.generate_metrics()`).

**Files:** `src/services/metrics_service.py`, `src/services/idempotency/wrapper.py`, `tests/test_metrics_rollback_signals.py`.

- [ ] **Step 1: Write the failing test** — assert each of the 5 new metric names appears in `MetricsService.generate_metrics()` after its `record_*` method is called, and that labels resolve:

```python
# tests/test_metrics_rollback_signals.py
from src.services.metrics_service import MetricsService

async def test_rollback_metrics_registered_and_emit():
    MetricsService.record_double_fire(surface="autonomous", kind="already_done")
    MetricsService.record_verification_false_negative(surface="chat")
    MetricsService.record_double_prompt(surface="chat")
    MetricsService.record_ungated_perception_write(surface="perception")
    MetricsService.record_shadow_divergence(kind="write_intent_set")
    body = MetricsService.generate_metrics().decode()
    for name in (
        "jarvis_double_fire_total",
        "jarvis_verification_false_negative_total",
        "jarvis_double_prompt_total",
        "jarvis_ungated_perception_write_total",
        "jarvis_shadow_divergence_total",
    ):
        assert name in body
```

- [ ] **Step 2: Run → FAIL** (methods/metrics don't exist).
- [ ] **Step 3: Implement** — add 5 `Counter`s in `metrics_service.py` (mirror the existing block; label `surface` where sensible, `shadow_divergence` labeled by `kind`, `double_fire` by `surface`+`kind`) + `record_*` staticmethods + a small `read_counter_total(metric, **labels) -> float` helper the watcher (Phase 5) uses to sample values in-process. Wire the double-fire increments in `wrapper.py`:

```python
# wrapper.py — at the existing log-only hooks
if outcome.already_done:                       # :81 today
    MetricsService.record_double_fire(surface="autonomous", kind="already_done")
    logger.info(...)                            # unchanged
    return outcome.result
if outcome.in_flight_conflict:                  # :84 today
    MetricsService.record_double_fire(surface="autonomous", kind="in_flight_conflict")
    logger.warning(...)                          # unchanged
    return {...}
```

- [ ] **Step 4: Run → PASS.**
- [ ] **Step 5: Byte-neutrality check** — the double-fire wiring is on the AUTONOMOUS idempotency path (live today) but is pure observation (a counter inc adjacent to the existing log). Confirm no behavior/return change: the surrounding `return` values are byte-identical. The other 4 metrics have NO live emitter yet (defined-only) — document each metric's activation point in a docstring: `verification_false_negative` → Phase 5 sampler (dormant until real `read_fn`, 10D); `double_prompt` → observation hook at the approval-creation point (byte-neutral, wired if a safe point exists, else 10C/10D); `ungated_perception_write` → provenance wiring (10C); `shadow_divergence` → Phase 3 comparator.
- [ ] **Step 6: Negative control** — rename `jarvis_double_fire_total` in the metric def but not the test → substring assertion FAILS (proves the test binds the real name). Restore.
- [ ] **Step 7: Commit** — `git commit -m "feat(step10b): define 4 rollback-gate metrics + shadow_divergence; wire double-fire counter"`.

---

## Phase 2 — `ShadowToolExecutor` + write-suppression (with teeth)

**Why:** the single write choke-point is `ToolExecutor.execute_tool` (`tool_executor.py:306`) — legacy calls it directly (`agent_invoker.py:611`), deep via `jarvis_tool_dispatcher.py:66`, autonomous via the idempotency/lock-wrapped inner fn (`step_runner.py:41` `make_lock_wrapped_execute_tool_fn`). Rather than a `shadow=` flag mutating that hot file, wrap it: a `ShadowToolExecutor` exposes the same `execute_tool(name, input, user_id, workspace_id)` signature, delegates READS to the real executor, and returns a synthetic suppressed result for WRITES **without calling real dispatch**.

> **Write-classification note (anchor correction):** the task said "reuse … `is_write_capability` … the write-lock already uses." In fact the write-lock (`write_lock.py:51`) and the autonomous lock-wrapper (`step_runner.py:62`) classify with the **pure** `is_read_only_capability(capability)` from `src/integrations/capabilities.py:227` (its NEGATION), NOT `CapabilityResolver.is_write_capability` (that async/DB-backed one lives on `CapabilityResolver`, `capability_resolver.py:94`, used by the idempotency wrapper). For the ShadowToolExecutor reuse the **same shape the write-lock uses**: resolve the tool→capability via `ToolRegistry.get_tool(name).capability` (needs a DB session — mirror `wrapper.py:_resolve_capability_is_write`, or inject a `resolve_capability` callable like `write_lock`/`step_runner` do), then suppress when `not is_read_only_capability(capability)`. **`is_read_only_capability` returns `False` for unknown capabilities → unknown is treated as a WRITE → suppressed = fail-closed** (a shadow never dispatches an unclassified tool as a real write). Bake that into the design + a test.

**Files:** `src/orchestrator/shadow_tool_executor.py` (new), `tests/test_shadow_tool_executor.py`.

- [ ] **Step 1: Write the failing test** — a spy real executor records every `execute_tool` call:

```python
# tests/test_shadow_tool_executor.py
async def test_write_is_suppressed_and_never_reaches_real_dispatch():
    real = SpyToolExecutor()  # records calls; resolve_capability returns write for "gmail_send"
    shadow = ShadowToolExecutor(real, resolve_capability=fake_resolve)  # gmail_send -> "email.send"
    out = await shadow.execute_tool("gmail_send", {"to": "x"}, user_id="u", workspace_id="w")
    assert out.get("shadow_suppressed") is True
    assert real.calls == []                      # real dispatch NEVER invoked for a write

async def test_read_passes_through_to_real_executor():
    real = SpyToolExecutor()
    shadow = ShadowToolExecutor(real, resolve_capability=fake_resolve)  # gmail_search -> "email.search"
    await shadow.execute_tool("gmail_search", {"q": "x"}, user_id="u", workspace_id="w")
    assert real.calls == [("gmail_search", {"q": "x"})]

async def test_unknown_capability_is_suppressed_fail_closed():
    real = SpyToolExecutor()
    shadow = ShadowToolExecutor(real, resolve_capability=lambda n: None)  # unknown
    out = await shadow.execute_tool("mystery_tool", {}, user_id="u", workspace_id="w")
    assert out.get("shadow_suppressed") is True and real.calls == []
```

- [ ] **Step 2: Run → FAIL** (class doesn't exist).
- [ ] **Step 3: Implement** — `ShadowToolExecutor(real_executor, resolve_capability)`: resolve capability; if `capability and is_read_only_capability(capability)` → `return await real_executor.execute_tool(...)`; else → `return {"shadow_suppressed": True, "tool": name, "capability": capability}` (a well-formed dict the agent loop can reason past — matches the Phase-0 spike's synthetic-result contract). Never touch `tool_executor.py` (single-owner hot seam).
- [ ] **Step 4: Run → PASS.**
- [ ] **Step 5: Negative control (teeth)** — remove the write-classification (delegate everything to real) → `test_write_is_suppressed_and_never_reaches_real_dispatch` FAILS because the write reaches real dispatch. Restore. (This is the plan's headline safety guard — the write MUST NOT fire.)
- [ ] **Step 6: Commit** — `git commit -m "feat(step10b): ShadowToolExecutor suppresses writes (fail-closed), passes reads through"`.

---

## Phase 3 — Sampled async shadow wiring at the chat seam + divergence comparator

**Why:** run the NON-authoritative engine alongside the authoritative one and diff READ-ONLY decision outputs. Wired at the CHAT seam (the runtime fork at `agent_invoker.py:529`). Must be SAMPLED (~5%, configurable), ASYNC (isolated `_spawn_background` task — zero user latency, a shadow failure never touches the authoritative turn), THROWAWAY DB session (never committed), and carry NO idempotency reservation.

**Design (keeps `agent_invoker.py` hot-seam churn minimal):**
- `agent_invoker.py` gains an **additive** `run_shadow_turn(agent_name, message, *, user_id, workspace_id, runtime, tool_executor) -> ShadowDecision` — reuses the existing build path with an EXPLICIT `runtime` arg (the opposite of authoritative) and the injected `ShadowToolExecutor` + throwaway session. It does NOT touch the live `:528-529` seam.
- `ShadowRunner.maybe_run_shadow(...)` (new): rolls the sample (`shadow_sample_rate`), checks budget, picks `opposite = "deep" if authoritative=="legacy" else "legacy"`, calls `run_shadow_turn`, then `DivergenceComparator.compare(authoritative_decision, shadow_decision)` and emits `shadow_divergence` per divergence kind.
- `chat_processor.py` captures the authoritative decision (it already collects `presenter_text` / frames) and, after `RunCompleted`, spawns `self._spawn_background(self._shadow_runner.maybe_run_shadow(...))` — **mirror the `interaction_learner.learn` spawn at `chat_processor.py:624`** (same isolation/lifecycle contract).
- **Default `shadow_sample_rate = 0.0` → the spawn is a no-op → byte-neutral/dormant.** Must be defaulted in `make_mock_settings` (MagicMock-truthy hazard: a `MagicMock` rate reads truthy and would fire the shadow in every chat test).

> **Comparator scope + B12 boundary:** compare plan/route, gate verdict, read-synthesis, final text, and the SET of write-INTENTS (the tool+capability the agent WANTED to write — captured, never executed). Do NOT compare `SurfaceUpdate` phases — the deep path has no phase machine until B12's native-stream→`surface_update` adapter (10D), so phase-level comparison is out of 10B scope. Diff the decision, not the transport.

**Files:** `src/orchestrator/divergence.py`, `src/orchestrator/shadow_runner.py`, `src/orchestrator/agent_invoker.py` (additive `run_shadow_turn`), `src/orchestrator/chat_processor.py`, `src/config/settings.py`, `tests/conftest.py`, `tests/test_shadow_runner.py`, `tests/test_divergence_comparator.py`.

### Task 3a — DivergenceComparator (pure, no I/O)

- [ ] **Step 1: Failing test** — `compare` returns `[]` for identical decisions; returns a `Divergence(kind="write_intent_set")` when the write-intent sets differ; a `Divergence(kind="route")` when plan/route differs; `kind="final_text"` when final text differs beyond a normalized-equality check (define the equality: exact after whitespace-normalize, or a documented similarity — decide + test the chosen rule).
- [ ] **Step 2 → 4:** RED → implement a pure `DivergenceComparator.compare(auth: ShadowDecision, shadow: ShadowDecision) -> list[Divergence]` → GREEN. **Step 5:** negative control (make the write-intent diff always return `[]` → the differing-sets test FAILS). **Step 6:** commit `feat(step10b): DivergenceComparator diffs read-only decision outputs`.

### Task 3b — ShadowRunner + seam spawn

- [ ] **Step 1: Failing test** — with `shadow_sample_rate=1.0` + fake models for both runtimes, `maybe_run_shadow` runs the shadow, the comparator detects an injected divergence, and `shadow_divergence` is emitted (assert via `generate_metrics()` delta). Second test: inject a shadow that RAISES → `maybe_run_shadow` swallows it (logged), the authoritative turn's captured output is unchanged, and NO exception propagates (isolation contract). Third test: `shadow_sample_rate=0.0` → `run_shadow_turn` is NEVER called (assert on a spy) → byte-neutral.
- [ ] **Step 2: Run → FAIL.**
- [ ] **Step 3: Implement** — `ShadowRunner` (sample + budget-gate + call `run_shadow_turn` + compare + emit); `agent_invoker.run_shadow_turn` (additive build path with explicit runtime + `ShadowToolExecutor` + throwaway `db_factory` session, no idempotency wrap, no surface push); the `chat_processor` `_spawn_background` spawn after `RunCompleted`; default `shadow_sample_rate=0.0` in `settings.py` + `make_mock_settings`.
- [ ] **Step 4: Run → PASS.**
- [ ] **Step 5: Negative control (teeth)** — remove the try/except isolation in `maybe_run_shadow` → the "shadow raises → authoritative unaffected" test FAILS (proves the isolation is real). Restore.
- [ ] **Step 6: Full gate** — `uv run pytest tests/ --ignore=tests/e2e` (18 skipped NOT ~108). The `chat_processor` spawn touches the live chat path — confirm every existing chat test stays green with `shadow_sample_rate=0.0`.
- [ ] **Step 7: Commit** — `git commit -m "feat(step10b): sampled async shadow-compare at chat seam (default off) + run_shadow_turn"`.

> **Note for 10C:** the autonomous shadow reuses `ShadowRunner`/`ShadowToolExecutor`/`DivergenceComparator` verbatim — 10C only adds a caller that spawns `maybe_run_shadow` from the autonomous step executor once it runs on `build_deep_agent`. Keep `ShadowRunner` runtime-agnostic (no `chat`-specific assumptions in its core).

---

## Phase 4 — Per-surface effective-runtime gate + wire the chat seam

**Why:** `settings.runtime` (`settings.py:172`, default `"legacy"`) is a plain pydantic field read at process start (`self._settings.runtime` at `agent_invoker.py:528-529`) — it CANNOT hot-change. 10D needs to flip surfaces live without a redeploy, and the watcher (Phase 5) needs to trip a surface to `legacy` fast. So replace the seam's direct read with `effective_runtime("chat")` resolving in priority: (1) manual override, (2) Redis auto-breaker, (3) static `settings.runtime`. Keyed per surface (`chat`/`perception`/`autonomous`). **Resolved ONCE per turn** (a mid-turn breaker trip must never switch runtime under a running turn).

> ### 🔑 Kill-switch storage — DESIGN DECISION (recommend the simplest)
> The task's resolved design says "durable MANUAL KILL-SWITCH (a DB row — survives Redis outage)." The constraint invites a migration-free alternative. **Recommendation: zero-migration, Redis override key + static fail-safe fallback.** Rationale: the kill-switch's job is to force the SAFE direction (`legacy`). Under merge-then-flip (10D), `settings.runtime` stays `legacy` in prod and `deep` is only ever activated via a Redis "enable" key — so a full Redis outage makes ALL keys unreadable and `effective_runtime` falls back to static `settings.runtime == "legacy"` = **fail-safe automatically**. A DB row is only strictly needed to force `legacy` while static settings say `deep`, which the flip model never does. So the Redis design satisfies "an outage never strands a surface on deep" **without a migration** — keeping the single-head `1a2770a28c39` baseline (matches the ledger's "B7 is the ONLY Step-10 migration").
> **Flagged alternative:** if the team wants a manual override that survives a full Redis outage AND overrides a static `deep`, add a tiny additive `runtime_override` table (surface PK, target_runtime, reason, set_by, updated_at). That is THE ONE 10B migration — flag it, move the baseline head, update every subsequent gate's expected head. **Raised as the open design question for execution.**
>
> Under the recommended design the `effective_runtime` priority keys (all Redis, all UUID-safe-prefixed, short-TTL cached) are: `jarvis:runtime:override:{surface}` (manual, values `legacy`/absent) → `jarvis:runtime:breaker:{surface}` (watcher-set `legacy`) → `jarvis:runtime:enabled:{surface}` (10D flip → `deep`) → static `settings.runtime`.

**Files:** `src/services/runtime_gate.py` (new), `src/services/runtime_breaker.py` (new — the Redis state read/write shared with Phase 5), `src/orchestrator/agent_invoker.py` (seam wire), `tests/test_runtime_gate.py` (real Redis, `_redis_reachable` guard).

- [ ] **Step 1: Write the failing test** (real Redis, UUID-suffixed keys):

```python
# tests/test_runtime_gate.py — _redis_reachable guard
async def test_no_keys_resolves_to_static_legacy():
    assert await effective_runtime("chat", redis=r, settings=mk(runtime="legacy")) == "legacy"

async def test_enable_key_flips_surface_to_deep():
    await r.set(key("enabled", "chat"), "deep")
    assert await effective_runtime("chat", redis=r, settings=mk(runtime="legacy")) == "deep"

async def test_manual_override_forces_legacy_over_enable():
    await r.set(key("enabled", "chat"), "deep")
    await r.set(key("override", "chat"), "legacy")
    assert await effective_runtime("chat", redis=r, settings=mk()) == "legacy"

async def test_breaker_forces_legacy_over_enable():
    await r.set(key("enabled", "chat"), "deep")
    await r.set(key("breaker", "chat"), "legacy")
    assert await effective_runtime("chat", redis=r, settings=mk()) == "legacy"

async def test_redis_unavailable_falls_back_to_static():
    assert await effective_runtime("chat", redis=None, settings=mk(runtime="legacy")) == "legacy"

async def test_resolved_once_per_turn_is_stable():
    # a mid-turn key flip does not change an already-resolved value
    ...  # resolve once, flip the key, assert the cached turn value is unchanged
```

- [ ] **Step 2: Run → FAIL** (module doesn't exist).
- [ ] **Step 3: Implement** — `runtime_gate.effective_runtime(surface, *, redis, settings)`: read override → breaker → enabled (each a sub-ms Redis GET, wrapped in try/except → on any Redis error fall through to the next tier, ultimately static `settings.runtime`); a per-turn resolve-once cache (resolve at the top of `call_agent_stream`, pass the value down — do NOT re-read mid-turn). `runtime_breaker.py` owns the key names + `trip(surface)` / `clear(surface)` / `state(surface)` so Phase 5 and the gate share one keyspace.
- [ ] **Step 4: Run → PASS.**
- [ ] **Step 5: Wire the seam** (⚠ collides with 10A A6/A4 — single-owner `agent_invoker.py`, sequence after 10A): resolve `runtime = await effective_runtime("chat", ...)` ONCE at the top of `call_agent_stream`, then use it for BOTH the metric label (`AGENT_RUNTIME_CALLS.labels(runtime=runtime)`, was `:528`) and the fork (`if runtime == "deep":`, was `:529`). Confirm the JIT gate at `:522` (`self._settings.runtime == "deep"`) is updated to the resolved value too, or intentionally left static (decide + comment — the JIT flag is deep-only anyway).
- [ ] **Step 6: Byte-neutrality gate** — with no Redis keys + `settings.runtime="legacy"`, `effective_runtime` returns `"legacy"` → the seam behaves identically to today (only added work = tiered Redis GETs, sub-ms, cached once/turn). Run the full gate; confirm every chat/deep test is green and the `AGENT_RUNTIME_CALLS` metric still labels correctly.
- [ ] **Step 7: Negative control (teeth)** — make `effective_runtime` ignore the override tier → `test_manual_override_forces_legacy_over_enable` FAILS. Restore.
- [ ] **Step 8: Commit** — `git commit -m "feat(step10b): per-surface effective-runtime gate (override>breaker>static), chat seam resolves once"`.

---

## Phase 5 — Auto-rollback watcher tick + thresholds + escape-hatch kill-switch

**Why:** a scheduler tick evaluates the 5 signals vs thresholds and, on breach, trips ONLY the affected surface's breaker to `legacy` (proven-safe direction) with cooldown; re-enabling `deep` is MANUAL (no flapping). The escape hatch = the manual override from Phase 4, exposed for an operator to force a surface (or all) to `legacy`.

**Signal → surface map (from the resolved design):** double-fire → `autonomous`; verification-FN → the flipped surface; double-prompt → `chat`; ungated-perception-write → `perception`/`autonomous`; shadow-divergence → all.

> **`AnthropicCircuitBreaker` is NOT repurposable as-is** (`api_circuit_breaker.py` — per-MODEL keyed, no runtime notion). The `runtime_breaker` (Phase 4) borrows its CLOSED/OPEN/cooldown SHAPE but is net-new (surface-keyed, Redis-backed for cross-process). Reuse the shape, not the object.

> **Watcher signal source (accepted limitation / open question):** `prometheus_client` counters are process-LOCAL. The watcher reads deltas from the in-process registry (`MetricsService.read_counter_total` from Phase 1), tracking last-seen counts per tick. In a multi-process deploy the watcher only sees its own process's counts — acceptable for 10B (the metrics are ALSO scraped externally by Prometheus; a production watcher that queries the Prometheus HTTP API is a 10D/ops hardening, noted not built). Flag this in the tick docstring + the ledger.

**Byte-neutrality:** the watcher only trips a surface currently resolving to `deep`. While every surface is `legacy` (default, 10B), it is a no-op — dormant on the live path. Gate it like the checkpoint-reaper tick (`checkpoint_reaper_tick.py:27` — `settings.runtime != "deep"` early-return pattern), but keyed on `effective_runtime(surface)` so it wakes only for a surface actually flipped in prod.

**Files:** `src/services/scheduler/runtime_rollback_tick.py` (new) + `src/services/scheduler/service.py` (MRO wire, mirror `CheckpointReaperTickMixin`), `src/services/runtime_breaker.py` (trip/cooldown), `src/config/settings.py` (thresholds + cooldown, defaulted), `src/api/routes_admin_runtime.py` (new — override set/clear) + route registration, `tests/test_runtime_rollback_watcher.py`, `tests/test_runtime_override_escape_hatch.py`.

### Task 5a — the watcher tick

- [ ] **Step 1: Failing test** — seed the in-process counter for `double_fire` past its threshold + set the `autonomous` surface to `deep` (enable key); run `_tick_runtime_rollback`; assert the `autonomous` breaker key is now `legacy` (tripped) and the cooldown is respected on a second immediate tick (no re-trip churn). Second test: a surface already `legacy` → the tick is a no-op (never writes a breaker key). Third test: one-directional — the watcher NEVER writes an `enabled=deep` or clears a breaker (only humans do).
- [ ] **Step 2: Run → FAIL.**
- [ ] **Step 3: Implement** — `RuntimeRollbackTickMixin._tick_runtime_rollback(factory)`: for each surface currently `deep`, read the mapped signal deltas; on breach call `runtime_breaker.trip(surface)` (sets the Redis breaker key + records `opened_at` for cooldown). Wire the mixin into `SchedulerLoop`'s MRO in `service.py`. Add thresholds + `runtime_breaker_cooldown_s` to `settings.py` + `make_mock_settings`.
- [ ] **Step 4: Run → PASS.**
- [ ] **Step 5: Negative control (teeth)** — swap the signal→surface map (double-fire → `chat` instead of `autonomous`) → the "trips the mapped surface" test FAILS (proves the map is load-bearing). Restore.
- [ ] **Step 6: Commit** — `git commit -m "feat(step10b): one-directional auto-rollback watcher (breach->trip surface to legacy, manual re-enable)"`.

### Task 5b — the escape-hatch kill-switch

- [ ] **Step 1: Failing test** — `set_manual_override("chat", "legacy")` then `effective_runtime("chat")` == `"legacy"` even with `enabled=deep`; `set_manual_override("all", "legacy")` forces every surface to `legacy`; `clear_manual_override("chat")` restores resolution to the enable/breaker/static tiers. Route test: `POST /v1/admin/runtime/override` with `{surface, target}` sets the key (auth/workspace-guard per the repo's admin-route convention).
- [ ] **Step 2 → 4:** RED → implement `set_manual_override`/`clear_manual_override` on `runtime_breaker` + the admin route (register in the app router) → GREEN. **Step 5:** negative control (make `set_manual_override("all", ...)` only set `chat` → the all-surfaces test FAILS). **Step 6:** commit `feat(step10b): manual runtime kill-switch (escape hatch) — force surface(s) to legacy`.

> **Note for 10D:** the escape hatch is RETIRED in 10D after each surface clears its 1-production-clean-week hold. Leave a `# TODO(10D): retire escape hatch` marker on the admin route + a ledger line.

---

## Phase 6 — Holistic review + ledger update + memory

- [ ] **Step 1: Full gate** — `uv run pytest tests/ --ignore=tests/e2e` → 3292 + 10A's tests + 10B's new tests, **18 skipped** (NOT ~108); `ruff check src tests` clean; `uv run alembic check` drift-free. **Head:** `1a2770a28c39` if the Redis-override kill-switch was chosen (recommended); the new head if the DB-table variant was chosen — reconcile with Phase-4's decision.
- [ ] **Step 2: Holistic opus review** — independent reviewer re-runs the gate + INDEPENDENTLY reproduces every negative control (RED → `git checkout` restore → GREEN, tree clean). Security-reviewer pass specifically on: the ShadowToolExecutor write-suppression (a write MUST never reach real dispatch) and the effective-runtime gate fail-safe (Redis error → `legacy`, never accidental `deep`).
- [ ] **Step 3: Ledger update** — in `docs/superpowers/plans/2026-07-08-activation-gate-ledger.md`: NO B-item is checked off (10B builds scaffolding, does not flip). Add a "10B BUILT" note under the Category-B header recording: the 5 rollback metrics exist; shadow-compare harness proven (spike verdict) + wired at chat (default off); effective-runtime gate live-resolvable per surface; auto-rollback watcher armed (dormant while all-legacy); escape hatch present. Annotate B1 (flip) and B12 (native-stream adapter) as 10D. Record the watcher's process-local-counter limitation + the kill-switch storage decision.
- [ ] **Step 4: Memory** — update `project_first_principles_rebuild.md` + `MEMORY.md` with the "STEP 10B DONE" block (commits, gate counts, spike verdict, kill-switch storage decision, carries → 10C shadow reuse / 10D flip).
- [ ] **Step 5: NO CLAUDE.md edit** — control-plane machinery is dormant/observability; the two-execution-paths + rollback-runbook doc rewrite is 10D at merge.
- [ ] **Step 6: Commit** — `git commit -m "docs(step10b): ledger + memory — cutover control plane BUILT (no flip)"`.

---

## Review strategy

- **Phase 0 spike = INDEPENDENT OPUS** — a reviewer re-runs the spike + its negative control from a clean tree and signs off the PROVEN/DISPROVEN verdict before Phases 2–3 are written. A red spike re-scopes the plan.
- **Seam wiring (Phase 3 `chat_processor`/`run_shadow_turn` + Phase 4 `agent_invoker.py:528-529`) = 2-stage PARALLEL spec+quality review on a FROZEN commit** — this is the blast-radius seam 10A A4/A6 also touch. Single-owner-per-file, SYNCHRONOUS (`run_in_background:false`) implementer dispatch, sequence Phase 3 → Phase 4 on `agent_invoker.py`. Security-reviewer on the write-suppression + gate fail-safe.
- **Metrics (Phase 1) + watcher/escape-hatch (Phase 5) = combined single review** (observability + a Redis state machine; lower blast radius).
- **Per-task: a negative control WITH TEETH on every load-bearing guard** — the write-never-dispatched suppression, the override-forces-legacy priority, the shadow-isolation try/except, the signal→surface map. Each is a one-line mutation the guard must defeat (Step-8/9 lesson).
- **Full gate at EVERY checkpoint** (18 skipped NOT ~108). Backend-only — no FE gate.

---

## Self-Review (run after drafting, before execution)

1. **Coverage:** all 5 deliverables mapped? (1) 4 metrics + `shadow_divergence` → Phase 1 ✓; (2) shadow harness → Phase 0 spike + Phase 2 executor + Phase 3 wiring/comparator ✓; (3) effective-runtime gate → Phase 4 ✓; (4) auto-rollback watcher → Phase 5a ✓; (5) escape hatch → Phase 5b ✓.
2. **Byte-neutrality:** every change is observability or default-off. Double-fire metric = pure obs on the live autonomous path; shadow `sample_rate=0.0` default → no spawn; effective-runtime returns static `legacy` with no keys; watcher no-ops while all-legacy. Confirm no live-path behavior change at each phase's byte-neutrality gate.
3. **No flip / no CLAUDE.md:** `JARVIS_RUNTIME` untouched, no `deep_*` flag flipped, no surface set to `deep`; no CLAUDE.md edit.
4. **Migration:** ZERO under the recommended Redis-override kill-switch. The DB-table variant is the flagged single-migration alternative — if chosen, update the baseline head everywhere.
5. **10A collision:** Phase 4 edits the same `agent_invoker.py:528-529` seam 10A A6 (`:536`) + A4 touch; Phase 2/3 reuse the write-classification 10A A3/A7 hardened. Sequence 10B AFTER 10A; single-owner-per-file; RE-VERIFY all anchors (banner).
6. **10C/10D handoff:** `ShadowRunner`/`ShadowToolExecutor`/`DivergenceComparator` kept runtime-agnostic for 10C's autonomous caller; verification-FN sampler dormant until A2/B4 `read_fn` (10D); escape hatch marked for 10D retirement.
7. **MagicMock-truthy:** every new settings field (`shadow_sample_rate`, thresholds, `runtime_breaker_cooldown_s`) defaulted in `make_mock_settings` — else the shadow/gate arms in unrelated tests.
8. **Anchor corrections carried into the plan:** metric count 18 not 13; write-classification is `is_read_only_capability` (pure negation) not `CapabilityResolver.is_write_capability`; `chat_processor.py:624` is the `_spawn_background(` line (`.learn(` at `:625`).

---

## Execution Handoff

Plan complete. Execution is a LATER session (do NOT execute in the planning run), and **only after 10A is committed** (10B wires the seams 10A hardens). When executed: **Phase 0 spike FIRST + opus sign-off** (a red spike re-scopes Phases 2–3 to per-step comparison); then subagent-driven, single-owner-per-file + SYNCHRONOUS dispatch, per-phase review (spike = independent opus; seam wiring = 2-stage parallel spec+quality on a frozen commit; metrics/watcher = combined), full gate at every checkpoint, holistic opus reproducing every negative control. Resolve the kill-switch storage decision (Phase 4 box) before Phase 4. Then update memory + ledger (no B-item checked, no CLAUDE.md edit) and STOP before 10C.
