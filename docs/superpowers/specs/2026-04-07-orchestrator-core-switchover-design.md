# Spec 1B-ii: Orchestrator Core Switchover

**Status:** Draft
**Date:** 2026-04-07
**Dependencies:** Spec 1B-i (Planner Prompt + Fast Path) — needs new prompt, `extract_plan()`, `intent_to_plan()`, Perceiver prompt
**Builds toward:** Spec 1B-iii (Service Ripple + Deletion), Spec 1B-iv (Frontend Migration)

## Problem Statement

Specs 1A and 1B-i built the infrastructure and intelligence layers. This spec performs the **minimal atomic switchover** — rewriting the orchestrator routing in jarvis.py, activating the Perceiver agent, integrating PlanOutput into the GraphExecutor, and switching the intent classifier. After this spec, the system runs on capability-based routing.

This is the riskiest spec in the suite but is now scoped to ~15 core files — the minimum set that must change together for the new routing to work.

## Design

### Component 1: Orchestrator Routing Rewrite

The core change in `jarvis.py` — replace decision-type routing with capability-based plan step execution.

**In `process_message()` and `process_message_stream()`:**

1. **Replace** calls to `intent_to_decision()` → `intent_to_plan()` (from Spec 1B-i)
2. **Replace** calls to `extract_decision()` → `extract_plan()` (from Spec 1B-i)
3. **Replace** Planner system prompt assembly — inject capability summary (from Spec 1A) instead of `JARVIS_DECISION_FRAMEWORK`
4. **Delete** `_resolve_pipeline()` method
5. **Delete** all `if decision.decision == "..."` conditional blocks (~20 locations)
6. **Replace** with a loop over `plan.steps` using `route_step()` (from Spec 1A) + `CapabilityResolver` (from Spec 1A)
7. **Update** `_persist_plan_record()` to accept `PlanOutput` instead of `PlannerOutput`
8. **Update** direct handlers: `set_goal`, `set_instruction`, `schedule_reminder`, `add_to_brief` — the Planner now outputs these as `{capability: "system.set_goal"}` steps. The orchestrator detects `system.*` capabilities and calls the appropriate handler.

**New routing loop (replaces pipeline execution):**
```python
for step in plan.steps:
    if step.actor == "user":
        # Present user action context, include in response
        user_steps.append(step)
        continue

    # System capabilities → direct handlers
    if step.capability.startswith("system."):
        await self._handle_system_capability(step, user_id, workspace_id)
        continue

    # Route to agent
    agent_name = await route_step(step.capability, resolver)
    tools = await resolver.resolve_for_step(step.capability)

    if step.capability in ("reason", "respond"):
        # Direct Presenter call — no tools needed
        result = await self._call_agent("presenter", message=step.description, ...)
    else:
        result = await self._call_agent(agent_name, message=step.description, tools=tools, ...)

    step_results.append(result)
```

9. **Add** public methods `get_budget_status()` and `get_system_health()` (absorbed issue #10 — replaces private attribute access)

### Component 2: Perceiver Agent Activation

In `agents.py`:
- Delete `observer` and `researcher` from `AGENTS` dict, `AGENT_MODEL_TIERS`, `AGENT_CAPABILITY_SCOPES`
- Add `perceiver` with:
  - `model_tier`: sonnet
  - `capability_scope`: Union of old observer + researcher scopes (all read capabilities)
  - `thinking`: enabled
  - `max_tokens`: 4096
  - `temperature`: 0.3

In `prompts.py`:
- Register `PERCEIVER_PROMPT` (written in Spec 1B-i) in `AGENT_PROMPTS` dict
- Remove `observer` and `researcher` from `AGENT_PROMPTS` dict

### Component 3: Intent Classifier Switch

In `jarvis.py` (both message paths):
- Replace `classify_intent` → `intent_to_decision()` calls with `intent_to_plan()`
- Replace `extract_decision()` calls with `extract_plan()`
- Use expanded `FAST_INTENTS` from Spec 1B-i

In `intent_classifier.py`:
- Delete `intent_to_decision()` and `extract_decision()` (superseded by `intent_to_plan()` and `extract_plan()` from Spec 1B-i)

### Component 4: GraphExecutor PlanOutput Integration

**`_populate_steps()`:** Accept `PlanOutput.steps` directly. Each `PlanStep` has `description`, `capability`, `depends_on`, `input` — map these to `TaskStep` fields. Store `step.capability` in `step.input_data["capability"]`.

**`_run_step_action()`:** Use `CapabilityResolver.resolve_for_step(step.capability)` to get a focused tool list for the Operator agent, replacing `_build_operator_tools()` which returns the entire catalog.

**`_execute_step()`:** Read `step.input_data.get("capability")` instead of `step.input_data.get("task_type")` for approval checks and routing.

**`execute_run()`:** Accept optional `surface_id` parameter (for Spec 3A surface updates — not wired yet, just the parameter).

### Component 5: Chat SSE Event Change

In `routes_chat.py`:
- `MessageMetadata.decision` field type changes from `PlannerOutput` to `PlanOutput`
- SSE stream emits `plan` event (with `PlanOutput` shape) instead of `decision` event (with `PlannerOutput` shape)

This is included in this spec (not deferred to frontend spec) because the chat API is the contract boundary — backend and frontend must agree on the event format.

## Absorbed Issues from Audit

**Issue #10 — Telegram private attribute access:** Add `get_budget_status()` and `get_system_health()` to orchestrator. Update `telegram.py` to use them.

**Issue #22 — MCP tool name identity mapping:** Delete `tool_mapping[t.name] = t.name` — CapabilityResolver handles resolution now.

## Files Changed

### Modified Files (~15)

| File | What changes | Risk |
|------|-------------|------|
| `src/orchestrator/jarvis.py` | Full routing rewrite — delete `_resolve_pipeline()`, delete ~20 decision-type conditionals, add plan step loop. Add `get_budget_status()`, `get_system_health()`. | **CRITICAL** |
| `src/orchestrator/agents.py` | Delete observer/researcher, add perceiver | **HIGH** |
| `src/orchestrator/prompts.py` | Register PERCEIVER_PROMPT, remove observer/researcher from AGENT_PROMPTS | **HIGH** |
| `src/orchestrator/intent_classifier.py` | Delete `intent_to_decision()`, `extract_decision()` | **HIGH** |
| `src/services/graph_executor.py` | PlanOutput steps, CapabilityResolver, capability field | **HIGH** |
| `src/api/routes_chat.py` | MessageMetadata uses PlanOutput, `plan` SSE event | **HIGH** |
| `src/interface/telegram.py` | Use public orchestrator methods | **MEDIUM** |
| `src/connectors/mcp_bridge.py` | Delete identity tool mapping | **LOW** |

## Testing Strategy

- Integration: message → new Planner → PlanOutput → capability routing → agent execution → response
- Integration: fast path → intent_to_plan → single-step execution
- Integration: multi-step plan → GraphExecutor with CapabilityResolver
- Integration: system.set_goal capability → direct handler called
- Regression: greetings, simple questions, data fetches still work
- Unit: route_step routes correctly from orchestrator
- Unit: GraphExecutor creates steps from PlanOutput

## Success Criteria

1. Messages route through capability-based plan steps (not decision types)
2. Perceiver agent handles all read requests (observer + researcher merged)
3. GraphExecutor uses CapabilityResolver for focused tool lists
4. Chat SSE emits `plan` event with PlanOutput shape
5. Telegram uses public orchestrator methods
6. All existing fast-path intents still work

## Blast Radius

**Highest risk — but scoped to ~15 files (down from 40).**

### Tier 1: CRITICAL (must change atomically)
- `jarvis.py` — routing rewrite
- `agents.py` — agent swap
- `prompts.py` — prompt registry
- `intent_classifier.py` — function swap
- `graph_executor.py` — PlanOutput integration
- `routes_chat.py` — SSE contract

### Tier 2: MEDIUM
- `telegram.py` — public methods
- `mcp_bridge.py` — delete identity mapping

### Total: ~15 files (8 modified, tests for each)
