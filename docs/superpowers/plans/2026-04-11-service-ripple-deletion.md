# Spec 1B-iii: Service Ripple + Old Code Deletion — Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Update all dependent services from old PlannerOutput/decision-type contracts to PlanOutput/capability-based contracts, then delete all dead routing code. Zero remaining references to the 19 old decision-type strings in `backend/src/`.

**Architecture:** The orchestrator already routes via PlanOutput + CapabilityResolver (done in Spec 1B-ii). This spec performs the ripple: governor reads plan risk from PlanStep capabilities, metrics labels switch to capabilities, surfaces derive kinds from capabilities, and all dead code (RouteResolver, old prompts, old contract models, agent_routes table) is deleted. No new features — just cleanup and alignment.

**Tech Stack:** Python 3.12, Pydantic, SQLAlchemy, Alembic, pytest, ruff

---

## File Structure

### Deleted Files (5)
| File | Reason |
|------|--------|
| `src/services/route_resolver.py` (480 lines) | Dead — replaced by CapabilityResolver in Spec 1B-i |
| `src/services/route_analytics.py` (190 lines) | Dead — depended on RouteResolver/AgentRoute |
| `src/models/agent_routes.py` (53 lines) | Dead — backing model for deleted RouteResolver |
| `tests/test_route_resolver.py` (467 lines) | Tests for deleted service |
| `tests/test_ignore_decision.py` (26 lines) | Tests old route config constants |

### Modified Files — Services (10)
| File | What Changes |
|------|-------------|
| `src/services/governor.py` | `_apply_policy` reads `plan.risk_level` and iterates step capabilities instead of checking `plan.decision` against string sets |
| `src/services/metrics_service.py` | `PLANS_CREATED` counter label `["decision"]` → `["capability"]`; `record_plan_created(decision)` → `record_plan_created(capability)` |
| `src/services/event_bus.py` | No code changes needed (verified: event_bus.py doesn't reference decision types or PlannerOutput) |
| `src/services/surface_builder.py` | `_load_persisted_surfaces` — rename payload key `"decision"` → `"capability"` in returned dict |
| `src/services/surface_detail_builders.py` | No code changes needed (verified: uses step/run data, not decision types) |
| `src/services/scheduler.py` | `_fire()` wake_agent: `"observer"` fallback → `"perceiver"` |
| `src/orchestrator/tracing.py` | No structural changes needed (SpanRecord.decision already stores strings generically) |
| `src/ui/renderer.py` | No code changes needed (verified: no decision-type references) |
| `src/api/app.py` | Remove `RouteResolver` import and `seed_defaults()` call from startup |
| `src/api/routes_traces.py` | `DecisionLogEntry.decision` field — rename to `plan_goal` for clarity |

### Modified Files — Contracts & Prompts (2)
| File | What Changes |
|------|-------------|
| `src/orchestrator/contracts.py` | Delete: `PlannerOutput`, `PlannerTask`, `InstructionSpec`, `ExecutionPlan` (4 dead models) |
| `src/orchestrator/prompts.py` | Delete: `JARVIS_DECISION_FRAMEWORK`, `JARVIS_SOUL`, `OBSERVER_PROMPT`, `RESEARCHER_PROMPT`, old `PLANNER_PROMPT` |

### Modified Files — Orchestrator (1)
| File | What Changes |
|------|-------------|
| `src/orchestrator/jarvis.py` | Rewrite `_handle_system_capability` to work directly with PlanStep instead of bridging to PlannerOutput. Remove PlannerOutput/InstructionSpec/PlannerTask imports. Fix `"decision": "ignore"` and `"decision": "error"` strings in error returns. |

### Modified Files — Models (1)
| File | What Changes |
|------|-------------|
| `src/models/__init__.py` | Remove `AgentRoute` import and `__all__` entry |

### New Files (1)
| File | Purpose |
|------|---------|
| `alembic/versions/xxx_drop_agent_routes_table.py` | Alembic migration to drop `agent_routes` table |

### Test Files (9 — rewrite or delete)
| File | Action |
|------|--------|
| `tests/test_contracts.py` | Rewrite: remove `TestPlannerTask`, `TestPlannerOutput`, update remaining tests |
| `tests/test_contracts_v2.py` | Rewrite: remove `TestExecutionPlan`, keep `TestPolicyDecision` and `TestDomainEvent` |
| `tests/test_orchestrator.py` | Update: remove `JARVIS_SOUL` test, fix `TestPrompts` to not check dead constants |
| `tests/test_orchestrator_routing.py` | Minimal update: already uses PlanOutput — fix any residual references |
| `tests/test_planner_structured.py` | Rewrite: replace PlannerOutput tests with PlanOutput equivalents |
| `tests/test_perception_execution.py` | Already uses PlanOutput — no changes needed (verified) |
| `tests/test_agent_registry.py` | Already uses perceiver — no changes needed (verified) |
| `tests/golden/test_planner_decisions.py` | Already uses PlanOutput — no changes needed (verified) |
| `tests/test_ignore_decision.py` | **DELETE** — tests RouteResolver constants |
| `tests/test_route_resolver.py` | **DELETE** — tests deleted service |
| `tests/test_integration_audit.py` | Update: remove `TestRouteAnalytics` class that imports deleted service |

---

## Task 1: Delete Dead Routing Files

**Files:**
- Delete: `backend/src/services/route_resolver.py`
- Delete: `backend/src/services/route_analytics.py`
- Delete: `backend/src/models/agent_routes.py`
- Modify: `backend/src/models/__init__.py`
- Modify: `backend/src/api/app.py`

- [ ] **Step 1: Delete route_resolver.py**

```bash
rm backend/src/services/route_resolver.py
```

- [ ] **Step 2: Delete route_analytics.py**

```bash
rm backend/src/services/route_analytics.py
```

- [ ] **Step 3: Delete agent_routes.py model**

```bash
rm backend/src/models/agent_routes.py
```

- [ ] **Step 4: Remove AgentRoute from models/__init__.py**

In `backend/src/models/__init__.py`, delete:
```python
from src.models.agent_routes import AgentRoute
```
And remove `"AgentRoute",` from the `__all__` list.

- [ ] **Step 5: Remove RouteResolver from app.py startup**

In `backend/src/api/app.py`, in the `lifespan()` function, remove the route seeding block.

Remove the import:
```python
from src.services.route_resolver import RouteResolver
```

Remove the route seeding try/except block:
```python
                try:
                    route_count = await RouteResolver(db).seed_defaults()
                    if route_count:
                        needs_commit = True
                        logger.info("Seeded %d agent routes", route_count)
                except Exception:
                    logger.warning("Route seed failed", exc_info=True)
```

- [ ] **Step 6: Verify no remaining imports of deleted files**

Run:
```bash
rg "from src\.services\.route_resolver|from src\.services\.route_analytics|from src\.models\.agent_routes" backend/src/
```
Expected: zero hits.

- [ ] **Step 7: Run tests to verify nothing breaks**

```bash
cd backend && python -m pytest tests/ -x -q --timeout=30 2>&1 | tail -20
```
Expected: tests that import deleted files will fail (expected — we fix those in Task 7).

- [ ] **Step 8: Commit**

```bash
git add -A backend/src/services/route_resolver.py backend/src/services/route_analytics.py backend/src/models/agent_routes.py backend/src/models/__init__.py backend/src/api/app.py
git commit -m "feat(spec1b-iii): delete RouteResolver, route_analytics, agent_routes model"
```

---

## Task 2: Delete Dead Contract Models

**Files:**
- Modify: `backend/src/orchestrator/contracts.py`

- [ ] **Step 1: Read contracts.py and identify dead models**

The following models are dead (no callers after Task 3 rewrites jarvis.py):
- `PlannerTask` (lines 15–22)
- `PlannerOutput` (lines 24–67)
- `InstructionSpec` (lines 70–78)
- `ExecutionPlan` (lines 230–241)

- [ ] **Step 2: Delete the four dead models from contracts.py**

Delete `PlannerTask` class (lines 15–22):
```python
class PlannerTask(BaseModel):
    """A single task within a planner output."""

    model_config = ConfigDict(extra="ignore")

    task_type: str
    input_data: dict[str, Any] = Field(default_factory=dict)
```

Delete `PlannerOutput` class (lines 24–67):
```python
class PlannerOutput(BaseModel):
    ...entire class...
```

Delete `InstructionSpec` class (lines 70–78):
```python
class InstructionSpec(BaseModel):
    ...entire class...
```

Delete `ExecutionPlan` class (lines 230–241):
```python
class ExecutionPlan(BaseModel):
    ...entire class...
```

- [ ] **Step 3: Verify contracts.py still exports all live models**

The surviving models should be: `AgentEnvelope`, `AgentResult`, `StepResult`, `ToolCallRequest`, `ToolCallResult`, `SpanToolCall`, `SpanRecord`, `MessageToolCall`, `MessageAgentStep`, `MessageMetadata`, `DomainEvent`, `PerceptionDecision`, `PolicyDecision`, `RealtimeEventPayload`, `WorkspaceSurfacePush`, `CapabilityGap`, `PlanStep`, `PlanOutput`.

Run:
```bash
cd backend && python -c "from src.orchestrator.contracts import PlanOutput, PolicyDecision, SpanRecord, DomainEvent; print('OK')"
```
Expected: `OK`

- [ ] **Step 4: Verify dead models are gone**

```bash
cd backend && python -c "from src.orchestrator.contracts import PlannerOutput" 2>&1
```
Expected: `ImportError`

- [ ] **Step 5: Commit**

```bash
git add backend/src/orchestrator/contracts.py
git commit -m "feat(spec1b-iii): delete PlannerOutput, PlannerTask, InstructionSpec, ExecutionPlan from contracts"
```

---

## Task 3: Delete Dead Prompts

**Files:**
- Modify: `backend/src/orchestrator/prompts.py`

- [ ] **Step 1: Delete dead prompt constants**

Delete the following from `prompts.py`:

1. `JARVIS_DECISION_FRAMEWORK` (lines 40–77) — the entire multi-line string constant
2. `JARVIS_SOUL` alias (line 80): `JARVIS_SOUL = JARVIS_SOUL_CORE + "\n" + JARVIS_DECISION_FRAMEWORK`
3. `OBSERVER_PROMPT` (lines 82–120) — entire multi-line string constant
4. `RESEARCHER_PROMPT` (lines 769–822) — entire multi-line string constant
5. `PLANNER_PROMPT` (lines 141–226) — the old v1 planner prompt (PLANNER_PROMPT_V2 is the active one, registered as `"planner"` in AGENT_PROMPTS)

- [ ] **Step 2: Verify AGENT_PROMPTS dict is unchanged**

After deletion, `AGENT_PROMPTS` should still be:
```python
AGENT_PROMPTS = {
    "perceiver": PERCEIVER_PROMPT,
    "librarian": LIBRARIAN_PROMPT,
    "planner": PLANNER_PROMPT_V2,
    "governor": GOVERNOR_PROMPT,
    "operator": OPERATOR_PROMPT,
    "presenter": PRESENTER_PROMPT,
    "persona": PERSONA_PROMPT,
}
```

Run:
```bash
cd backend && python -c "from src.orchestrator.prompts import AGENT_PROMPTS, JARVIS_SOUL_CORE; print(sorted(AGENT_PROMPTS.keys())); print('SOUL OK' if len(JARVIS_SOUL_CORE) > 100 else 'FAIL')"
```
Expected: `['governor', 'librarian', 'operator', 'perceiver', 'persona', 'planner', 'presenter']` and `SOUL OK`

- [ ] **Step 3: Verify dead constants are gone**

```bash
cd backend && python -c "from src.orchestrator.prompts import JARVIS_SOUL" 2>&1
```
Expected: `ImportError`

```bash
cd backend && python -c "from src.orchestrator.prompts import OBSERVER_PROMPT" 2>&1
```
Expected: `ImportError`

- [ ] **Step 4: Commit**

```bash
git add backend/src/orchestrator/prompts.py
git commit -m "feat(spec1b-iii): delete JARVIS_DECISION_FRAMEWORK, JARVIS_SOUL, OBSERVER_PROMPT, RESEARCHER_PROMPT, old PLANNER_PROMPT"
```

---

## Task 4: Rewrite _handle_system_capability in jarvis.py

**Files:**
- Modify: `backend/src/orchestrator/jarvis.py`

This is the only place in production code that still imports `PlannerOutput`, `PlannerTask`, and `InstructionSpec`. The bridge pattern was explicitly deferred to this spec.

- [ ] **Step 1: Read the current _handle_system_capability method**

Current code (lines 2546–2610) bridges PlanStep → PlannerOutput for legacy handlers. After this rewrite, handlers accept PlanStep + PlanOutput directly.

- [ ] **Step 2: Rewrite _handle_system_capability to use PlanStep directly**

Replace the entire method body. The new version reads data directly from `step.input` instead of bridging through PlannerOutput:

```python
    async def _handle_system_capability(
        self,
        step: PlanStep,
        plan: PlanOutput,
        user_id: str,
        workspace_id: str,
    ) -> dict:
        """Route system.* capability steps to direct handlers."""
        cap = step.capability

        if cap in ("system.respond", "system.acknowledge"):
            return {}

        known_system_caps = {
            "system.set_goal",
            "system.set_instruction",
            "system.schedule_reminder",
            "system.add_to_brief",
        }
        if cap not in known_system_caps:
            logger.warning("Unknown system capability: %s", cap)
            return {}

        goal_text = step.description or plan.goal
        reasoning = plan.reasoning

        if cap == "system.set_goal":
            return await self._handle_set_goal_v2(
                goal_text, reasoning, plan.priority, user_id, workspace_id
            )
        elif cap == "system.set_instruction":
            instruction = step.input.get("instruction", {})
            return await self._handle_set_instruction_v2(
                goal_text, reasoning, instruction, user_id, workspace_id
            )
        elif cap == "system.schedule_reminder":
            tasks = step.input.get("tasks", [])
            return await self._handle_schedule_reminder_v2(
                goal_text, reasoning, tasks, user_id, workspace_id
            )
        elif cap == "system.add_to_brief":
            return await self._handle_add_to_brief(
                step, user_id, workspace_id
            )

        return {}
```

- [ ] **Step 3: Write v2 handler methods**

Add these methods to JarvisOrchestrator. They replace the PlannerOutput-based handlers with direct-argument versions. The existing `_handle_set_goal`, `_handle_set_instruction`, `_handle_schedule_reminder` remain temporarily until all callers are migrated, then get deleted in Step 5.

`_handle_set_goal_v2`:
```python
    async def _handle_set_goal_v2(
        self,
        goal_text: str,
        reasoning: str,
        priority: str,
        user_id: str,
        workspace_id: str,
    ) -> dict:
        """Store a goal as a memory."""
        if not self._services.memory_service:
            return {"status": "skipped", "reason": "no memory service"}
        try:
            memory_id = await self._services.memory_service.store_goal_memory(
                user_id=user_id,
                workspace_id=workspace_id,
                goal_text=goal_text,
                reasoning=reasoning,
                priority=priority,
            )
            return {"status": "stored", "memory_id": memory_id, "goal": goal_text}
        except Exception as e:
            logger.warning("Failed to store goal: %s", e)
            return {"status": "error", "error": str(e)}
```

`_handle_set_instruction_v2`:
```python
    async def _handle_set_instruction_v2(
        self,
        instruction_text: str,
        reasoning: str,
        instruction: dict,
        user_id: str,
        workspace_id: str,
    ) -> dict:
        """Store an instruction as a preference memory, optionally create trigger/schedule."""
        if not self._services.memory_service:
            return {"status": "skipped", "reason": "no memory service"}
        try:
            memory_id = await self._services.memory_service.store_instruction_memory(
                user_id=user_id,
                workspace_id=workspace_id,
                instruction_text=instruction_text,
                reasoning=reasoning,
            )
            result: dict = {"status": "stored", "memory_id": memory_id}

            instruction_type = instruction.get("instruction_type", "preference")
            if instruction_type == "trigger" and instruction.get("trigger_conditions"):
                try:
                    async with self._db_factory() as db:
                        from src.models.triggers import Trigger
                        from ulid import ULID

                        trigger = Trigger(
                            trigger_id=f"trig_{ULID()}",
                            user_id=user_id,
                            workspace_id=workspace_id,
                            name=instruction_text[:100],
                            conditions=instruction["trigger_conditions"],
                            action_type="custom_agent_task",
                            action_config={"instructions": instruction_text},
                            enabled=True,
                        )
                        db.add(trigger)
                        await db.commit()
                        result["trigger_id"] = trigger.trigger_id
                except Exception as e:
                    logger.warning("Failed to create trigger: %s", e)

            elif instruction_type == "schedule" and instruction.get("schedule_config"):
                try:
                    async with self._db_factory() as db:
                        from src.models.schedules import Schedule
                        from ulid import ULID

                        config = instruction["schedule_config"]
                        schedule = Schedule(
                            schedule_id=f"sched_{ULID()}",
                            user_id=user_id,
                            workspace_id=workspace_id,
                            name=instruction_text[:100],
                            schedule_type="recurring",
                            cron_expr=config.get("cron_expr", "0 9 * * *"),
                            action_type=config.get("action_type", "custom_agent_task"),
                            action_config={"instructions": instruction_text},
                            enabled=True,
                        )
                        db.add(schedule)
                        await db.commit()
                        result["schedule_id"] = schedule.schedule_id
                except Exception as e:
                    logger.warning("Failed to create schedule: %s", e)

            return result
        except Exception as e:
            logger.warning("Failed to store instruction: %s", e)
            return {"status": "error", "error": str(e)}
```

`_handle_schedule_reminder_v2`:
```python
    async def _handle_schedule_reminder_v2(
        self,
        reminder_text: str,
        reasoning: str,
        tasks: list[dict],
        user_id: str,
        workspace_id: str,
    ) -> dict:
        """Create a one-shot schedule for a reminder."""
        try:
            async with self._db_factory() as db:
                from src.models.schedules import Schedule
                from ulid import ULID

                remind_at = None
                for t in tasks:
                    input_data = t.get("input_data", {})
                    if input_data.get("remind_at"):
                        remind_at = input_data["remind_at"]
                        break

                schedule = Schedule(
                    schedule_id=f"sched_{ULID()}",
                    user_id=user_id,
                    workspace_id=workspace_id,
                    name=f"Reminder: {reminder_text[:80]}",
                    schedule_type="one_shot",
                    cron_expr=None,
                    action_type="custom_agent_task",
                    action_config={
                        "instructions": f"Remind user: {reminder_text}",
                        "remind_at": remind_at,
                    },
                    enabled=True,
                )
                if remind_at:
                    from datetime import datetime
                    try:
                        schedule.next_run_at = datetime.fromisoformat(remind_at)
                    except (ValueError, TypeError):
                        pass
                db.add(schedule)
                await db.commit()
                return {
                    "status": "scheduled",
                    "schedule_id": schedule.schedule_id,
                    "reminder": reminder_text,
                }
        except Exception as e:
            logger.warning("Failed to schedule reminder: %s", e)
            return {"status": "error", "error": str(e)}
```

- [ ] **Step 4: Update _handle_add_to_brief to accept PlanStep directly**

The current `_handle_add_to_brief` takes a PlannerOutput. Update its signature:

```python
    async def _handle_add_to_brief(
        self, step_or_plan, user_id: str, workspace_id: str
    ) -> dict:
        """Store a briefing item as a memory."""
        # Accept either a PlanStep or a legacy PlannerOutput
        if hasattr(step_or_plan, 'description'):
            text = step_or_plan.description
        elif hasattr(step_or_plan, 'goal'):
            text = step_or_plan.goal
        else:
            text = str(step_or_plan)
        ...rest of method stays the same...
```

- [ ] **Step 5: Remove old PlannerOutput-based handlers**

Delete the old `_handle_set_goal`, `_handle_set_instruction`, `_handle_schedule_reminder` methods that accept `PlannerOutput`. These were the bridge targets.

- [ ] **Step 6: Remove PlannerOutput imports from jarvis.py**

In `backend/src/orchestrator/jarvis.py` line 30, change:
```python
from src.orchestrator.contracts import PlannerOutput, PlanOutput, PlanStep
```
to:
```python
from src.orchestrator.contracts import PlanOutput, PlanStep
```

Also remove the lazy import at line 2558:
```python
from src.orchestrator.contracts import InstructionSpec, PlannerTask
```

- [ ] **Step 7: Fix error return strings in process_message**

In `backend/src/orchestrator/jarvis.py`, the early-return error dicts use `"decision": "error"` and `"decision": "ignore"`. These are the old convention. Change them:

Line 730: `return {"error": "user_id is required", "decision": "error"}` → `return {"error": "user_id is required"}`
Line 732: `return {"error": "workspace_id is required", "decision": "error"}` → `return {"error": "workspace_id is required"}`
Line 734: `return {"error": "Empty message", "decision": "ignore"}` → `return {"error": "Empty message"}`

- [ ] **Step 8: Run tests**

```bash
cd backend && python -m pytest tests/test_orchestrator_routing.py tests/test_perception_execution.py -x -v --timeout=30 2>&1 | tail -30
```

- [ ] **Step 9: Commit**

```bash
git add backend/src/orchestrator/jarvis.py
git commit -m "feat(spec1b-iii): rewrite _handle_system_capability for PlanStep, remove PlannerOutput bridge"
```

---

## Task 5: Update Governor for Capability-Based Policy

**Files:**
- Modify: `backend/src/services/governor.py`
- Test: `backend/tests/test_governor_capability.py` (new)

The governor's `_apply_policy` currently reads `plan.decision` to match against string sets (`APPROVAL_REQUIRED_ACTIONS`, `AUTO_EXECUTE_DECISIONS`, etc.). After this change, it reads `plan.risk_level` and iterates plan task step capabilities.

- [ ] **Step 1: Write failing tests for capability-based governor policy**

Create `backend/tests/test_governor_capability.py`:

```python
"""Tests for governor capability-based policy evaluation."""

import pytest
from unittest.mock import AsyncMock, MagicMock

from src.services.governor import Governor


def _make_plan(
    risk_level: str = "low",
    decision: str = "",
    task_types: list[str] | None = None,
):
    """Build a mock Plan object."""
    plan = MagicMock()
    plan.plan_id = "plan_test"
    plan.goal = "Test plan"
    plan.decision = decision
    plan.risk_level = risk_level
    plan.reasoning_summary = "Test"
    plan.tasks = []
    if task_types:
        for tt in task_types:
            task = MagicMock()
            task.task_type = tt
            plan.tasks.append(task)
    return plan


class TestCapabilityBasedPolicy:
    @pytest.mark.asyncio
    async def test_low_risk_read_plan_auto_executes(self):
        """Low-risk plans with no write actions should auto-execute in full_auto mode."""
        db = AsyncMock()
        gov = Governor(db=db)
        gov._get_policy_mode = AsyncMock(return_value="full_auto")
        plan = _make_plan(risk_level="low")
        result = await gov._apply_policy(plan, "usr_1")
        assert result == "auto_execute"

    @pytest.mark.asyncio
    async def test_high_risk_requires_approval(self):
        """High-risk plans always require approval."""
        db = AsyncMock()
        gov = Governor(db=db)
        gov._get_policy_mode = AsyncMock(return_value="full_auto")
        plan = _make_plan(risk_level="high")
        result = await gov._apply_policy(plan, "usr_1")
        assert result == "approval_required"

    @pytest.mark.asyncio
    async def test_critical_risk_requires_approval(self):
        """Critical risk always requires approval regardless of mode."""
        db = AsyncMock()
        gov = Governor(db=db)
        gov._get_policy_mode = AsyncMock(return_value="full_auto")
        plan = _make_plan(risk_level="critical")
        result = await gov._apply_policy(plan, "usr_1")
        assert result == "approval_required"

    @pytest.mark.asyncio
    async def test_lockdown_blocks_everything(self):
        db = AsyncMock()
        gov = Governor(db=db)
        gov._get_policy_mode = AsyncMock(return_value="lockdown")
        plan = _make_plan(risk_level="low")
        result = await gov._apply_policy(plan, "usr_1")
        assert result == "blocked"

    @pytest.mark.asyncio
    async def test_suggest_only_blocks(self):
        db = AsyncMock()
        gov = Governor(db=db)
        gov._get_policy_mode = AsyncMock(return_value="suggest_only")
        plan = _make_plan(risk_level="low")
        result = await gov._apply_policy(plan, "usr_1")
        assert result == "blocked"

    @pytest.mark.asyncio
    async def test_default_mode_low_risk_requires_approval(self):
        """Default approval_required mode: even low-risk requires approval."""
        db = AsyncMock()
        gov = Governor(db=db)
        gov._get_policy_mode = AsyncMock(return_value="approval_required")
        plan = _make_plan(risk_level="low")
        result = await gov._apply_policy(plan, "usr_1")
        assert result == "approval_required"
```

- [ ] **Step 2: Run tests to verify they fail**

```bash
cd backend && python -m pytest tests/test_governor_capability.py -x -v --timeout=30 2>&1 | tail -20
```

Some tests may already pass since the old code has overlapping behavior. That's fine.

- [ ] **Step 3: Rewrite _apply_policy for capability-based evaluation**

In `backend/src/services/governor.py`, replace `_apply_policy` and the old decision-string constants:

Remove the old constants:
```python
APPROVAL_REQUIRED_ACTIONS = {
    "draft_reply",
    "draft_email",
    ...
}

AUTO_EXECUTE_DECISIONS = {
    "fetch_info",
    "summarize",
    ...
}
```

Replace with capability-based risk assessment:
```python
# Capabilities that always require approval regardless of mode
CRITICAL_CAPABILITIES = {
    "payment",
    "deploy",
    "delete_data",
    "modify_permissions",
    "security_change",
}

VALID_POLICY_MODES = {"lockdown", "approval_required", "suggest_only", "full_auto"}
```

Rewrite `_apply_policy`:
```python
    async def _apply_policy(self, plan: Plan, user_id: str) -> str:
        """Apply policy rules based on plan risk level and user settings."""
        risk = plan.risk_level or "low"
        policy_mode = await self._get_policy_mode(user_id)

        # Lockdown: block everything
        if policy_mode == "lockdown":
            return "blocked"

        # Suggest-only: never execute
        if policy_mode == "suggest_only":
            return "blocked"

        # Critical risk always requires approval, even in full_auto
        if risk == "critical":
            return "approval_required"

        # Full auto mode: auto-execute unless high-risk
        if policy_mode == "full_auto":
            if risk == "high":
                return "approval_required"
            return "auto_execute"

        # Default: approval_required mode
        if risk == "high":
            return "approval_required"

        # Trust-based graduation for medium-risk
        if risk == "medium":
            if await self._check_trust(user_id, "write", risk):
                return "auto_execute"
            return "approval_required"

        # Low/none risk in approval_required mode — check trust
        if await self._check_trust(user_id, "read", risk):
            return "auto_execute"

        # Default: require approval for safety
        return "approval_required"
```

Also update `_create_approval` to not reference `plan.decision` or `task_types`:

In `_create_approval`, change line 226:
```python
            approval_type=task_types[0] if task_types else plan.decision,
```
to:
```python
            approval_type=plan.risk_level or "medium",
```

And remove the task_types extraction (lines 219-220):
```python
        task_types = []
        if plan.tasks:
            task_types = [t.task_type for t in plan.tasks]
```

- [ ] **Step 4: Run tests**

```bash
cd backend && python -m pytest tests/test_governor_capability.py -x -v --timeout=30 2>&1 | tail -20
```
Expected: all pass.

- [ ] **Step 5: Commit**

```bash
git add backend/src/services/governor.py backend/tests/test_governor_capability.py
git commit -m "feat(spec1b-iii): rewrite governor _apply_policy for capability-based risk evaluation"
```

---

## Task 6: Update Metrics Service

**Files:**
- Modify: `backend/src/services/metrics_service.py`

- [ ] **Step 1: Update PLANS_CREATED counter label**

In `backend/src/services/metrics_service.py`, change:
```python
PLANS_CREATED = Counter(
    "jarvis_plans_created_total",
    "Total plans created",
    ["decision"],
)
```
to:
```python
PLANS_CREATED = Counter(
    "jarvis_plans_created_total",
    "Total plans created",
    ["capability"],
)
```

- [ ] **Step 2: Update record_plan_created method**

Change:
```python
    @staticmethod
    def record_plan_created(decision: str) -> None:
        PLANS_CREATED.labels(decision=decision).inc()
```
to:
```python
    @staticmethod
    def record_plan_created(capability: str) -> None:
        PLANS_CREATED.labels(capability=capability).inc()
```

- [ ] **Step 3: Find and update all callers of record_plan_created**

```bash
rg "record_plan_created" backend/src/
```

Update any callers to pass capability instead of decision. (If callers are in jarvis.py, they should already pass capability from PlanStep.)

- [ ] **Step 4: Commit**

```bash
git add backend/src/services/metrics_service.py
git commit -m "feat(spec1b-iii): update metrics PLANS_CREATED label from decision to capability"
```

---

## Task 7: Update Scheduler and Surface Builder

**Files:**
- Modify: `backend/src/services/scheduler.py`
- Modify: `backend/src/services/surface_builder.py`

- [ ] **Step 1: Fix scheduler wake_agent observer → perceiver**

In `backend/src/services/scheduler.py`, in the `_fire` method, around line 736:

Change:
```python
            agent = config.get("agent", "observer")
            source = config.get("source")
            if agent == "observer" and source:
```
to:
```python
            agent = config.get("agent", "perceiver")
            source = config.get("source")
            if agent == "perceiver" and source:
```

- [ ] **Step 2: Update surface_builder persisted surface payload key**

In `backend/src/services/surface_builder.py`, `_load_persisted_surfaces` method, around line 277:

Change:
```python
                        "decision": payload.get("decision"),
```
to:
```python
                        "capability": payload.get("capability"),
```

- [ ] **Step 3: Commit**

```bash
git add backend/src/services/scheduler.py backend/src/services/surface_builder.py
git commit -m "feat(spec1b-iii): scheduler observer→perceiver, surface_builder decision→capability"
```

---

## Task 8: Add Alembic Migration to Drop agent_routes Table

**Files:**
- Create: `backend/alembic/versions/xxx_drop_agent_routes_table.py`

- [ ] **Step 1: Generate Alembic migration**

```bash
cd backend && alembic revision --autogenerate -m "drop agent_routes table"
```

- [ ] **Step 2: Verify the migration drops agent_routes**

Read the generated file and verify it contains:
```python
def upgrade():
    op.drop_index('ix_agent_routes_decision_type', table_name='agent_routes')
    op.drop_index('ix_agent_routes_enabled', table_name='agent_routes')
    op.drop_index('ix_agent_routes_priority', table_name='agent_routes')
    op.drop_table('agent_routes')
```

If autogenerate didn't detect it (because the model was already deleted), write the migration manually:

```python
"""drop agent_routes table

Revision ID: <auto>
"""
from alembic import op


def upgrade():
    op.drop_index('ix_agent_routes_decision_type', table_name='agent_routes')
    op.drop_index('ix_agent_routes_enabled', table_name='agent_routes')
    op.drop_index('ix_agent_routes_priority', table_name='agent_routes')
    op.drop_table('agent_routes')


def downgrade():
    # Not reversible — table and data are permanently dropped
    pass
```

- [ ] **Step 3: Commit**

```bash
git add backend/alembic/versions/
git commit -m "feat(spec1b-iii): add migration to drop agent_routes table"
```

---

## Task 9: Rewrite and Delete Test Files

**Files:**
- Delete: `backend/tests/test_route_resolver.py`
- Delete: `backend/tests/test_ignore_decision.py`
- Modify: `backend/tests/test_contracts.py`
- Modify: `backend/tests/test_contracts_v2.py`
- Modify: `backend/tests/test_orchestrator.py`
- Modify: `backend/tests/test_planner_structured.py`
- Modify: `backend/tests/test_integration_audit.py`

- [ ] **Step 1: Delete test_route_resolver.py**

```bash
rm backend/tests/test_route_resolver.py
```

- [ ] **Step 2: Delete test_ignore_decision.py**

```bash
rm backend/tests/test_ignore_decision.py
```

- [ ] **Step 3: Rewrite test_contracts.py — remove PlannerOutput and PlannerTask tests**

Remove these classes entirely:
- `TestPlannerTask` (lines 20–33)
- `TestPlannerOutput` (lines 38–133)

Update imports — remove `PlannerOutput`, `PlannerTask`:
```python
from src.orchestrator.contracts import (
    AgentEnvelope,
    AgentResult,
    DomainEvent,
    StepResult,
    ToolCallRequest,
    ToolCallResult,
)
```

The remaining test classes (`TestAgentEnvelope`, `TestAgentResult`, `TestStepResult`, `TestToolCallRequest`, `TestToolCallResult`, `TestDomainEvent`) stay unchanged.

- [ ] **Step 4: Rewrite test_contracts_v2.py — remove ExecutionPlan tests**

Remove `TestExecutionPlan` class (lines 16–79).

Update imports — remove `ExecutionPlan`, `PlannerTask`:
```python
from src.orchestrator.contracts import (
    DomainEvent,
    PolicyDecision,
)
```

The remaining test classes (`TestPolicyDecision`, `TestDomainEvent`) stay unchanged.

- [ ] **Step 5: Update test_orchestrator.py — fix TestPrompts**

In `TestPrompts`, remove tests that reference deleted constants:

Remove `test_jarvis_soul_not_empty` (references `JARVIS_SOUL`).
Remove `test_planner_prompt_mentions_json` (references old `PLANNER_PROMPT`).

Add replacement test:
```python
    def test_jarvis_soul_core_not_empty(self):
        from src.orchestrator.prompts import JARVIS_SOUL_CORE

        assert len(JARVIS_SOUL_CORE) > 100
        assert "operating system" in JARVIS_SOUL_CORE.lower()

    def test_planner_v2_prompt_mentions_json(self):
        from src.orchestrator.prompts import PLANNER_PROMPT_V2

        assert "JSON" in PLANNER_PROMPT_V2
```

- [ ] **Step 6: Rewrite test_planner_structured.py**

Replace entire file. Tests PlanOutput instead of PlannerOutput:

```python
"""Tests for PlanOutput contract (capability-based planning)."""

from src.orchestrator.contracts import PlanOutput, PlanStep


def _valid_plan_data(**overrides) -> dict:
    """Factory for valid PlanOutput data."""
    data = {
        "goal": "Send investor update email",
        "reasoning": "User requested email draft",
        "priority": "high",
        "achievable": "full",
        "steps": [
            {
                "step_id": "s1",
                "description": "Draft email",
                "capability": "email.draft",
                "risk": "medium",
            }
        ],
        "success_criteria": "Email drafted and ready for review",
    }
    data.update(overrides)
    return data


class TestPlanOutputContract:
    def test_valid_minimal(self):
        output = PlanOutput(goal="Test")
        assert output.goal == "Test"
        assert output.steps == []
        assert output.priority == "medium"
        assert output.achievable == "full"

    def test_valid_full(self):
        output = PlanOutput(**_valid_plan_data())
        assert len(output.steps) == 1
        assert output.steps[0].capability == "email.draft"
        assert output.priority == "high"

    def test_extra_fields_ignored(self):
        output = PlanOutput(goal="Test", extra_stuff="nope")
        assert not hasattr(output, "extra_stuff")

    def test_model_json_schema_has_required_fields(self):
        schema = PlanOutput.model_json_schema()
        assert "goal" in str(schema)
        assert "properties" in schema

    def test_model_dump_roundtrip(self):
        data = _valid_plan_data()
        output = PlanOutput.model_validate(data)
        dumped = output.model_dump()
        reparsed = PlanOutput.model_validate(dumped)
        assert reparsed.goal == data["goal"]
        assert len(reparsed.steps) == 1

    def test_plan_step_defaults(self):
        step = PlanStep(description="Read", capability="email.read")
        assert step.actor == "jarvis"
        assert step.risk == "none"
        assert step.depends_on == []

    def test_not_achievable(self):
        output = PlanOutput(
            goal="Impossible task",
            achievable="not_achievable",
            capability_gaps=[{"description": "Missing X", "resolution": "Connect X"}],
        )
        assert output.achievable == "not_achievable"
        assert len(output.capability_gaps) == 1
```

- [ ] **Step 7: Update test_integration_audit.py — remove TestRouteAnalytics**

In `backend/tests/test_integration_audit.py`, delete the `TestRouteAnalytics` class (which imports from `src.services.route_analytics`).

- [ ] **Step 8: Run all tests**

```bash
cd backend && python -m pytest tests/ -x -q --timeout=30 2>&1 | tail -20
```
Expected: all pass.

- [ ] **Step 9: Commit**

```bash
git add -A backend/tests/
git commit -m "test(spec1b-iii): rewrite 5 test files, delete 2 test files for post-switchover state"
```

---

## Task 10: String Grep Sweep — Zero Decision-Type Strings in backend/src/

**Files:**
- Potentially any file in `backend/src/` with remaining references

- [ ] **Step 1: Run the sweep**

```bash
rg '"create_task"|"draft_reply"|"read_source"|"research"|"observe"|"remember"|"acknowledge"|"answer_directly"|"search_memory"|"add_to_brief"|"ignore"|"watcher_create"|"goal_update"|"recommend"|"summarize"|"schedule_reminder"|"set_goal"|"set_instruction"|"ask_user"' backend/src/
```

- [ ] **Step 2: Categorize and address every hit**

For each remaining hit:

1. **In `prompts.py`**: Should all be gone after Task 3. If any remain in PLANNER_PROMPT_V2 examples — these are examples in the *old v1 prompt* which was deleted. The v2 prompt uses capability names, not decision types. If any literal strings exist in prompts that are part of the *active* prompt text (like in example JSON), they should be changed to capability-based equivalents.

2. **In `contracts.py`**: Should all be gone after Task 2 (PlannerOutput deleted). If `ConfigDict(extra="ignore")` matches, that's a false positive — `"ignore"` in `extra="ignore"` is a Pydantic config, not a decision type. Exclude these.

3. **In `jarvis.py`**: Should be gone after Task 4. The `"decision": "ignore"` error return was fixed. Check for any remaining.

4. **In `ui/contracts.py`**: `ConfigDict(extra="ignore")` — false positive (Pydantic config).

5. **In `config/settings.py`**: `"extra": "ignore"` — false positive (Pydantic config).

6. **In `config/logging.py`**: `warnings.filterwarnings("ignore", ...)` — false positive (Python warnings).

7. **In `workflows/research_agent.py`**: `name="research"` — this is a tool name, not a decision type. Acceptable if it's a tool/capability name.

8. **In `services/tool_registry.py`**: `"research": ["internal", "browser"]` — this is a capability family mapping, not a decision type. Acceptable.

**Rule**: Any hit that is:
- A Pydantic `extra="ignore"` config → **false positive, skip**
- A Python `warnings.filterwarnings("ignore")` → **false positive, skip**
- A tool name or capability name → **acceptable, not a decision type**
- An actual decision-type string being compared/routed → **must be fixed**

- [ ] **Step 3: Fix any real hits**

Address each genuine decision-type reference found.

- [ ] **Step 4: Re-run sweep to confirm zero real hits**

```bash
rg '"create_task"|"draft_reply"|"read_source"|"research"|"observe"|"remember"|"acknowledge"|"answer_directly"|"search_memory"|"add_to_brief"|"ignore"|"watcher_create"|"goal_update"|"recommend"|"summarize"|"schedule_reminder"|"set_goal"|"set_instruction"|"ask_user"' backend/src/ | grep -v 'extra="ignore"' | grep -v 'filterwarnings' | grep -v 'ConfigDict'
```

Expected: zero hits after filtering false positives. Real decision-type routing strings should be completely gone.

- [ ] **Step 5: Run full test suite**

```bash
cd backend && python -m pytest tests/ -x -q --timeout=30 2>&1 | tail -20
```
Expected: all pass.

- [ ] **Step 6: Commit (if any fixes were needed)**

```bash
git add -A backend/src/
git commit -m "feat(spec1b-iii): clean remaining decision-type string references"
```

---

## Task 11: Final Verification

- [ ] **Step 1: Verify zero PlannerOutput references in src/**

```bash
rg "PlannerOutput|PlannerTask|InstructionSpec|ExecutionPlan" backend/src/
```
Expected: zero hits.

- [ ] **Step 2: Verify zero RouteResolver references in src/**

```bash
rg "RouteResolver|route_resolver|route_analytics|AgentRoute|agent_routes" backend/src/
```
Expected: zero hits.

- [ ] **Step 3: Verify zero dead prompt references in src/**

```bash
rg "JARVIS_DECISION_FRAMEWORK|JARVIS_SOUL[^_]|OBSERVER_PROMPT|RESEARCHER_PROMPT" backend/src/
```
Expected: zero hits. (`JARVIS_SOUL_CORE` should still exist.)

- [ ] **Step 4: Run full test suite**

```bash
cd backend && python -m pytest tests/ -v --timeout=30 2>&1 | tail -40
```
Expected: all pass.

- [ ] **Step 5: Run ruff format and lint**

```bash
cd backend && ruff format src/ tests/ && ruff check src/ tests/ --fix
```

- [ ] **Step 6: Final commit (if formatting changes)**

```bash
git add -A && git commit -m "chore(spec1b-iii): ruff format"
```
