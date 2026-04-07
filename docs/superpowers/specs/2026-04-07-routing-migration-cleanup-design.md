# Spec 1B-ii: Routing Migration + Agent Merge + Cleanup

**Status:** Draft
**Date:** 2026-04-07
**Dependencies:** Spec 1B-i (Planner Prompt + Fast Path) — needs new prompt, `extract_plan()`, `intent_to_plan()`, Perceiver prompt
**Builds toward:** Spec 2 (Trust), Spec 3 (Surfaces), Spec 4 (Perception)

## Problem Statement

Specs 1A and 1B-i built the full capability infrastructure and new Planner intelligence. This spec **switches over** — replacing the orchestrator routing, merging Observer+Researcher into Perceiver, updating the GraphExecutor, deleting all old routing code, and updating the frontend. This is the highest-risk spec in the suite.

## Design

### Component 1: Orchestrator Routing Rewrite

Replace decision-type routing in `jarvis.py` with capability-based routing:

- **Delete** `_resolve_pipeline()` method
- **Delete** all `if decision.decision == "..."` blocks (~20 locations)
- **Delete** imports of `RouteResolver`, `DEFAULT_ROUTES`
- **Replace** with loop over `plan.steps` using `route_step()` (from Spec 1A) + `CapabilityResolver`
- **Update** `_persist_plan_record()` to accept `PlanOutput`
- **Update** `_push_workspace_surface()` — use `_capability_to_surface_kind()` instead of decision-type mapping
- **Update** direct handlers: `set_goal`, `set_instruction`, etc. become plan steps with `capability: "system.set_goal"` instead of decision-type checks
- **Wire into BOTH** `process_message()` and `process_message_stream()`
- **Add** public methods `get_budget_status()`, `get_system_health()` (absorbed issue #10)

### Component 2: Perceiver Agent Activation

- Delete `observer` and `researcher` from `AGENTS` dict in `agents.py`
- Add `perceiver` with merged capability scope (all read capabilities)
- Delete `OBSERVER_PROMPT`, `RESEARCHER_PROMPT` from `prompts.py`
- Rename `PLANNER_PROMPT_V2` → `PLANNER_PROMPT` (replace old)
- Delete `JARVIS_DECISION_FRAMEWORK`, `JARVIS_SOUL` alias

### Component 3: GraphExecutor PlanOutput Integration

- `_populate_steps()` accepts `PlanOutput.steps` directly (have `capability`, `depends_on`)
- `_run_step_action()` uses `CapabilityResolver.resolve_for_step()` for focused tool list
- `_execute_step()` reads `step.capability` instead of `step.input_data.get("task_type")`
- `execute_run()` accepts `surface_id` parameter for live surface updates (Spec 3 dependency)

### Component 4: Switch Intent Classifier

- Replace calls to `intent_to_decision()` → `intent_to_plan()` in jarvis.py
- Replace calls to `extract_decision()` → `extract_plan()` in jarvis.py
- Delete `intent_to_decision()` and `extract_decision()` from intent_classifier.py

### Component 5: Delete Old Routing Code

- **Delete** `src/services/route_resolver.py`
- **Delete** `src/services/route_analytics.py`
- **Delete** `PlannerOutput`, `PlannerTask`, `InstructionSpec`, `ExecutionPlan` from contracts.py
- **Remove** `RouteResolver.seed_defaults()` from `app.py` startup
- **Add** Alembic migration to drop `agent_routes` table
- **Delete** MCP bridge identity mapping (absorbed issue #22)

### Component 6: Service Updates

- `governor.py` — work with `PlanOutput` (no `decision` field)
- `metrics_service.py` — capability-based labels instead of decision labels
- `event_bus.py` — update domain event payloads
- `surface_builder.py` — capability-based surface building
- `scheduler.py` — PlanOutput format, "perceiver" references
- `tracing.py` — plan goal string instead of decision-type string

### Component 7: Frontend Updates

- `api.ts` — PlanOutput type, `plan` SSE event instead of `decision`
- `types.ts` — delete decision-field types, update MessageMetadata
- `a2ui-types.ts` — remove `decision` from WorkspaceSurfacePush
- `types/runtime.ts` — capability-based RuntimeEventType values
- `agent-config.ts` — delete observer/researcher, add perceiver, demote governor
- `chat-panel.tsx` — parse `plan` event, render `perceiver` agent steps
- `activity-store.ts` — new event types
- `activity-strip.tsx` — new event rendering

### Component 8: Telegram Fix (Absorbed Issue #10)

Update `telegram.py` to use `orchestrator.get_budget_status()` and `orchestrator.get_system_health()` instead of accessing private `_budget` and `_db_factory()`.

## Files Changed

### Deleted Files
- `src/services/route_resolver.py`
- `src/services/route_analytics.py`
- `tests/test_route_resolver.py` (60+ tests)

### Modified Files — Backend (20 files)
- `src/orchestrator/jarvis.py` — Full routing rewrite
- `src/orchestrator/contracts.py` — Delete PlannerOutput, PlannerTask, InstructionSpec, ExecutionPlan
- `src/orchestrator/prompts.py` — Delete 4 constants, rename V2 → primary
- `src/orchestrator/agents.py` — Delete 2 agents, add 1
- `src/orchestrator/intent_classifier.py` — Delete 2 old functions
- `src/orchestrator/tracing.py` — Update SpanRecord.decision
- `src/services/graph_executor.py` — PlanOutput steps + CapabilityResolver
- `src/services/governor.py` — Work with PlanOutput
- `src/services/metrics_service.py` — Capability labels
- `src/services/event_bus.py` — Event payloads
- `src/services/surface_builder.py` — Capability-based surfaces
- `src/services/surface_detail_builders.py` — New plan structure
- `src/services/scheduler.py` — PlanOutput, "perceiver"
- `src/api/app.py` — Remove seed_defaults()
- `src/api/routes_chat.py` — PlanOutput in MessageMetadata, `plan` SSE event
- `src/api/routes_traces.py` — New trace structure
- `src/ui/renderer.py` — Capability-based detail config
- `src/models/agent_routes.py` — Deprecate
- `src/interface/telegram.py` — Public orchestrator methods

### Modified Files — Frontend (8 files)
- `frontend/src/lib/api.ts`, `types.ts`, `a2ui-types.ts`, `types/runtime.ts`, `agent-config.ts`
- `frontend/src/components/jarvis/chat-panel.tsx`
- `frontend/src/stores/activity-store.ts`
- `frontend/src/components/shell/activity-strip.tsx`

### New Files
- Alembic migration to drop `agent_routes` table

### Tests — Rewrite Required (9 files)
- `tests/test_contracts.py`, `test_orchestrator.py`, `test_planner_structured.py`
- `tests/test_perception_execution.py`, `test_ignore_decision.py`, `test_agent_registry.py`
- `tests/golden/test_planner_decisions.py`, `tests/test_contracts_v2.py`

## Testing Strategy

- Integration: message → new Planner → PlanOutput → capability routing → agent execution → response
- Integration: fast path → intent_to_plan → single-step execution
- Integration: multi-step plan → GraphExecutor with CapabilityResolver
- Regression: greetings, simple questions, data fetches still work
- Frontend: SSE `plan` event parsed, `perceiver` agent rendered
- Grep sweep: all 19 decision type strings eliminated from `backend/src/`

## Success Criteria

1. All 19 decision types eliminated — capability-based routing only
2. Observer + Researcher merged into Perceiver
3. RouteResolver, DEFAULT_ROUTES, PlannerOutput deleted
4. Operator receives step-focused tools via CapabilityResolver
5. Frontend parses `plan` event and renders `perceiver` steps
6. Telegram uses public orchestrator methods
7. All tests pass with new routing

## Blast Radius

**Highest risk in the suite — core routing replacement.**

### Tier 1: CRITICAL (5 files — must change atomically)
`jarvis.py`, `contracts.py`, `prompts.py`, `agents.py`, `intent_classifier.py`

### Tier 2: HIGH (5 files)
`graph_executor.py`, `governor.py`, `routes_chat.py`, `route_resolver.py` (DELETE), `app.py`

### Tier 3: MEDIUM (18 files — services + frontend)
6 backend services + 8 frontend files + 4 misc

### Tier 4: Tests (9 files — rewrite)

### Key Risk: String grep for 19 decision types before deletion.

### Total: ~40 files (20 backend, 9 tests, 8 frontend, 2 deleted, 1 migration)
