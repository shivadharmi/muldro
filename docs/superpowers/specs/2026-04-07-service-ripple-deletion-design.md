# Spec 1B-iii: Service Ripple + Old Code Deletion

**Status:** Draft
**Date:** 2026-04-07
**Dependencies:** Spec 1B-ii (Orchestrator Core Switchover) — core routing must be working on PlanOutput
**Builds toward:** Spec 1B-iv (Frontend Migration)

## Problem Statement

After Spec 1B-ii, the orchestrator runs on capability-based routing. But dependent services still reference old contracts (`PlannerOutput`, decision types, observer/researcher agent names), and dead code still exists (RouteResolver, JARVIS_DECISION_FRAMEWORK, old prompts). This spec updates all dependent services and deletes all old routing code.

This is safe because the core switchover (1B-ii) already stopped using these — we're just cleaning up references and deleting dead code.

## Design

### Component 1: Service Updates

Each service that references `PlannerOutput`, decision types, or old agent names must be updated:

| Service | What changes |
|---------|-------------|
| `governor.py` | `evaluate_plan()` works with PlanOutput — iterate step capabilities for risk instead of reading `decision` field |
| `metrics_service.py` | `PLANS_CREATED` counter label changes from `["decision"]` to capability-based label |
| `event_bus.py` | Domain event payloads no longer include `decision` field — use plan goal instead |
| `surface_builder.py` | Surface building uses `_capability_to_surface_kind()` instead of decision-type mapping |
| `surface_detail_builders.py` | Detail builders use new plan structure (step capabilities, not task_type) |
| `scheduler.py` | `_tick_background_tasks()` works with PlanOutput format, "observer" references → "perceiver" |
| `tracing.py` | `SpanRecord.decision` stores plan goal string instead of decision-type string |
| `renderer.py` | `build_detail_config()` uses capability-based plan structure |
| `app.py` | Remove `RouteResolver.seed_defaults()` from startup |
| `routes_traces.py` | Trace display uses new plan structure |

### Component 2: Delete Old Routing Code

| File/Code | Action |
|-----------|--------|
| `src/services/route_resolver.py` | **DELETE entire file** |
| `src/services/route_analytics.py` | **DELETE entire file** |
| `JARVIS_DECISION_FRAMEWORK` in prompts.py | **DELETE** constant |
| `JARVIS_SOUL` alias in prompts.py | **DELETE** |
| `OBSERVER_PROMPT` in prompts.py | **DELETE** (PERCEIVER_PROMPT already registered in 1B-ii) |
| `RESEARCHER_PROMPT` in prompts.py | **DELETE** |
| `PlannerOutput` in contracts.py | **DELETE** model |
| `PlannerTask` in contracts.py | **DELETE** model |
| `InstructionSpec` in contracts.py | **DELETE** model |
| `ExecutionPlan` in contracts.py | **DELETE** model |
| `src/models/agent_routes.py` | **DELETE** or deprecate model |
| Alembic migration | **ADD** migration to drop `agent_routes` table |

### Component 3: Test Rewrites

All tests referencing old contracts must be rewritten:

| Test File | Action |
|-----------|--------|
| `tests/test_contracts.py` | Rewrite — 50+ tests reference PlannerOutput |
| `tests/test_route_resolver.py` | **DELETE** (60+ tests for deleted service) |
| `tests/test_orchestrator.py` | Rewrite for new routing |
| `tests/test_planner_structured.py` | Rewrite for PlanOutput parsing |
| `tests/test_perception_execution.py` | Update PlannerOutput fixtures |
| `tests/test_ignore_decision.py` | Update for new handling |
| `tests/test_agent_registry.py` | Update for perceiver agent |
| `tests/golden/test_planner_decisions.py` | Complete rewrite |
| `tests/test_contracts_v2.py` | Update for new models |

### Component 4: String Grep Sweep

Before this spec is marked complete, run:

```bash
rg '"create_task"|"draft_reply"|"read_source"|"research"|"observe"|"remember"|"acknowledge"|"answer_directly"|"search_memory"|"add_to_brief"|"ignore"|"watcher_create"|"goal_update"|"recommend"|"summarize"|"schedule_reminder"|"set_goal"|"set_instruction"|"ask_user"' backend/src/
```

Every remaining hit must be addressed. Zero tolerance — these strings should not exist in `backend/src/` after this spec.

## Files Changed

### Deleted Files (3)
- `src/services/route_resolver.py`
- `src/services/route_analytics.py`
- `tests/test_route_resolver.py`

### Modified Files — Services (10)
- `src/services/governor.py`
- `src/services/metrics_service.py`
- `src/services/event_bus.py`
- `src/services/surface_builder.py`
- `src/services/surface_detail_builders.py`
- `src/services/scheduler.py`
- `src/orchestrator/tracing.py`
- `src/ui/renderer.py`
- `src/api/app.py`
- `src/api/routes_traces.py`

### Modified Files — Contracts (2)
- `src/orchestrator/contracts.py` — Delete PlannerOutput, PlannerTask, InstructionSpec, ExecutionPlan
- `src/orchestrator/prompts.py` — Delete JARVIS_DECISION_FRAMEWORK, JARVIS_SOUL, OBSERVER_PROMPT, RESEARCHER_PROMPT

### Modified Files — Models (1)
- `src/models/agent_routes.py` — Delete or deprecate

### New Files (1)
- Alembic migration to drop `agent_routes` table

### Test Files (9 — rewrite or delete)
- 8 test files rewritten, 1 deleted

## Testing Strategy

- Verify: all services work with PlanOutput (no PlannerOutput references)
- Verify: zero hits from decision-type string grep
- Verify: RouteResolver fully deleted with no import errors
- Verify: startup works without seed_defaults()
- All rewritten tests pass

## Success Criteria

1. Zero references to PlannerOutput in `backend/src/`
2. Zero references to 19 decision type strings in `backend/src/`
3. RouteResolver, route_analytics deleted
4. agent_routes table dropped
5. All dependent services updated for PlanOutput
6. All tests pass with new contracts

## Blast Radius

**Medium — mostly deletion and updates to services that already stopped using old code (1B-ii removed the callers).**

| Change Type | Files | Risk |
|-------------|-------|------|
| Service updates (governor, metrics, etc.) | 10 | **MEDIUM** — behavioral change |
| Contract deletion (PlannerOutput) | 2 | **LOW** — callers already switched in 1B-ii |
| File deletion (RouteResolver) | 3 | **LOW** — nothing calls it anymore |
| Test rewrites | 9 | **MEDIUM** — coverage must be maintained |

### Total: ~25 files (12 modified, 3 deleted, 1 new migration, 9 tests)
