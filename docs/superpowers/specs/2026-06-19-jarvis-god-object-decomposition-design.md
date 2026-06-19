# Jarvis God-Object Decomposition — Design Spec

**Date:** 2026-06-19
**Branch:** `review/architecture-remediation`
**Status:** Design — awaiting implementation plan
**Target:** `backend/src/orchestrator/jarvis.py` (`JarvisOrchestrator`, ~3,269 lines)

## 1. Problem

`JarvisOrchestrator` is a god object: one class, ~45 methods, ~3,269 lines (next-largest file
in `orchestrator/` is `agent_loop.py` at 857). Despite prior extractions (`chat_pipeline.py`,
`core_events.py`, `system_capability_handler.py`, `perception.py`, `presenter_skip.py`), the
class still owns **eight unrelated responsibilities** that share one `self` and one namespace:

1. Lifecycle & wiring
2. Chat message processing
3. Perception loop (~600 lines)
4. Context assembly
5. A2UI surface push
6. Event bus / runtime events
7. Tool building & execution
8. Plan/interaction persistence + briefing

The core defect is **coupling**, not line count: background perception and synchronous chat
never share a call stack yet share all fields, so nothing prevents one from reading/mutating
the other's state. Tests must construct the entire object graph to exercise any single concern.

## 2. Goals / Non-Goals

**Goals**
- Decompose into focused, independently testable collaborators with **enforced** boundaries
  (composition + dependency injection, not inheritance/mixins).
- `JarvisOrchestrator` becomes a thin coordinator: wiring, lifecycle, public API.
- **Zero behavior change.** Every extraction is structure-only, verified against a green suite.
- Honor `docs/engineering-standards.md`: one-way deps, typed boundary contracts, file-size caps
  (200–400 target / 800 hard cap), structure/behavior commit separation, characterization tests.

**Non-Goals**
- No functional changes, no new features, no API surface changes.
- No re-litigation of the chat path's ungated-by-design behavior (CLAUDE.md "Two execution paths").
- No public method signature changes on `JarvisOrchestrator` (callers: api routes, scheduler,
  auth routes, tests must keep working unchanged).

## 3. Chosen strategy

- **Pattern:** Full composition (#1). Every cluster extracted into its own injected collaborator;
  the orchestrator delegates. Strongest boundaries, most testable. (Decided over inheritance-split,
  which only relocates code without reducing coupling, and over a hybrid that leaves the core intact.)
- **Safety net:** **Characterization tests first.** Before extraction, pin current observable
  behavior of the chat path, perception cycle, and surface push; extract against green.

## 4. Key finding from dependency mapping

The agent-invocation primitive (`_call_agent`, `_call_agent_stream`, `_get_model_for_agent`,
`_build_system_prompt`, `_apply_cache_control_to_tools`) is called by **both** chat
(`_process_core` → `_call_agent_stream`) **and** perception (`run_perception_cycle` /
`run_cross_source_synthesis` → `_call_agent`). If `_call_agent` lived inside "ChatProcessor",
perception would depend on chat — a backwards cycle.

The reverse edge (chat → perception) is only `_bump_perception_for_sources`, which is a one-liner
delegating to `PerceptionPolicyService.request_run()` — it does **not** need the perception runner.

**Resolution:** extract the agent-invocation engine as its own collaborator (`AgentInvoker`).
Both chat and perception then depend *downward* on it, and chat receives only
`PerceptionPolicyService` for the bump. The cycle dissolves into a clean DAG.

## 5. Target architecture — 8 collaborators + thin coordinator

All dependency arrows point downward (no cycles):

```
JarvisOrchestrator (coordinator)
  owns: _agents, _budget, _circuit_breaker, _background_tasks/_spawn_background,
        _trace_manager, _interaction_learner, _client
  exposes: process_message / _events / _stream, run_perception_cycle,
           run_cross_source_synthesis, generate_briefing, get_budget_status,
           get_system_health, shutdown, load_agents_from_db
        │
        ├── ChatProcessor          ──► AgentInvoker, ContextAssembler, PlanStore,
        │                              SurfacePusher, EventPublisher,
        │                              SystemCapabilityHandler, PerceptionPolicyService(bump)
        ├── PerceptionRunner       ──► AgentInvoker, EventPublisher, SurfacePusher,
        │                              PlanStore, SystemCapabilityHandler, PerceptionPolicyService
        ├── AgentInvoker           ──► ToolExecutor, ContextAssembler
        ├── ToolExecutor           ──► EventPublisher
        ├── SurfacePusher          ──► EventPublisher
        ├── ContextAssembler       ──► (leaf)
        ├── PlanStore              ──► (leaf, db only)
        └── EventPublisher         ──► (leaf)
```

### Collaborator responsibilities (methods moved verbatim where possible)

| Collaborator | New file | Methods moved | Depends on |
|---|---|---|---|
| `EventPublisher` | `event_publisher.py` | `_ensure_event_bus`, `_publish_event`, `_emit_runtime_event` (+ owns `_event_bus`, `_event_bus_lock`, `_event_bus_redis`) | — |
| `ContextAssembler` | `context_assembler.py` | `_load_conversation_history`, `_summarize_history`, `_assemble_context`, `_load_integration_context` | — |
| `PlanStore` | `plan_store.py` | `_persist_plan_record`, `_log_interaction` | — |
| `ToolExecutor` | `tool_executor.py` | `_build_tool_definitions`, `_build_internal_tool_definitions`, `_internal_tool_names`, `_get_tools_for_agent`, `_apply_cache_control_to_tools`, `_call_composite_tool`, `_call_internal_tool`, `_execute_tool` | EventPublisher |
| `SurfacePusher` | `surface_pusher.py` | `_check_surface_rate`, `_push_presenter_surface`, `_push_workspace_surface`, `_push_insight_surface` | EventPublisher |
| `AgentInvoker` | `agent_invoker.py` | `_call_agent`, `_call_agent_stream`, `_get_model_for_agent`, `_build_system_prompt` | ToolExecutor, ContextAssembler |
| `PerceptionRunner` | `perception_runner.py` | `run_perception_cycle`, `run_cross_source_synthesis`, `_poll_connector`, `_build_cursor_upsert_stmt`, `_ingest_raw_events`, `_update_cursor`, `_apply_perception_policy_from_planner`, `_queue_perception_plan`, `_bump_perception_for_sources`, `_extract_perception_policy` | AgentInvoker, EventPublisher, SurfacePusher, PlanStore, SystemCapabilityHandler, PerceptionPolicyService |
| `ChatProcessor` | `chat_processor.py` | `process_message`, `process_message_events`, `process_message_stream`, `_process_core` | AgentInvoker, ContextAssembler, PlanStore, SurfacePusher, EventPublisher, SystemCapabilityHandler, PerceptionPolicyService |

### Stays on the coordinator
- Lifecycle: `__init__`, `_request_services`, `_spawn_background`, `shutdown`, `load_agents_from_db`,
  `_ensure_learner_deps`, `get_budget_status`, `get_system_health`, `_get_available_capabilities`.
- `generate_briefing` — an orchestration of invoker + tool + surface + event; stays as a thin
  coordinator method delegating to the collaborators. (Not extracted: it is pure orchestration with
  no state of its own, which is exactly the coordinator's job.)
- Shared resources (`_budget`, `_circuit_breaker`, `_agents`, `_trace_manager`, `_client`,
  `_interaction_learner`) are owned here and injected into the collaborators that need them.

## 6. Design decisions to resolve coupling

1. **`_agents` runtime mutation.** `load_agents_from_db()` swaps the agent set at runtime.
   `AgentInvoker` needs the current set. Use an explicit `AgentInvoker.set_agents(agents)` that the
   coordinator calls after a DB load — no shared mutable dict, boundary stays honest.
2. **Background spawning.** Collaborators that spawn fire-and-forget work (chat, perception) receive
   `spawn_background: Callable[[Coroutine], None]` injected from the coordinator; `_background_tasks`
   + `shutdown()` remain coordinator-owned so lifecycle is centralized.
3. **Event bus ownership.** The lazy `_event_bus` (+ lock + redis) moves into `EventPublisher`.
   `ToolExecutor` and `SurfacePusher` depend on `EventPublisher`, getting the bus through it.
   `_ensure_learner_deps` (coordinator) pulls the redis/bus from `EventPublisher`.
4. **The bump signal.** `ChatProcessor` receives `PerceptionPolicyService` (not `PerceptionRunner`),
   so `_bump_perception_for_sources` logic moves with perception but the chat-side trigger is just a
   policy-service call — this is what keeps the DAG acyclic.
5. **Typed boundaries.** Cross-collaborator dependencies are passed as the concrete collaborator
   objects (constructor injection), not loose callables, so types document the contract. The one
   exception is `spawn_background` (a lifecycle primitive, naturally a callable).

## 7. Extraction order (leaves first → fewest downstream breaks)

0. **Characterization tests** for chat path, perception cycle, surface push (RED→GREEN baseline).
1. `EventPublisher` (leaf)
2. `ContextAssembler` (leaf)
3. `PlanStore` (leaf)
4. `ToolExecutor` (needs #1)
5. `SurfacePusher` (needs #1)
6. `AgentInvoker` (needs #4, #2)
7. `PerceptionRunner` (needs #6, #1, #5, #3)
8. `ChatProcessor` (needs #6, #2, #3, #5, #1)
9. Coordinator slim-down + `generate_briefing` delegation; final full-suite run.

Each numbered step is its own **structure-only** commit (`refactor:`), suite green before the next.

## 8. Testing strategy

- **Characterization first** (step 0): capture observable outputs/side-effects (events emitted,
  surfaces pushed, agents called, DB writes) of the three highest-risk flows before moving code.
- After each extraction: full `pytest tests/ -v` green; `ruff check` + `ruff format` clean.
- New per-collaborator unit tests added as each is extracted (each now constructible with a small
  set of mocked deps instead of the whole orchestrator) — moves coverage toward the 80% standard
  for the new modules.
- Existing orchestrator tests must pass **unchanged** (public API preserved) — this is the primary
  regression guarantee that behavior is identical.

## 9. Risks & mitigations

| Risk | Mitigation |
|---|---|
| Silent behavior change in just-folded `_process_core` | Characterization tests first; public API unchanged; structure-only commits |
| Hidden shared state (lazy event bus, learner wiring) | Event bus ownership explicitly moved to `EventPublisher`; `_ensure_learner_deps` reads through it |
| `_agents` staleness in `AgentInvoker` after DB load | Explicit `set_agents()` push from coordinator |
| Import cycles between new modules | DAG enforced (Section 5); leaf-first order; collaborators import only downward |
| Over-large diff hard to review | One collaborator per commit; each independently green |

## 10. Success criteria

- `jarvis.py` reduced to a coordinator within the file-size standard (target < 800, ideally far less).
- 8 new focused modules, each within the 200–400 line target band.
- Full test suite green and unchanged-passing for existing orchestrator tests.
- No public API or behavior change; `ruff` clean.
- Dependency graph acyclic and one-way (verified by imports).
