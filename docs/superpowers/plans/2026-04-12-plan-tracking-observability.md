# Plan Tracking & Observability Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Give users full visibility into their plan history, run lifecycle, and the ability to cancel/retry plans. Add status transition audit trail for debugging and learning.

**Architecture:** New `routes_plans.py` and expanded `routes_runs.py` expose plan history and run management. `execution_state.py` emits `RuntimeEvent` on every transition for the audit trail. Frontend gets a `/runs` page (deferred to a separate frontend plan).

**Tech Stack:** Python/FastAPI, SQLAlchemy async, Pydantic response models

---

## File Map

| File | Action | Responsibility |
|------|--------|----------------|
| `backend/src/api/routes_plans.py` | Create | Plan list, detail, runs-for-plan endpoints |
| `backend/src/api/routes_runs.py` | Modify | Add run list with filtering, cancel, retry endpoints |
| `backend/src/services/execution_state.py` | Modify | Emit RuntimeEvent on transitions |
| `backend/src/api/app.py` | Modify | Register new router |
| `backend/tests/test_plan_tracking.py` | Create | Tests for new endpoints |

---

### Task 1: Create Plan List and Detail Endpoints

**Gaps:** 4.1
**Files:**
- Create: `backend/src/api/routes_plans.py`
- Modify: `backend/src/api/app.py`
- Create: `backend/tests/test_plan_tracking.py`

- [ ] **Step 1: Write the failing test**

```python
# backend/tests/test_plan_tracking.py
"""Tests for plan tracking and observability endpoints."""

import pytest


class TestPlanEndpoints:
    """Plan list and detail routes exist and are registered."""

    def test_routes_plans_module_exists(self):
        """routes_plans module should be importable."""
        from src.api import routes_plans

        assert hasattr(routes_plans, "router")

    def test_plan_list_endpoint_registered(self):
        """GET /v1/plans should be a registered route."""
        from src.api.routes_plans import router

        routes = [r.path for r in router.routes]
        assert "/v1/plans" in routes

    def test_plan_detail_endpoint_registered(self):
        """GET /v1/plans/{plan_id} should be a registered route."""
        from src.api.routes_plans import router

        routes = [r.path for r in router.routes]
        assert "/v1/plans/{plan_id}" in routes

    def test_plan_runs_endpoint_registered(self):
        """GET /v1/plans/{plan_id}/runs should be a registered route."""
        from src.api.routes_plans import router

        routes = [r.path for r in router.routes]
        assert "/v1/plans/{plan_id}/runs" in routes
```

- [ ] **Step 2: Run test to verify it fails**

Run: `cd backend && python -m pytest tests/test_plan_tracking.py::TestPlanEndpoints -v`
Expected: FAIL — `routes_plans` module doesn't exist.

- [ ] **Step 3: Create routes_plans.py**

```python
# backend/src/api/routes_plans.py
"""Plan API routes — list, detail, and linked runs for plan history."""

import logging
from datetime import datetime

from fastapi import APIRouter, Depends, HTTPException, Query
from pydantic import BaseModel
from sqlalchemy import func, select
from sqlalchemy.ext.asyncio import AsyncSession

from src.api.deps import get_current_user_id, get_current_workspace_id, get_session
from src.models.plans import Plan, PlanTask
from src.models.task_graph import TaskRun

router = APIRouter()
logger = logging.getLogger(__name__)


class PlanTaskResponse(BaseModel):
    task_id: str
    task_type: str
    status: str
    depends_on: list[str] | None = None


class PlanSummaryResponse(BaseModel):
    plan_id: str
    goal: str
    priority: str
    status: str
    risk_level: str
    trigger_type: str
    task_count: int = 0
    created_at: str


class PlanDetailResponse(PlanSummaryResponse):
    reasoning_summary: str | None = None
    execution_mode: str
    success_conditions: dict | None = None
    plan_output_json: dict | None = None
    tasks: list[PlanTaskResponse] = []


class PlanRunResponse(BaseModel):
    run_id: str
    status: str
    source: str
    started_at: str | None = None
    completed_at: str | None = None
    error: dict | None = None


@router.get("/v1/plans", response_model=list[PlanSummaryResponse])
async def list_plans(
    status: str | None = None,
    trigger_type: str | None = None,
    created_after: datetime | None = None,
    created_before: datetime | None = None,
    limit: int = Query(default=50, le=200),
    offset: int = Query(default=0, ge=0),
    user_id: str = Depends(get_current_user_id),
    workspace_id: str = Depends(get_current_workspace_id),
    db: AsyncSession = Depends(get_session),
):
    """List plans for the current workspace with optional filters."""
    query = (
        select(Plan)
        .where(
            Plan.workspace_id == workspace_id,
            Plan.user_id == user_id,
        )
        .order_by(Plan.created_at.desc())
    )

    if status:
        query = query.where(Plan.status == status)
    if trigger_type:
        query = query.where(Plan.trigger_type == trigger_type)
    if created_after:
        query = query.where(Plan.created_at >= created_after)
    if created_before:
        query = query.where(Plan.created_at <= created_before)

    query = query.offset(offset).limit(limit)
    result = await db.execute(query)
    plans = result.scalars().all()

    summaries = []
    for p in plans:
        # Count tasks inline
        task_count = await db.execute(
            select(func.count()).select_from(PlanTask).where(PlanTask.plan_id == p.plan_id)
        )
        summaries.append(
            PlanSummaryResponse(
                plan_id=p.plan_id,
                goal=p.goal,
                priority=p.priority,
                status=p.status,
                risk_level=p.risk_level,
                trigger_type=p.trigger_type,
                task_count=task_count.scalar() or 0,
                created_at=p.created_at.isoformat() if p.created_at else "",
            )
        )
    return summaries


@router.get("/v1/plans/{plan_id}", response_model=PlanDetailResponse)
async def get_plan_detail(
    plan_id: str,
    user_id: str = Depends(get_current_user_id),
    workspace_id: str = Depends(get_current_workspace_id),
    db: AsyncSession = Depends(get_session),
):
    """Get full plan detail including tasks and stored PlanOutput."""
    result = await db.execute(
        select(Plan).where(
            Plan.plan_id == plan_id,
            Plan.user_id == user_id,
            Plan.workspace_id == workspace_id,
        )
    )
    plan = result.scalar_one_or_none()
    if not plan:
        raise HTTPException(status_code=404, detail=f"Plan {plan_id} not found")

    task_result = await db.execute(
        select(PlanTask).where(PlanTask.plan_id == plan_id).order_by(PlanTask.id)
    )
    tasks = [
        PlanTaskResponse(
            task_id=t.task_id,
            task_type=t.task_type,
            status=t.status,
            depends_on=t.depends_on,
        )
        for t in task_result.scalars().all()
    ]

    return PlanDetailResponse(
        plan_id=plan.plan_id,
        goal=plan.goal,
        priority=plan.priority,
        status=plan.status,
        risk_level=plan.risk_level,
        trigger_type=plan.trigger_type,
        reasoning_summary=plan.reasoning_summary,
        execution_mode=plan.execution_mode,
        success_conditions=plan.success_conditions,
        plan_output_json=getattr(plan, "plan_output_json", None),
        tasks=tasks,
        task_count=len(tasks),
        created_at=plan.created_at.isoformat() if plan.created_at else "",
    )


@router.get("/v1/plans/{plan_id}/runs", response_model=list[PlanRunResponse])
async def list_plan_runs(
    plan_id: str,
    user_id: str = Depends(get_current_user_id),
    workspace_id: str = Depends(get_current_workspace_id),
    db: AsyncSession = Depends(get_session),
):
    """List all execution runs for a given plan."""
    # Verify plan exists and belongs to user
    plan_check = await db.execute(
        select(Plan.plan_id).where(
            Plan.plan_id == plan_id,
            Plan.user_id == user_id,
            Plan.workspace_id == workspace_id,
        )
    )
    if not plan_check.scalar_one_or_none():
        raise HTTPException(status_code=404, detail=f"Plan {plan_id} not found")

    result = await db.execute(
        select(TaskRun)
        .where(TaskRun.plan_id == plan_id)
        .order_by(TaskRun.created_at.desc())
    )
    runs = result.scalars().all()

    return [
        PlanRunResponse(
            run_id=r.run_id,
            status=r.status,
            source=r.source,
            started_at=r.started_at.isoformat() if r.started_at else None,
            completed_at=r.completed_at.isoformat() if r.completed_at else None,
            error=r.error,
        )
        for r in runs
    ]
```

- [ ] **Step 4: Register the router in app.py**

Find the router registration section in `backend/src/api/app.py` and add:

```python
from src.api.routes_plans import router as plans_router
app.include_router(plans_router)
```

- [ ] **Step 5: Run test to verify it passes**

Run: `cd backend && python -m pytest tests/test_plan_tracking.py::TestPlanEndpoints -v`
Expected: PASS

- [ ] **Step 6: Commit**

```bash
cd backend && git add src/api/routes_plans.py src/api/app.py tests/test_plan_tracking.py
git commit -m "feat: add plan list, detail, and runs-for-plan API endpoints"
```

---

### Task 2: Add Run List with Filtering

**Gaps:** 4.2
**Files:**
- Modify: `backend/src/api/routes_runs.py`
- Modify: `backend/tests/test_plan_tracking.py`

- [ ] **Step 1: Write the failing test**

Add to `backend/tests/test_plan_tracking.py`:

```python
class TestRunListEndpoint:
    """GET /v1/runs with filtering should be registered."""

    def test_run_list_endpoint_exists(self):
        """GET /v1/runs route should exist."""
        from src.api.routes_runs import router

        paths = [r.path for r in router.routes]
        assert "/v1/runs" in paths
```

- [ ] **Step 2: Run test to verify it fails**

Run: `cd backend && python -m pytest tests/test_plan_tracking.py::TestRunListEndpoint -v`
Expected: FAIL — `/v1/runs` doesn't exist.

- [ ] **Step 3: Add GET /v1/runs endpoint**

In `backend/src/api/routes_runs.py`, add:

```python
from datetime import datetime
from fastapi import Query


@router.get("/v1/runs", response_model=list[RunResponse])
async def list_runs(
    status: str | None = None,
    source: str | None = None,
    created_after: datetime | None = None,
    created_before: datetime | None = None,
    limit: int = Query(default=50, le=200),
    offset: int = Query(default=0, ge=0),
    user_id: str = Depends(get_current_user_id),
    workspace_id: str = Depends(get_current_workspace_id),
    db: AsyncSession = Depends(get_session),
):
    """List runs for the current workspace with optional filters."""
    from sqlalchemy import func
    from sqlalchemy.ext.asyncio import AsyncSession

    from src.models.task_graph import TaskRun, TaskStep

    query = (
        select(TaskRun)
        .where(
            TaskRun.workspace_id == workspace_id,
            TaskRun.user_id == user_id,
        )
        .order_by(TaskRun.created_at.desc())
    )

    if status:
        query = query.where(TaskRun.status == status)
    if source:
        query = query.where(TaskRun.source == source)
    if created_after:
        query = query.where(TaskRun.created_at >= created_after)
    if created_before:
        query = query.where(TaskRun.created_at <= created_before)

    query = query.offset(offset).limit(limit)
    result = await db.execute(query)
    runs = result.scalars().all()

    responses = []
    for r in runs:
        step_count = await db.execute(
            select(func.count()).select_from(TaskStep).where(TaskStep.run_id == r.run_id)
        )
        responses.append(
            RunResponse(
                run_id=r.run_id,
                plan_id=r.plan_id,
                user_id=r.user_id,
                status=r.status,
                started_at=r.started_at.isoformat() if r.started_at else None,
                completed_at=r.completed_at.isoformat() if r.completed_at else None,
                error=r.error,
                retry_count=r.retry_count or 0,
                step_count=step_count.scalar() or 0,
            )
        )
    return responses
```

- [ ] **Step 4: Run test to verify it passes**

Run: `cd backend && python -m pytest tests/test_plan_tracking.py::TestRunListEndpoint -v`
Expected: PASS

- [ ] **Step 5: Commit**

```bash
cd backend && git add src/api/routes_runs.py tests/test_plan_tracking.py
git commit -m "feat: add GET /v1/runs with status, source, date range filtering"
```

---

### Task 3: Add Run Cancel and Retry Endpoints

**Gaps:** 4.3, 4.4
**Files:**
- Modify: `backend/src/api/routes_runs.py`
- Modify: `backend/tests/test_plan_tracking.py`

- [ ] **Step 1: Write the failing test**

Add to `backend/tests/test_plan_tracking.py`:

```python
class TestRunManagementEndpoints:
    """Cancel and retry endpoints should be registered."""

    def test_cancel_endpoint_exists(self):
        """POST /v1/runs/{run_id}/cancel should exist."""
        from src.api.routes_runs import router

        paths = [r.path for r in router.routes]
        assert "/v1/runs/{run_id}/cancel" in paths

    def test_retry_endpoint_exists(self):
        """POST /v1/runs/{run_id}/retry should exist."""
        from src.api.routes_runs import router

        paths = [r.path for r in router.routes]
        assert "/v1/runs/{run_id}/retry" in paths
```

- [ ] **Step 2: Run test to verify it fails**

Run: `cd backend && python -m pytest tests/test_plan_tracking.py::TestRunManagementEndpoints -v`
Expected: FAIL — endpoints don't exist.

- [ ] **Step 3: Add cancel and retry endpoints**

In `backend/src/api/routes_runs.py`, add:

```python
from src.config.settings import Settings, get_settings


@router.post("/v1/runs/{run_id}/cancel", response_model=RunResponse)
@per_endpoint_rate_limit(rpm=30)
async def cancel_run(
    run_id: str,
    user_id: str = Depends(get_current_user_id),
    workspace_id: str = Depends(get_current_workspace_id),
    db: AsyncSession = Depends(get_session),
    settings: Settings = Depends(get_settings),
):
    """Cancel a running or pending execution."""
    from src.models.task_graph import TaskRun

    result = await db.execute(
        select(TaskRun).where(
            TaskRun.run_id == run_id,
            TaskRun.user_id == user_id,
            TaskRun.workspace_id == workspace_id,
        )
    )
    run = result.scalar_one_or_none()
    if not run:
        raise HTTPException(status_code=404, detail=f"Run {run_id} not found")

    if run.status in ("completed", "cancelled", "archived"):
        raise HTTPException(
            status_code=400,
            detail=f"Cannot cancel run in '{run.status}' state",
        )

    from src.services.graph_executor import create_graph_executor

    executor = await create_graph_executor(settings=settings, db=db, workspace_id=workspace_id)
    cancelled = await executor.cancel_run(run_id)

    return RunResponse(
        run_id=cancelled.run_id,
        plan_id=cancelled.plan_id,
        user_id=cancelled.user_id,
        status=cancelled.status,
        started_at=cancelled.started_at.isoformat() if cancelled.started_at else None,
        completed_at=cancelled.completed_at.isoformat() if cancelled.completed_at else None,
        error=cancelled.error,
        retry_count=cancelled.retry_count or 0,
    )


@router.post("/v1/runs/{run_id}/retry", response_model=RunResponse)
@per_endpoint_rate_limit(rpm=10)
async def retry_run(
    run_id: str,
    user_id: str = Depends(get_current_user_id),
    workspace_id: str = Depends(get_current_workspace_id),
    db: AsyncSession = Depends(get_session),
    settings: Settings = Depends(get_settings),
):
    """Retry a failed or timed-out execution."""
    from src.models.task_graph import TaskRun
    from src.services.execution_state import transition_run

    result = await db.execute(
        select(TaskRun).where(
            TaskRun.run_id == run_id,
            TaskRun.user_id == user_id,
            TaskRun.workspace_id == workspace_id,
        )
    )
    run = result.scalar_one_or_none()
    if not run:
        raise HTTPException(status_code=404, detail=f"Run {run_id} not found")

    if run.status not in ("failed", "timed_out"):
        raise HTTPException(
            status_code=400,
            detail=f"Can only retry failed or timed_out runs, got '{run.status}'",
        )

    transition_run(run, "pending")
    run.retry_count = (run.retry_count or 0) + 1
    run.error = None
    run.completed_at = None
    await db.commit()

    return RunResponse(
        run_id=run.run_id,
        plan_id=run.plan_id,
        user_id=run.user_id,
        status=run.status,
        started_at=run.started_at.isoformat() if run.started_at else None,
        completed_at=None,
        error=None,
        retry_count=run.retry_count or 0,
    )
```

- [ ] **Step 4: Run test to verify it passes**

Run: `cd backend && python -m pytest tests/test_plan_tracking.py::TestRunManagementEndpoints -v`
Expected: PASS

- [ ] **Step 5: Commit**

```bash
cd backend && git add src/api/routes_runs.py tests/test_plan_tracking.py
git commit -m "feat: add POST /v1/runs/{run_id}/cancel and /retry endpoints"
```

---

### Task 4: Emit RuntimeEvent on State Transitions

**Gaps:** 4.7, 4.8
**Files:**
- Modify: `backend/src/services/execution_state.py`
- Modify: `backend/tests/test_plan_tracking.py`

- [ ] **Step 1: Write the failing test**

Add to `backend/tests/test_plan_tracking.py`:

```python
class TestStatusTransitionAudit:
    """State transitions emit RuntimeEvent records."""

    def test_transition_run_accepts_db_session(self):
        """transition_run accepts optional db parameter for event emission."""
        import inspect
        from src.services.execution_state import transition_run

        sig = inspect.signature(transition_run)
        assert "db" in sig.parameters or "emit_event" in sig.parameters
```

- [ ] **Step 2: Run test to verify it fails**

Run: `cd backend && python -m pytest tests/test_plan_tracking.py::TestStatusTransitionAudit -v`
Expected: FAIL — `transition_run` doesn't accept `db` parameter.

- [ ] **Step 3: Add event emission to transition functions**

In `backend/src/services/execution_state.py`, modify both functions to accept an optional callback:

```python
from typing import Callable


def transition_run(
    run,
    new_status: str,
    emit_event: Callable | None = None,
) -> None:
    """Transition a TaskRun to a new status, enforcing allowed transitions.

    Mutates run.status in place. Raises InvalidTransitionError if invalid.
    If emit_event callback is provided, emits a status_transition event.
    """
    allowed = RUN_TRANSITIONS.get(run.status, set())
    if new_status not in allowed:
        raise InvalidTransitionError("run", run.run_id, run.status, new_status)
    old = run.status
    run.status = new_status
    logger.debug("Run %s: %s → %s", run.run_id, old, new_status)

    if emit_event:
        try:
            emit_event(
                "run.status_changed",
                {
                    "run_id": run.run_id,
                    "from_status": old,
                    "to_status": new_status,
                },
            )
        except Exception:
            logger.debug("Failed to emit run transition event", exc_info=True)


def transition_step(
    step,
    new_status: str,
    emit_event: Callable | None = None,
) -> None:
    """Transition a TaskStep to a new status, enforcing allowed transitions.

    Mutates step.status in place. Raises InvalidTransitionError if invalid.
    """
    allowed = STEP_TRANSITIONS.get(step.status, set())
    if new_status not in allowed:
        raise InvalidTransitionError("step", step.step_id, step.status, new_status)
    old = step.status
    step.status = new_status
    logger.debug("Step %s: %s → %s", step.step_id, old, new_status)

    if emit_event:
        try:
            emit_event(
                "step.status_changed",
                {
                    "step_id": step.step_id,
                    "from_status": old,
                    "to_status": new_status,
                },
            )
        except Exception:
            logger.debug("Failed to emit step transition event", exc_info=True)
```

Note: The `emit_event` parameter is optional and backward-compatible. Existing callers that don't pass it continue to work. GraphExecutor can pass its `_emit_event` method to get audit events.

- [ ] **Step 4: Run test to verify it passes**

Run: `cd backend && python -m pytest tests/test_plan_tracking.py::TestStatusTransitionAudit -v`
Expected: PASS

- [ ] **Step 5: Run full test suite for regressions**

Run: `cd backend && python -m pytest tests/ -v -x --timeout=30 2>&1 | tail -30`
Expected: All existing tests still pass (emit_event is optional).

- [ ] **Step 6: Commit**

```bash
cd backend && git add src/services/execution_state.py tests/test_plan_tracking.py
git commit -m "feat: optional emit_event callback in transition_run/step for audit trail"
```
