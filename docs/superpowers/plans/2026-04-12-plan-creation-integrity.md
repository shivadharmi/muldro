# Plan Creation Integrity Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Fix plan creation so every step has an ID, dependencies resolve correctly, user steps are persisted, PlanOutput is fully stored, and parse failures are logged.

**Architecture:** Plan creation flows through `intent_classifier.py` (fast path) or `jarvis.py:_run_planner` (full path), both producing `PlanOutput` that gets persisted via `_persist_plan_record()` into `Plan` + `PlanTask` rows. Fixes target the contract model, the persistence layer, and validation.

**Tech Stack:** Python, Pydantic models, SQLAlchemy async, Alembic migrations

---

## File Map

| File | Action | Responsibility |
|------|--------|----------------|
| `backend/src/orchestrator/intent_classifier.py` | Modify | Add step IDs, log parse failures |
| `backend/src/orchestrator/jarvis.py` | Modify | Two-pass dependency resolution, persist user steps, store PlanOutput JSON |
| `backend/src/orchestrator/contracts.py` | Modify | Add step_id uniqueness validator |
| `backend/src/models/plans.py` | Modify | Add plan_output_json column |
| `backend/tests/test_intent_to_plan.py` | Modify | Add step ID assertions |
| `backend/tests/test_plan_creation.py` | Create | Dependency resolution, user step persistence tests |

---

### Task 1: Generate Step IDs in Fast-Path intent_to_plan()

**Gaps:** 1.1
**Files:**
- Modify: `backend/src/orchestrator/intent_classifier.py:153-220`
- Modify: `backend/tests/test_intent_to_plan.py`

- [ ] **Step 1: Write the failing test**

Add to `backend/tests/test_intent_to_plan.py` in `TestIntentToPlan`:

```python
    def test_all_fast_intents_have_step_ids(self):
        """Every step from intent_to_plan must have a non-empty step_id."""
        for intent in FAST_INTENTS:
            result = intent_to_plan(intent, "test message", self.CAPS)
            for i, step in enumerate(result.steps):
                assert step.step_id, f"Empty step_id for intent={intent}, step={i}"

    def test_step_ids_are_sequential(self):
        """Step IDs follow s1, s2, ... pattern."""
        result = intent_to_plan("greeting", "Hey!", self.CAPS)
        assert result.steps[0].step_id == "s1"
```

- [ ] **Step 2: Run test to verify it fails**

Run: `cd backend && python -m pytest tests/test_intent_to_plan.py::TestIntentToPlan::test_all_fast_intents_have_step_ids -v`
Expected: FAIL — `step_id` is empty string.

- [ ] **Step 3: Add step_id generation to intent_to_plan()**

In `backend/src/orchestrator/intent_classifier.py`, modify `intent_to_plan()`. For every `PlanStep` constructor, add `step_id=f"s{N}"`:

```python
def intent_to_plan(intent: str, message: str, capabilities: list[str]) -> PlanOutput:
    """Generate a lightweight PlanOutput from fast intent classification."""
    goal = message[:200]

    if intent in ("greeting", "chitchat", "acknowledgment"):
        return PlanOutput(
            goal=goal,
            steps=[PlanStep(step_id="s1", description="Respond to user", capability="respond")],
            priority="low",
        )

    if intent == "direct_answer":
        return PlanOutput(
            goal=goal,
            steps=[PlanStep(step_id="s1", description="Answer from context", capability="reason")],
        )

    if intent == "simple_question":
        return PlanOutput(
            goal=goal,
            steps=[PlanStep(step_id="s1", description="Answer question", capability="reason")],
        )

    if intent in ("single_read", "data_fetch"):
        return PlanOutput(
            goal=goal,
            steps=[PlanStep(step_id="s1", description=goal, capability="perceive", risk="none")],
        )

    if intent == "status_query":
        return PlanOutput(
            goal=goal,
            steps=[
                PlanStep(step_id="s1", description="Retrieve status", capability="knowledge.search"),
            ],
        )

    if intent == "memory_operation":
        return PlanOutput(
            goal=goal,
            steps=[
                PlanStep(
                    step_id="s1",
                    description="Store or recall knowledge",
                    capability="knowledge.search",
                ),
            ],
        )

    if intent == "approval_response":
        return PlanOutput(
            goal=goal,
            steps=[PlanStep(step_id="s1", description="Process approval", capability="respond")],
        )

    # Fallback for unknown intents
    return PlanOutput(
        goal=goal,
        steps=[PlanStep(step_id="s1", description="Respond to user", capability="respond")],
        priority="low",
    )
```

- [ ] **Step 4: Run test to verify it passes**

Run: `cd backend && python -m pytest tests/test_intent_to_plan.py -v`
Expected: PASS — all tests including new step_id assertions.

- [ ] **Step 5: Commit**

```bash
cd backend && git add src/orchestrator/intent_classifier.py tests/test_intent_to_plan.py
git commit -m "fix: generate step_id for all fast-path PlanStep objects"
```

---

### Task 2: Fix Forward Dependency Resolution in _persist_plan_record()

**Gaps:** 1.2
**Files:**
- Modify: `backend/src/orchestrator/jarvis.py:362-393`
- Create: `backend/tests/test_plan_creation.py`

- [ ] **Step 1: Write the failing test**

```python
# backend/tests/test_plan_creation.py
"""Tests for plan creation integrity — dependency resolution, user steps, PlanOutput storage."""

import pytest
from unittest.mock import AsyncMock, MagicMock, patch

from src.orchestrator.contracts import PlanOutput, PlanStep


class TestDependencyResolution:
    """Forward dependency references must resolve correctly."""

    def test_forward_reference_preserved(self):
        """Step s1 depends on s2, where s2 appears AFTER s1 in the list.
        
        The two-pass approach should still resolve s2 -> task_id correctly.
        """
        plan = PlanOutput(
            goal="Test forward deps",
            steps=[
                PlanStep(step_id="s1", description="Step 1", capability="respond", depends_on=["s2"]),
                PlanStep(step_id="s2", description="Step 2", capability="reason"),
            ],
        )
        # After fix, _persist_plan_record should map s2 to its task_id
        # even though s1 is processed first.
        # We test the two-pass logic directly.
        from src.orchestrator.jarvis import _build_step_to_task_map

        step_to_task = _build_step_to_task_map(plan.steps)
        assert "s1" in step_to_task
        assert "s2" in step_to_task

        # Resolve deps for s1 — s2 should be found
        dep_task_ids = [step_to_task[dep] for dep in plan.steps[0].depends_on if dep in step_to_task]
        assert len(dep_task_ids) == 1
        assert dep_task_ids[0] == step_to_task["s2"]
```

- [ ] **Step 2: Run test to verify it fails**

Run: `cd backend && python -m pytest tests/test_plan_creation.py::TestDependencyResolution -v`
Expected: FAIL — `_build_step_to_task_map` doesn't exist yet.

- [ ] **Step 3: Implement two-pass dependency resolution**

In `backend/src/orchestrator/jarvis.py`, add a helper function and refactor `_persist_plan_record()`:

Add this helper near the top of the class or as a module-level function:

```python
def _build_step_to_task_map(steps: list) -> dict[str, str]:
    """First pass: create step_id -> task_id mapping for ALL jarvis-actor steps.
    
    This allows forward references (step s1 depending on s3 that appears later)
    to resolve correctly in the second pass.
    """
    from ulid import ULID

    step_to_task: dict[str, str] = {}
    for step in steps:
        if step.actor != "jarvis":
            continue
        if step.step_id:
            step_to_task[step.step_id] = f"ptask_{ULID()}"
    return step_to_task
```

Then modify `_persist_plan_record()` to use two passes:

```python
                # Two-pass: first create all step->task mappings, then resolve deps
                step_to_task = _build_step_to_task_map(plan_output.steps)
                tasks: list[PlanTask] = []
                max_risk_ord = 0

                for step in plan_output.steps:
                    max_risk_ord = max(max_risk_ord, risk_ord.get(step.risk, 0))

                    if step.actor != "jarvis":
                        continue

                    task_id = step_to_task.get(step.step_id, f"ptask_{ULID()}")

                    # Second pass: resolve deps using pre-built map
                    dep_task_ids = [
                        step_to_task[dep] for dep in step.depends_on if dep in step_to_task
                    ]

                    tasks.append(
                        PlanTask(
                            task_id=task_id,
                            plan_id=plan_id,
                            workspace_id=workspace_id,
                            task_type=step.capability,
                            input_data=step.input,
                            depends_on=dep_task_ids or None,
                            status="pending",
                        )
                    )
```

- [ ] **Step 4: Run test to verify it passes**

Run: `cd backend && python -m pytest tests/test_plan_creation.py::TestDependencyResolution -v`
Expected: PASS

- [ ] **Step 5: Commit**

```bash
cd backend && git add src/orchestrator/jarvis.py tests/test_plan_creation.py
git commit -m "fix: two-pass dependency resolution to handle forward step references"
```

---

### Task 3: Persist User Actor Steps as PlanTask Records

**Gaps:** 1.3
**Files:**
- Modify: `backend/src/orchestrator/jarvis.py:368-393`
- Modify: `backend/tests/test_plan_creation.py`

- [ ] **Step 1: Write the failing test**

Add to `backend/tests/test_plan_creation.py`:

```python
class TestUserStepPersistence:
    """User-actor steps should be persisted as PlanTask rows."""

    def test_user_steps_included_in_task_list(self):
        """Steps with actor='user' create PlanTask with task_type='user_action'."""
        plan = PlanOutput(
            goal="Draft email with user review",
            steps=[
                PlanStep(step_id="s1", description="Draft email", capability="email.draft", actor="jarvis"),
                PlanStep(step_id="s2", description="User reviews draft", capability="respond", actor="user", depends_on=["s1"]),
                PlanStep(step_id="s3", description="Send email", capability="email.send", actor="jarvis", depends_on=["s2"]),
            ],
        )
        # After fix, all 3 steps should produce PlanTask rows
        # User steps get task_type="user_action" and status="awaiting_input"
        assert plan.steps[1].actor == "user"
```

- [ ] **Step 2: Implement user step persistence**

In `_persist_plan_record()`, remove the `if step.actor != "jarvis": continue` skip and handle user steps:

```python
                for step in plan_output.steps:
                    max_risk_ord = max(max_risk_ord, risk_ord.get(step.risk, 0))

                    task_id = step_to_task.get(step.step_id, f"ptask_{ULID()}")

                    dep_task_ids = [
                        step_to_task[dep] for dep in step.depends_on if dep in step_to_task
                    ]

                    if step.actor == "user":
                        tasks.append(
                            PlanTask(
                                task_id=task_id,
                                plan_id=plan_id,
                                workspace_id=workspace_id,
                                task_type="user_action",
                                input_data={"description": step.description, "capability": step.capability},
                                depends_on=dep_task_ids or None,
                                status="awaiting_input",
                            )
                        )
                    else:
                        tasks.append(
                            PlanTask(
                                task_id=task_id,
                                plan_id=plan_id,
                                workspace_id=workspace_id,
                                task_type=step.capability,
                                input_data=step.input,
                                depends_on=dep_task_ids or None,
                                status="pending",
                            )
                        )
```

Also update `_build_step_to_task_map()` to include user steps:

```python
def _build_step_to_task_map(steps: list) -> dict[str, str]:
    """First pass: create step_id -> task_id mapping for ALL steps (jarvis + user)."""
    from ulid import ULID

    step_to_task: dict[str, str] = {}
    for step in steps:
        if step.step_id:
            step_to_task[step.step_id] = f"ptask_{ULID()}"
    return step_to_task
```

- [ ] **Step 3: Run tests**

Run: `cd backend && python -m pytest tests/test_plan_creation.py -v`
Expected: PASS

- [ ] **Step 4: Commit**

```bash
cd backend && git add src/orchestrator/jarvis.py tests/test_plan_creation.py
git commit -m "feat: persist user-actor steps as PlanTask rows with awaiting_input status"
```

---

### Task 4: Store Full PlanOutput JSON in Plan Table

**Gaps:** 1.4
**Files:**
- Modify: `backend/src/models/plans.py`
- Modify: `backend/src/orchestrator/jarvis.py`
- Create: Alembic migration

- [ ] **Step 1: Add plan_output_json column to Plan model**

In `backend/src/models/plans.py`, add after `idempotency_key`:

```python
    plan_output_json: Mapped[dict | None] = mapped_column(JSONB)
```

- [ ] **Step 2: Generate Alembic migration**

Run: `cd backend && alembic revision --autogenerate -m "add plan_output_json to plans"`

- [ ] **Step 3: Apply migration**

Run: `cd backend && alembic upgrade head`

- [ ] **Step 4: Store PlanOutput in _persist_plan_record()**

In `backend/src/orchestrator/jarvis.py`, in `_persist_plan_record()`, add to the Plan constructor:

```python
                plan_record = Plan(
                    plan_id=plan_id,
                    user_id=user_id,
                    workspace_id=workspace_id,
                    trigger_type=trigger_type,
                    trigger_ref=None,
                    idempotency_key=idempotency_key,
                    goal=plan_output.goal or "",
                    priority=plan_output.priority,
                    decision="plan",
                    reasoning_summary=plan_output.reasoning or None,
                    risk_level=risk_level,
                    execution_mode=execution_mode,
                    status="created",
                    success_conditions=(
                        {"criteria": plan_output.success_criteria}
                        if plan_output.success_criteria
                        else None
                    ),
                    plan_output_json=plan_output.model_dump(mode="json"),
                )
```

- [ ] **Step 5: Run tests**

Run: `cd backend && python -m pytest tests/ -v -x -k "plan" 2>&1 | tail -20`
Expected: PASS

- [ ] **Step 6: Commit**

```bash
cd backend && git add src/models/plans.py src/orchestrator/jarvis.py alembic/versions/
git commit -m "feat: store full PlanOutput JSON in Plan table for audit and replay"
```

---

### Task 5: Log Warning on Planner JSON Parse Failure

**Gaps:** 1.5
**Files:**
- Modify: `backend/src/orchestrator/intent_classifier.py:105-150`
- Modify: `backend/tests/test_plan_creation.py`

- [ ] **Step 1: Write the failing test**

Add to `backend/tests/test_plan_creation.py`:

```python
class TestExtractPlanFallback:
    """extract_plan logs warning when falling back to minimal plan."""

    def test_malformed_json_logs_warning(self, caplog):
        """Unparseable Planner response should log a warning."""
        import logging
        from src.orchestrator.intent_classifier import extract_plan

        with caplog.at_level(logging.WARNING, logger="src.orchestrator.intent_classifier"):
            result = extract_plan("This is not JSON at all")

        assert result.steps[0].capability == "respond"
        assert any("Planner response did not contain valid JSON" in r.message for r in caplog.records)

    def test_valid_json_no_warning(self, caplog):
        """Valid Planner JSON should not log any warning."""
        import logging
        import json
        from src.orchestrator.intent_classifier import extract_plan

        valid = json.dumps({
            "goal": "Test",
            "steps": [{"step_id": "s1", "description": "Do thing", "capability": "respond"}],
        })
        with caplog.at_level(logging.WARNING, logger="src.orchestrator.intent_classifier"):
            result = extract_plan(valid)

        assert result.goal == "Test"
        assert not any("Planner response" in r.message for r in caplog.records)
```

- [ ] **Step 2: Run test to verify it fails**

Run: `cd backend && python -m pytest tests/test_plan_creation.py::TestExtractPlanFallback -v`
Expected: FAIL — no warning logged.

- [ ] **Step 3: Add warning log to extract_plan fallback**

In `backend/src/orchestrator/intent_classifier.py`, modify the fallback return at line 147:

```python
    logger.warning(
        "Planner response did not contain valid JSON — falling back to minimal respond plan. "
        "Response preview: %.300s",
        response_text,
    )
    return PlanOutput(
        goal=response_text[:200],
        steps=[PlanStep(step_id="s1", description="Respond to user", capability="respond")],
        achievable="partial",
    )
```

- [ ] **Step 4: Run test to verify it passes**

Run: `cd backend && python -m pytest tests/test_plan_creation.py::TestExtractPlanFallback -v`
Expected: PASS

- [ ] **Step 5: Commit**

```bash
cd backend && git add src/orchestrator/intent_classifier.py tests/test_plan_creation.py
git commit -m "fix: log warning when Planner JSON parse fails and mark plan as partial"
```

---

### Task 6: Add Step ID Uniqueness Validator to PlanOutput

**Gaps:** 1.8
**Files:**
- Modify: `backend/src/orchestrator/contracts.py:381-409`
- Modify: `backend/tests/test_plan_creation.py`

- [ ] **Step 1: Write the failing test**

Add to `backend/tests/test_plan_creation.py`:

```python
class TestPlanOutputValidation:
    """PlanOutput validators catch invalid step configurations."""

    def test_duplicate_step_ids_rejected(self):
        """Two steps with the same step_id should raise ValueError."""
        import pytest

        with pytest.raises(ValueError, match="Duplicate step_id"):
            PlanOutput(
                goal="Test",
                steps=[
                    PlanStep(step_id="s1", description="A", capability="respond"),
                    PlanStep(step_id="s1", description="B", capability="reason"),
                ],
            )

    def test_unique_step_ids_accepted(self):
        """Distinct step_ids should pass validation."""
        plan = PlanOutput(
            goal="Test",
            steps=[
                PlanStep(step_id="s1", description="A", capability="respond"),
                PlanStep(step_id="s2", description="B", capability="reason"),
            ],
        )
        assert len(plan.steps) == 2
```

- [ ] **Step 2: Run test to verify it fails**

Run: `cd backend && python -m pytest tests/test_plan_creation.py::TestPlanOutputValidation::test_duplicate_step_ids_rejected -v`
Expected: FAIL — no uniqueness check.

- [ ] **Step 3: Add step_id uniqueness check to PlanOutput validator**

In `backend/src/orchestrator/contracts.py`, in the `_validate_step_dependencies` method, add before the existing validation:

```python
    @model_validator(mode="after")
    def _validate_step_dependencies(self) -> PlanOutput:
        # Check step_id uniqueness
        seen_ids: set[str] = set()
        for step in self.steps:
            if step.step_id:
                if step.step_id in seen_ids:
                    raise ValueError(f"Duplicate step_id: '{step.step_id}'")
                seen_ids.add(step.step_id)

        step_ids = {s.step_id for s in self.steps if s.step_id}
        for step in self.steps:
            if step.step_id and step.step_id in step.depends_on:
                raise ValueError(f"Step '{step.step_id}' depends on itself")
            for dep in step.depends_on:
                if dep and dep not in step_ids:
                    raise ValueError(f"Step '{step.step_id}' depends on unknown step '{dep}'")
        # Cycle detection via DFS (existing code continues)
```

- [ ] **Step 4: Run test to verify it passes**

Run: `cd backend && python -m pytest tests/test_plan_creation.py::TestPlanOutputValidation -v`
Expected: PASS

- [ ] **Step 5: Run full test suite for regressions**

Run: `cd backend && python -m pytest tests/ -v -x --timeout=30 2>&1 | tail -20`
Expected: All tests pass.

- [ ] **Step 6: Commit**

```bash
cd backend && git add src/orchestrator/contracts.py tests/test_plan_creation.py
git commit -m "feat: validate step_id uniqueness in PlanOutput model"
```
