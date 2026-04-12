# Agent Architecture Gaps: Design Spec

**Date:** 2026-04-03
**Branch:** `improve-the-perception-system-v1`
**Status:** Draft
**Scope:** 4 remaining agent self-sufficiency gaps after the agentic routing initiative

## Background

The agent self-sufficiency initiative converted most routes from scripted (hardcoded handlers) to agentic (agent loop with tool discovery). 8 commits landed:
- `draft_reply` converted from GraphExecutor to agent loop
- Operator got read capabilities for all write domains
- New `store_memory` and `store_preference` MCP tools
- `ignore` decision early return
- Planner decision framework expanded to all 19 decisions

4 gaps remain. This spec addresses all of them.

## Design Decisions (from brainstorming)

| Decision | Choice | Rationale |
|----------|--------|-----------|
| Scope | All 4 issues | Comprehensive cleanup of agent self-sufficiency gaps |
| Issue 1 approach | Hybrid: GraphExecutor wraps agent loop | Preserves DAG/checkpointing/resume while gaining tool discovery |
| Cleanup scope | Full | Delete dead handlers, views.py, MCP bridge dispatch |
| Governor approach | New read tool + context enrichment | Read-before-write principle; defense-in-depth |
| Per-step autonomy | Full | Full Operator tool catalog, fresh context, max_tool_rounds=10 |
| Sequencing | Issue-parallel (4 phases) | Each phase independently deployable and testable |

---

## Phase 1: Governor Enhancement

### Goal
Give the Governor agent independent plan verification capability and richer context for policy decisions.

### Problem
The Governor prompt instructs: "Validate that the Planner created this plan (check plan_id)" but the Governor has only 2 tools (`evaluate_policy`, `approve_action`) — neither exposes plan details. The `evaluate_policy` tool does a DB plan lookup internally (returns "blocked" if not found), but the Governor cannot independently verify or cross-check plan metadata.

Additionally, the Governor is NOT in `CONTEXT_ENRICHED_AGENTS`, so it makes policy decisions without user preferences, entity context, or related run history.

### Changes

#### 1.1 New `get_plan_details` internal MCP tool

**`src/tools/schemas.py`** — Add input model:
```python
class GetPlanDetailsInput(BaseModel):
    plan_id: str
    user_id: str
    workspace_id: str = ""
```

**`src/tools/catalog.py`** — Add to `INTERNAL_TOOLS`:
```python
InternalToolDef(
    name="get_plan_details",
    description="Fetch plan metadata to verify existence and inspect tasks",
    capability="internal.get_plan_details",
    risk_level="low",
    requires_approval=False,
    server_prefix="intelligence",
)
```

**`src/tools/intelligence_server.py`** — Implement handler:
- Query `Plan` + `PlanTask` records from DB by `plan_id` + `workspace_id`
- Return: `{plan_id, goal, priority, risk_level, decision, status, created_at, tasks: [{task_type, description, depends_on}]}`
- Return `{"status": "not_found"}` if plan doesn't exist or wrong workspace

#### 1.2 Governor capability scope expansion

**`src/orchestrator/agents.py`**:
- Add `"internal.get_plan_details"` to governor's capability set
- Add `"governor"` to `CONTEXT_ENRICHED_AGENTS`

#### 1.3 Governor prompt update

**`src/orchestrator/prompts.py`** — Update `GOVERNOR_PROMPT`:
- Clarify verification workflow: call `get_plan_details(plan_id)` first
- Cross-check plan metadata (goal, priority, risk_level) against PlannerOutput claims
- Then call `evaluate_policy(plan_id)` if verification passes
- If plan not found → return blocked verdict immediately

#### 1.4 Tests

- `test_get_plan_details`: plan found (returns metadata), plan not found (returns not_found), wrong workspace (returns not_found)
- Governor integration: agent calls `get_plan_details` → `evaluate_policy` in sequence
- Update existing governor tests to account for new tool in scope

### Data flow (after)

```
Governor receives: "Evaluate this plan: {decision_json}"
  → ContextBuilder assembles: preferences, entities, related runs, graph
  → Governor calls get_plan_details(plan_id) → verifies plan exists
  → Cross-checks DB metadata against PlannerOutput claims
  → Governor calls evaluate_policy(plan_id) → gets policy verdict
  → Returns PolicyDecision with verification + policy reasoning
```

### Files touched
- `src/tools/schemas.py` (add input model)
- `src/tools/catalog.py` (add tool def)
- `src/tools/intelligence_server.py` (add implementation)
- `src/orchestrator/agents.py` (capability scope + context enrichment)
- `src/orchestrator/prompts.py` (governor prompt)
- `tests/test_governor.py` or new `tests/test_get_plan_details.py`

---

## Phase 2: GraphExecutor Agentic Migration

### Goal
Replace GraphExecutor's hardcoded step handlers with per-step agent loop calls. The `create_task` route becomes the 17th agentic route while preserving DAG orchestration, checkpointing, and resume.

### Problem
GraphExecutor's `_run_step_action` uses a 4-tier dispatch:
1. MCP bridge check (external MCP servers)
2. ToolRegistry connector dispatch
3. Built-in Claude handlers (`_draft_action`, `_summarize_action`)
4. `_generic_claude_action` catch-all (single-turn Claude call, no tools, no governor)

This means:
- No tool discovery (steps can't adapt to available tools)
- No multi-turn reasoning (one-shot execution, can't refine on failure)
- Generic actions bypass governor hooks (security gap)
- Every new action type needs a handler or registry entry

### Changes

#### 2.1 New `_run_step_via_agent_loop` method

**`src/services/graph_executor.py`** — Add method:

```
_run_step_via_agent_loop(step: TaskStep, run: TaskRun) -> dict:
    1. Build step message from task_type + input_data + resolved upstream references
    2. Assemble fresh context via ContextBuilder.build() (new DB session per step)
    3. Resolve Operator agent definition from AGENTS registry
    4. Get full tool catalog for Operator via ToolRegistry
    5. Call agent_loop(
         client=self._client,
         agent=operator,
         model=self._settings.resolved_model,
         system_blocks=[operator system prompt + context sections],
         tools=full_operator_tool_catalog,
         message=step_message,
         user_id=run.user_id,
         workspace_id=run.workspace_id,
         execute_tool_fn=self._execute_tool_with_governor,
         max_tool_rounds=10,
       )
    6. Collect final text + tool results from LoopDone event
    7. Return {status: "completed", result: text, tool_calls_made: [...], tokens_used: N}
```

#### 2.2 Replace `_run_step_action` dispatch

**`src/services/graph_executor.py`** — Rewrite `_run_step_action`:

Before (4-tier dispatch, ~60 lines):
```
_run_step_action(step, run):
  task_type = input_data.get("task_type")
  if is_mcp_tool(task_type): return mcp_bridge(...)
  tool_def = registry.get_tool(task_type)
  if tool_def and tool_def.connector_type != "internal": return connector(...)
  if task_type in ("draft_email", "draft_reply"): return _draft_action(...)
  if task_type == "summarize": return _summarize_action(...)
  return _generic_claude_action(...)
```

After (single dispatch, ~3 lines):
```
_run_step_action(step, run):
  return await _run_step_via_agent_loop(step, run)
```

#### 2.3 GraphExecutor constructor changes

**`src/services/graph_executor.py`** — Add dependencies:
- `db_factory` parameter — for per-step `ContextBuilder.build()` calls
- `anthropic_client` — for agent_loop Claude API calls (or reuse existing `self._client`)
- `services` container reference — for `execute_tool_fn` callback wiring

#### 2.4 Update orchestrator bridge

**`src/orchestrator/jarvis.py`** — Update `_execute_plan_via_graph`:
- Pass `db_factory` and `services` to GraphExecutor constructor
- No changes to plan persistence or result handling

#### 2.5 Update scheduler bridge

**`src/services/scheduler.py`** — Update `_tick_background_tasks`:
- Pass same new dependencies to GraphExecutor constructor
- Background task execution continues via GraphExecutor (no alternative path needed)

#### 2.6 Governor hooks integration

After migration, security improves:
- Agent loop's `execute_tool_fn` callback must be wired to the same tool execution infrastructure used by the orchestrator — specifically `hooks.py` governor pre-hook (`on_tool_call`) and audit post-hook
- GraphExecutor provides `execute_tool_fn` by wrapping the orchestrator's existing `_execute_tool` method (passed via constructor or services container), ensuring governor hooks fire for every tool call
- If governor blocks a tool call → approval created, step transitions to `waiting_approval`, run transitions to `awaiting_approval`
- On approval resume → `routes_approvals.py` creates a NEW TaskRun (`source="approval_resume"`) with the approved tool as a single-step plan. Scheduler picks it up via `_tick_background_tasks` and executes through GraphExecutor → agent loop

Two layers of approval gates:
1. Pre-step: `_execute_step` checks `tool.requires_approval` from plan's task definition
2. Per-tool-call: agent loop's `on_tool_call` hook checks actual tool being called at runtime

### What stays the same
- DAG dependency resolution (`depends_on`, `_get_ready_steps`)
- Checkpointing after every step (`TaskCheckpoint` records)
- Resume from pause (`resume_run`)
- Output reference passing between steps (`{task_id}.output.field`)
- Step-level retry (3 retries, exponential backoff)
- Run-level timeout (600s for background runs)
- State machine enforcement (`transition_run`, `transition_step`)
- Pre-step approval gates in `_execute_step`
- Background task execution via `_tick_background_tasks`
- Post-execution verification (`_run_verification`)
- Memory writeback (`_writeback_memories`)

### What changes
- Step execution: hardcoded handlers → agent loop with full tool catalog
- Tool discovery: registry lookup by `task_type` → Operator discovers tools autonomously
- Reasoning: single-turn Claude call → multi-turn with tool refinement (up to 10 rounds)
- Security: generic actions bypassed governor → ALL tool calls gated by governor hooks
- Context: run-level `context_pack_json` still populated at run creation (used for verification/writeback), but each step also calls `ContextBuilder.build()` for fresh context (prevents stale data in long-running DAGs)
- Token cost: ~5x per step (multi-turn + larger system prompt + tool catalog)

### Architecture after migration

```
Planner → Plan + PlanTask records → DB
  ↓
Governor (agent loop — with get_plan_details + context enrichment)
  ↓
GraphExecutor.execute_run(run_id)
  ↓
_execute_dag(run)           ← UNCHANGED (DAG loop, dependency resolution)
  ↓
_execute_step(run, step)    ← UNCHANGED (approval gates, retry, checkpointing)
  ↓
_run_step_action(step, run) ← CHANGED: delegates to _run_step_via_agent_loop
  ↓
agent_loop(operator, tools, message, ...)  ← NEW: full tool discovery,
  ↓                                          multi-turn reasoning,
  ↓                                          governor hooks per tool call
  ↓
LoopDone → step output → checkpoint → next step in DAG
```

### Token cost consideration
Per-step agent loop calls are more expensive than the old single-turn `_generic_claude_action`. For a 5-step plan, expect ~5x the token usage. BudgetTracker integration in `agent_loop` enforces daily limits. This is the trade-off for autonomy + security.

### Tests
- `_run_step_via_agent_loop` with mocked `agent_loop`: verify tool catalog passed, context assembled, result collected
- Governor hooks fire during per-step agent loop execution
- Step approval gate pauses DAG when agent loop tool gets blocked
- 2-step plan with dependency: step 2 receives step 1 output via reference resolution
- Background task via scheduler uses new agent loop path
- Update `test_graph_executor.py`: mock `agent_loop` instead of `_generic_claude_action`

### Files touched
- `src/services/graph_executor.py` (major: new method, rewrite dispatch, constructor)
- `src/orchestrator/jarvis.py` (minor: pass new deps to GraphExecutor)
- `src/services/scheduler.py` (minor: pass new deps to GraphExecutor)
- `tests/test_graph_executor.py` (update mocks)

---

## Phase 3: Dead Code Cleanup

### Goal
Remove all code paths made unreachable by Phase 2, plus the already-orphaned `views.py`.

### Deletions

| File | What | Lines (approx) |
|------|------|-----------------|
| `src/services/graph_executor.py` | `_draft_action` method | ~65 |
| `src/services/graph_executor.py` | `_summarize_action` method | ~25 |
| `src/services/graph_executor.py` | `_generic_claude_action` method | ~35 |
| `src/services/graph_executor.py` | MCP bridge check from old dispatch | ~15 |
| `src/services/graph_executor.py` | Connector dispatch path (`_execute_via_connector`) | ~30 |
| `src/ui/views.py` | Entire file (10 orphaned view generators) | ~300 |
| `tests/test_graph_executor_draft.py` | Entire file (tests `_draft_action`) | ~128 |
| `src/services/graph_executor.py` | Unused imports from deleted code | ~5 |

**Estimated net deletion: ~600 lines**

### Verification before deletion

For each deletion, grep for callers to confirm unreachability:
- `_draft_action`: only called from old `_run_step_action` dispatch (replaced in Phase 2)
- `_summarize_action`: same
- `_generic_claude_action`: same
- `_execute_via_connector`: only called from old dispatch path
- `views.py`: grep for `from src.ui.views` and `from src.ui import views` — confirm zero callers
- `test_graph_executor_draft.py`: tests deleted method only

### What we do NOT delete
- `_build_context_prompt` — keep if reused by `_run_step_via_agent_loop` for building step message context. Delete only if no remaining callers.
- `renderer.py` — 36 builder functions still used by `SurfaceService` and `_push_workspace_surface`
- `ui/contracts.py` — A2UI component types still used by renderer.py and frontend

### Tests
- Run full test suite after deletions to confirm no breakage
- No new tests needed (pure deletion phase)

### Files touched
- `src/services/graph_executor.py` (delete methods + imports)
- `src/ui/views.py` (delete entire file)
- `tests/test_graph_executor_draft.py` (delete entire file)

---

## Phase 4: Prompt & Documentation Fixes

### Goal
Fix misleading prompt examples, clarify agent roles, update CLAUDE.md.

### Changes

#### 4.1 Planner prompt fix

**`src/orchestrator/prompts.py`** — `JARVIS_DECISION_FRAMEWORK`:
- Fix `draft_reply` example: remove `tasks: [{task_type: "draft_email"}]` (misleading — `draft_reply` is agentic, Operator discovers tools)
- Update `create_task` examples: `task_type` is now a semantic label describing the goal, not a tool name. Add guidance: "Each task step will be executed by the Operator agent with full tool access. Describe the goal, not the method."

#### 4.2 Presenter prompt clarification

**`src/orchestrator/prompts.py`** — `PRESENTER_PROMPT`:
- Add: "You generate text responses for the user. Workspace surfaces (cards, tables, metrics) are built by infrastructure (SurfaceService), not by you. Focus on clear, conversational communication."

#### 4.3 CLAUDE.md updates

**`CLAUDE.md`**:
- Update "Agentic vs Scripted Execution" section: all routes are now agentic. GraphExecutor is a "durable DAG wrapper around agent loop" not a separate execution mode.
- Update "Common Mistakes":
  - Remove: "Do not add `action: execute_plan` to new routes unless the workflow genuinely needs DAG execution"
  - Add: "Do not bypass agent loop for step execution — GraphExecutor delegates to agent_loop per step"
  - Add: "Do not import from `src/ui/views.py` — deleted. Use `renderer.py` builders + `SurfaceService`"
- Update Governor row in agent boundaries table: add read plan details to capabilities
- Note `create_task` route still uses `action: "execute_plan"` to trigger GraphExecutor — the route definition is correct; it's the execution *inside* GraphExecutor that changed

#### 4.4 Tests
- No behavioral tests (text-only changes)
- Optional: unit test that parses Planner prompt JSON examples to prevent prompt rot

### Files touched
- `src/orchestrator/prompts.py` (planner + presenter prompts)
- `CLAUDE.md` (documentation)

---

## Cross-Cutting Concerns

### Migration safety
- Each phase is independently deployable
- Phase 1 (Governor) has zero risk to execution paths
- Phase 2 (GraphExecutor) is the big change — requires thorough testing
- Phase 3 (cleanup) is pure deletion — trivially revertable via `git revert`
- Phase 4 (prompts) is text-only

### Backward compatibility
- No API changes (all internal)
- No DB migrations needed
- Existing `TaskRun`/`TaskStep` records continue to work (schema unchanged)
- Background tasks (`_tick_background_tasks`) continue working (same GraphExecutor entry point)
- Approval resume flow unchanged (same `resume_run` method)

### Token budget impact
- Phase 2 increases per-step token cost (~5x for multi-turn agent loop vs single-turn Claude call)
- Mitigated by `BudgetTracker` daily limits
- Monitor via existing Prometheus metrics (`jarvis_tokens_used_total`)

### Testing strategy
- Phase 1: 3-5 new unit tests + 1-2 integration tests
- Phase 2: 5-7 new unit tests + 2-3 integration tests
- Phase 3: run full suite (1137 tests), no new tests
- Phase 4: no behavioral tests

### Rollback plan
- Phase 1: remove tool from catalog + revert capability scope
- Phase 2: revert `_run_step_action` to old 4-tier dispatch (keep deleted handlers until Phase 3)
- Phase 3: `git revert` the cleanup commit
- Phase 4: `git revert` the prompt commit
