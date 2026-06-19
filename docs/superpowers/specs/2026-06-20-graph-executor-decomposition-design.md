# GraphExecutor God-Object Decomposition — Design Spec

**Date:** 2026-06-20
**Branch:** `review/architecture-remediation`
**Status:** PROPOSED (awaiting sign-off)
**Target:** `backend/src/services/graph_executor.py` (`GraphExecutor`, ~2,053 lines, ~41 methods)

This follows the recipes proven in the jarvis.py decomposition
(`docs/superpowers/specs/2026-06-19-jarvis-god-object-decomposition-design.md`):
**full composition + dependency injection**, **extract-class-behind-a-facade**, the
**db-factory provider pattern**, **leaf-first extraction order**, and **retargeting
white-box tests** (call-through facades stay; moved-symbol `@patch` targets repoint).

## 1. Problem

`GraphExecutor` is the durable-DAG wrapper around `agent_loop`. One class owns ~41
methods / ~2,053 lines spanning **six unrelated responsibilities** that share one
`self`, one `AsyncSession`, and one namespace:

1. Run lifecycle (create/execute/resume/pause/cancel, trace, timeout, commit)
2. DAG execution engine (the ready-step loop + per-step pipeline)
3. Step-graph persistence (step queries, dependency readiness, reference resolution, checkpoints)
4. Agentic step execution (Operator agent loop, tool building, minimal-Claude fallback)
5. The single TrustEngine approval gate (risk assessment, approval/pause, auto-execute trust feedback)
6. Post-run outcome learning (memory writeback, entity/graph sync, verification, trust penalty)

A prior extraction (**SVC-P1-3**) already split the surface/event cluster into
`SurfaceEmitter` (`execution_surface_emitter.py`); its four `_emit_*`/`_publish_progress`
methods are now thin forwarders. This spec extracts the remaining five clusters.

The core defect is **coupling**, not line count: e.g. the trust gate, the agent-loop
runner, and the outcome learner all mutate the same run/step rows through the same
session, and any test of one concern must construct the entire object graph.

## 2. Goals / Non-Goals

**Goals**
- Decompose into focused, independently testable collaborators with **enforced** boundaries
  (composition + DI, not inheritance/mixins).
- `GraphExecutor` becomes a thin **run-lifecycle coordinator**: public API, trace/cancel
  lifecycle, timeout/error wrapping, commit, plan reconciliation.
- **Zero behavior change.** Every extraction is structure-only, verified against the
  green suite (`2476 passed`) after each step.
- Honor `docs/engineering-standards.md`: one-way deps, typed boundary contracts,
  file-size caps (200–400 target / 800 hard cap), structure/behavior commit separation.

**Non-Goals**
- No functional changes, no new features.
- **No weakening or duplication of the single TrustEngine approval gate** (4×4 matrix;
  risk fails closed to `high`; fail-closed contract guard on missing engine/capability).
- **No direct status mutation** — every moved method keeps using
  `transition_run()`/`transition_step()`.
- **No public API or signature changes.** Callers must keep working unchanged:
  `create_graph_executor(...)` factory (routes_approvals, routes_history,
  background_tasks_tick, perception_runner) and the `GraphExecutor(settings, db, ...)`
  constructor (runtime.py + ~10 test files). Cursor/commit and checkpoint invariants preserved.

## 3. Chosen strategy

- **Pattern:** Full composition (5 new collaborators + the existing `SurfaceEmitter`);
  the coordinator delegates. (Decided over inheritance-split and over a partial extraction
  that leaves the DAG engine in place — the latter cannot reach the 800-line cap.)
- **Safety net:** Rely on the **existing extensive white-box suite** (the approval gate,
  trust feedback, durability, resume, and surface paths are already deeply pinned), plus
  a focused unit test per new collaborator. No separate characterization layer.

## 4. Key findings from dependency mapping

1. **Heavy white-box test coupling (the analog of jarvis's AgentInvoker finding).**
   12 private methods are called *directly* by tests and 5 module-level symbols are
   `@patch`-ed:

   | Symbol patched in tests | Count | Disposition |
   |---|---|---|
   | `get_anthropic_client` | 23 | **Stays** in `graph_executor` (`__init__` builds the client) — patches never move |
   | `transition_run` | 9 | Retarget per owning module (some stay on coordinator, some move) |
   | `transition_step` | 8 | Retarget per owning module |
   | `get_or_assess_risk` | 6 | Moves with the trust gate → retarget to `trust_gate` |
   | `create_graph_executor` | 5 | **Stays** (factory unchanged) |

   Direct call-through methods (must keep delegating facades on `GraphExecutor`):
   `_execute_step`, `_run_step_action`, `_run_step_via_agent_loop`, `_run_verification`,
   `_record_auto_execution_outcome`, `_remember_auto_executed`, `_writeback_memories`,
   `_learn_entities_isolated`, `_handle_step_failure`, `_checkpoint`, `_populate_steps`,
   `_build_graph_definition`.

   → Recipe: **keep the facades** (call-through tests stay green untouched);
   **retarget only the moved-symbol patches** for clusters that move.

2. **Module-helper import cycle.** `_compute_retry_delay`, `_safe_error_fields`,
   `_step_to_state` are used by both the coordinator and the to-be-extracted `DagRunner`.
   Leaving them in `graph_executor.py` would force `dag_runner.py` to import *up* from
   `graph_executor.py` while the coordinator imports `dag_runner.py` down — a cycle.
   → **Resolution:** move the three helpers to a shared leaf `execution_support.py`
   first; `graph_executor.py` re-exports them (`from ... import _step_to_state ...`) so
   any existing `from src.services.graph_executor import _step_to_state` keeps working.

3. **`_active_traces` is shared across the seam.** Only `StepRunner`
   (`_run_step_via_agent_loop`) reads it (`self._active_traces.get(run_id)`); the
   coordinator owns its lifecycle (created in `execute_run`/`resume_run`, popped in
   `_finalize_trace`). → Inject an **`active_traces_provider`** (zero-arg callable
   returning the live dict), mirroring the `db_factory` provider pattern, so the
   coordinator stays the single source of truth.

4. **Background spawning stays centralized.** `OutcomeLearner._writeback_memories`
   fire-and-forgets entity learning. Inject `spawn_background: Callable` from the
   coordinator; `_background_tasks` stays coordinator-owned.

5. **Verifier presence drives a branch in the DAG loop.** `_execute_dag` checks
   `if self._verifier` to choose `partially_completed` vs `completed`. → `OutcomeLearner`
   owns the verifier and exposes a `verification_enabled` bool; `DagRunner` reads that
   instead of holding the verifier itself.

## 5. Target architecture — 5 collaborators + existing SurfaceEmitter + thin coordinator

All dependency arrows point downward (no cycles):

```
GraphExecutor (run-lifecycle coordinator)
  owns: _db, _client, _audit, _active_traces, _cancel_events, _background_tasks,
        _trace_store, _settings; wires all collaborators; keeps public API + facades.
  exposes: create_run, populate_run_steps, execute_run, resume_run, pause_run,
           cancel_run  (+ call-through facades, surface forwarders)
        │
        ├── DagRunner          ──► StepGraphStore, StepRunner, TrustGate,
        │     (_execute_dag,        OutcomeLearner, SurfaceEmitter  (+ _db)
        │      _execute_step,
        │      _finalize_step,
        │      _handle_step_failure)
        ├── TrustGate          ──► StepGraphStore (checkpoint), SurfaceEmitter
        │     (_assess_step_risk, _create_approval_and_pause, _notify_auto_executed,
        │      _record_auto_execution_outcome, _remember_auto_executed)
        ├── StepRunner         ──► StepGraphStore (get_all_steps), SurfaceEmitter
        │     (_run_step_action, _minimal_claude_action, _build_operator_tools,
        │      _run_step_via_agent_loop, _build_step_context)
        ├── OutcomeLearner     ──► StepGraphStore (get_all_steps), SurfaceEmitter(n/a)
        │     (_writeback_memories, _learn_entities_*, _extract_and_sync_entities,
        │      _completed_all_knowledge_routed, _run_verification,
        │      _record_verification_trust_penalty)
        ├── SurfaceEmitter     ──► (existing leaf; unchanged)
        ├── StepGraphStore     ──► (leaf, _db + _context_builder only)
        │     (_get_all_steps, _get_ready_steps, _resolve_step_references,
        │      _checkpoint, _build_graph_definition, _populate_steps)
        └── execution_support  ──► (leaf, pure functions)
              (_compute_retry_delay, _safe_error_fields, _step_to_state)
```

### Collaborator responsibilities (methods moved verbatim where possible)

| Collaborator | New file | Methods moved | Depends on |
|---|---|---|---|
| `execution_support` (module) | `execution_support.py` | `_compute_retry_delay`, `_safe_error_fields`, `_step_to_state` | — |
| `StepGraphStore` | `step_graph_store.py` | `_populate_steps`, `_build_graph_definition`, `_get_all_steps`, `_get_ready_steps`, `_resolve_step_references`, `_checkpoint` | `_db`, `_context_builder`, `execution_support` |
| `StepRunner` | `step_runner.py` | `_run_step_action`, `_minimal_claude_action`, `_build_operator_tools`, `_run_step_via_agent_loop`, `_build_step_context` | `StepGraphStore`, `SurfaceEmitter`, client/tool_registry/context_builder/db_factory/execute_tool_fn/budget/circuit_breaker/settings, `active_traces_provider` |
| `TrustGate` | `trust_gate.py` | `_assess_step_risk`, `_create_approval_and_pause`, `_notify_auto_executed`, `_record_auto_execution_outcome`, `_remember_auto_executed` | `StepGraphStore` (checkpoint), `SurfaceEmitter`, trust_engine/client/redis/db/notifier |
| `OutcomeLearner` | `outcome_learner.py` | `_writeback_memories`, `_completed_all_knowledge_routed`, `_learn_entities_from_outcome`, `_learn_entities_isolated`, `_extract_and_sync_entities`, `_run_verification`, `_record_verification_trust_penalty` | `StepGraphStore` (get_all_steps), memory_service/world_model/verifier/db/db_factory/settings, `spawn_background` |
| `DagRunner` | `dag_runner.py` | `_execute_dag`, `_execute_step`, `_finalize_step`, `_handle_step_failure` | `StepGraphStore`, `StepRunner`, `TrustGate`, `OutcomeLearner`, `SurfaceEmitter`, `_db`, `execution_support` |

### Stays on the coordinator (`GraphExecutor`)
- Public API: `create_run`, `populate_run_steps`, `execute_run`, `resume_run`,
  `pause_run`, `cancel_run`.
- Lifecycle/orchestration: `__init__` (wires collaborators), `_finalize_trace`,
  `_reconcile_plan_status` (+ `_RUN_STATUS_TO_PLAN_STATUS`), `_spawn_background`.
- Owned resources: `_db`, `_client`, `_audit`, `_active_traces`, `_cancel_events`,
  `_background_tasks`, `_trace_store`, `_settings`, `_surface_emitter`.
- **Delegating facades** for every call-through method above (so the white-box suite
  keeps passing unchanged), plus the existing `_emit_*` surface forwarders.

## 6. Provider / injection decisions (resolve coupling)

1. **db-factory provider** — collaborators take `db_factory` exactly as the executor
   receives it today (the constructor passes `db_factory` straight through; tests reassign
   `executor._db_factory` rarely here, so a plain pass-through matches current behavior).
   *(If any test reassigns `_db_factory` post-construction, switch that injection to the
   zero-arg provider used in jarvis. Verified during step execution.)*
2. **active_traces_provider** — `StepRunner` gets `lambda: self._active_traces`; the
   coordinator owns the dict and its lifecycle.
3. **spawn_background callable** — `OutcomeLearner` gets `self._spawn_background`;
   `_background_tasks` stays coordinator-owned.
4. **verification_enabled** — `OutcomeLearner.verification_enabled` (bool over its
   `_verifier`) is read by `DagRunner` for the `partially_completed`/`completed` branch.
5. **Typed boundaries** — cross-collaborator deps are passed as concrete collaborator
   objects (constructor injection); only `db_factory`, `active_traces_provider`, and
   `spawn_background` are callables (lifecycle primitives).

## 7. Extraction order (leaves first → fewest downstream breaks)

Each numbered step is its own **structure-only** commit (`refactor:`); the full non-e2e
suite (`pytest tests/ -q -p no:warnings --ignore=tests/e2e`, baseline **2476 passed**)
must be green before the next.

0. `execution_support.py` — move the 3 module helpers; re-export from `graph_executor`.
1. `StepGraphStore` (leaf) — step DAG build/query/checkpoint.
2. `StepRunner` (needs #1) — agentic step execution.
3. `TrustGate` (needs #1) — the single approval gate cluster. *(Retarget `get_or_assess_risk`
   patches + the trust-flow `transition_*` patches.)*
4. `OutcomeLearner` (needs #1) — memory/entity/verification. *(Retarget verification-path
   `transition_run` patches.)*
5. `DagRunner` (needs #1–#4 + SurfaceEmitter) — the loop + per-step pipeline. *(Retarget
   the step-pipeline `transition_*` patches.)*
6. Coordinator slim-down: confirm facades + surface forwarders only; final full-suite run;
   `ruff check` + `ruff format` clean; verify `graph_executor.py` < 800 lines.

For the large mechanical moves (#3, #5) a subagent may be given a precise rewrite map,
then verified here (full suite + grep for un-rewritten cross-cluster `self._`-calls +
diff review) before committing.

## 8. Testing strategy

- After each extraction: full suite green; existing tests pass **unchanged except for the
  documented patch-target retargets** (call-through facades absorb everything else).
- A focused unit test per new collaborator (constructible with a small mocked dep set),
  mirroring `test_execution_surface_emitter.py`.
- The single-approval-gate invariants (`test_single_approval_gate.py`,
  `test_trust_unification.py`, `test_trust_feedback.py`) must pass after retargeting —
  this is the primary guarantee that the gate semantics are byte-for-byte preserved.

## 9. Risks & mitigations

| Risk | Mitigation |
|---|---|
| Trust-gate semantics altered when moved | Methods moved verbatim; `get_or_assess_risk`/`transition_*` patches retargeted; gate-specific tests must stay green |
| Import cycle (`dag_runner` ↔ `graph_executor`) | Module helpers extracted to `execution_support.py` leaf first (Finding 2) |
| Stale `_active_traces` in `StepRunner` | `active_traces_provider` returns the coordinator's live dict |
| `transition_*` patch misses after a method moves | Per-module retarget table tracked; call-through facade names unchanged |
| Over-large `DagRunner` | ~440 lines: over the 400 target, under the 800 cap, cohesive (the execution engine). Acceptable; revisit only if it grows |
| Hidden behavior change in `_execute_step` gate flow | Public API + facade names unchanged; structure-only commits; suite green per step |

## 10. Success criteria

- `graph_executor.py` reduced to a coordinator **< 800 lines** (target ~740).
- 5 new focused modules (+ the `execution_support` leaf), each within/near the 200–400 band
  (`DagRunner` ~440 is the one cohesive exception).
- Full suite green; existing tests pass with only documented patch-target retargets.
- No public API / factory / constructor change; cursor/commit + checkpoint invariants intact;
  single TrustEngine gate semantics unchanged. `ruff` clean.
- Dependency graph acyclic and one-way (verified by imports).
