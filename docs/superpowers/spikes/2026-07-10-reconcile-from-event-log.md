# Spike 0.2 — reconcile-from-event-log (B9c primitive)

**Step 10C, Phase 0.2** · 2026-07-10 · SPIKE (throwaway, not production)
Probe: `backend/spikes/deep_autonomous/probe_reconcile.py`
Run: `uv run python -m spikes.deep_autonomous.probe_reconcile` → exit 0

## Question

Can the reconcile-from-event-log consumer rebuild a run's `{status, completed_steps}`
from the `runtime_events` log ALONE (seq-ordered) after a mid-run kill, INDEPENDENT
of which substrate produced the steps (legacy DAG vs P1 deep step-executor)? This is
the primitive 10D's auto-rollback drain needs to bring in-flight deep autonomous runs
back onto legacy.

## Finding

The consumer seat already exists: `RuntimeProjectionService.rebuild_run_projection(run_id)`
(`src/services/runtime_projection.py`) folds the seq-ordered `runtime_events` for a run
into `{status, total_steps, completed_steps, progress_pct}`. The fold reads ONLY the
event `type` (+ payload `status`), never any checkpoint or substrate state — so it is
**substrate-blind by construction**. A net-new 10D consumer simply WRAPS this method;
nothing new needs to be folded.

Proven against a real Postgres (verbatim final block):

```
STEP 10C PHASE 0.2 — reconcile-from-event-log (B9c)
REBUILD_MATCHES_LIVE=True
SUBSTRATE_AGNOSTIC=True
EXECUTOR_EMITS_NOTHING_STILL_REBUILDS=True
REQUIRED_EVENT_TYPES=['step_started', 'tool_call_started', 'step_completed', 'run_completed', 'run_failed', 'run_cancelled']
  step_started         EMITTED_FROM=shared-dag      (dag_runner.py:353,447 execute_step ('step.started'->'step_started'))
  tool_call_started    EMITTED_FROM=executor-entry  (step_runner.py:159 run_step_action (pre-branch; redundant w/ step_started))
  step_completed       EMITTED_FROM=shared-dag      (dag_runner.py:819 finalize_step)
  run_completed        EMITTED_FROM=shared-dag      (dag_runner.py:137 execute_dag (durable=True))
  run_failed           EMITTED_FROM=shared-dag      (graph_executor.py:395 coordinator except handler)
  run_cancelled        EMITTED_FROM=shared-dag      (graph_executor.py:601 cancel_run)
DEEP_EXECUTOR_EMISSION_GAP=NONE
RESULT=CONFIRMED
```

- **Step 1** — rebuild of a mid-run run from the exact runtime_events the live legacy
  DAG writes matches the live `get_active_runs()` read (`total=2, completed=1, pct=50`).
- **Step 2** — the SAME terminal sequence seeded twice, tagged `substrate="legacy"` vs
  `substrate="deep"` in payload, rebuilds byte-identically (`("completed",2,2,100)`).
  `RuntimeEvent` has no substrate column; the marker rides in payload and the fold
  ignores it — substrate-blindness proven the strong way.
- **Step 3 (the GATE)** — a run seeded with ONLY the shared-DAG event types
  (`step_started`, `step_completed`, `run_completed`) and NO `tool_call_started`
  (simulating a deep step-executor that emits nothing of its own) STILL rebuilds
  correctly. The DAG driver that wraps every step covers the whole fold.

## The GATE (what P1's deep step-executor must emit)

For each event type the fold needs, the LIVE emission point is:

| event type          | fold role            | emitted from                                                              |
|---------------------|----------------------|---------------------------------------------------------------------------|
| `step_started`      | total_steps (`started` set) | **shared-dag** — `dag_runner.py:353,447` `execute_step` (SurfaceEmitter normalizes `step.started`→`step_started` at `execution_surface_emitter.py:89`) |
| `tool_call_started` | total_steps (`started` set) | executor-entry — `step_runner.py:159` `run_step_action`, BEFORE the agent-loop-vs-deep branch; REDUNDANT with `step_started` (same step_id into a set) |
| `step_completed`    | completed_steps      | **shared-dag** — `dag_runner.py:819` `finalize_step`                       |
| `run_completed`     | status               | **shared-dag** — `dag_runner.py:137` `execute_dag` (`durable=True`)        |
| `run_failed`        | status               | **shared-dag** — `graph_executor.py:395` coordinator `except` handler      |
| `run_cancelled`     | status               | **shared-dag** — `graph_executor.py:601` `cancel_run`                       |

**NONE is emitted only from the legacy-executor body** (`run_step_via_agent_loop`, the
method the P1 deep path replaces via `run_step_via_deep_agent`). The DagRunner
(`execute_step` → `run_step_action` → `finalize_step`; `execute_dag` closes the run) and
the GraphExecutor coordinator STAY shared across both substrates and emit every
fold-required type AROUND the executor call. The deep step-executor need emit NOTHING
for reconcile-from-event-log to rebuild its runs.

The only executor-adjacent emission is `tool_call_started` from `StepRunner.run_step_action`
— and (a) it fires at the shared entry BEFORE the branch to legacy-vs-deep, and (b) it is
redundant with the shared `step_started` (both add the same step_id to the fold's `started`
set). So it is not a gap even if the deep executor changes nothing there.

## Adjacent nuances (pre-existing, NOT substrate gaps)

- The DagRunner **failed-branch** (`dag_runner.py:174`, "some steps failed but the DAG
  loop ended normally") does `transition_run(run, "failed")` WITHOUT emitting a
  `run_failed` runtime_event. `run_failed` is only written when the DAG **raises** into
  the coordinator's `except` (`graph_executor.py:395`). So a run that ends `failed` via
  the failed-branch has no run-terminal event → the fold's `status` is `None`. This is a
  pre-existing fold limitation that affects BOTH substrates equally; the 10D drain should
  treat a `None` reconciled status (with completed<total) as "incomplete, re-pick ready
  steps on legacy", not as "unknown". Flagged for P1/10D, not introduced here.
- `routes_realtime.py`'s big event-type set is an SSE **filter** list, not emission
  points — disregarded.

## DECISION

`DECISION: reconcile-from-event-log rebuilds run state across substrates = CONFIRMED; net-new consumer wraps rebuild_run_projection; required event types = [step_started|tool_call_started, step_completed, run_completed|run_failed|run_cancelled]; deep-executor emission gap = NONE (all fold-required types are emitted from the shared DagRunner/GraphExecutor coordinator that wraps both substrates; run_step_via_deep_agent need emit nothing).`
