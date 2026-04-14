# History Page Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Build a `/history` page — a calm ledger showing all runs with live execution monitoring, backed by a dedicated `/v1/history` API that replaces 9 unused endpoints.

**Architecture:** New `routes_history.py` with list+detail+retry endpoints joining TaskRun+Plan+TaskStep+UISurface+Approval. Frontend page at `/history` using Zustand store + React Query + existing WebSocket hook for live updates. Remove `routes_runs.py` entirely; trim `routes_plans.py` to keep only the plan detail endpoint used by internal tools.

**Tech Stack:** FastAPI + SQLAlchemy (backend), Next.js + Zustand + TanStack React Query + Tailwind (frontend), existing WebSocket infrastructure for live updates.

**Spec:** `docs/superpowers/specs/2026-04-13-history-page-design.md`

---

## File Structure

### Backend — New files
| File | Responsibility |
|------|---------------|
| `backend/src/api/schemas_history.py` | Pydantic response models for all history endpoints |
| `backend/src/api/routes_history.py` | `GET /v1/history`, `GET /v1/history/{run_id}`, `POST /v1/history/{run_id}/retry`, plus moved `cancel` and `resume` handlers |
| `backend/tests/test_routes_history.py` | API contract tests for history endpoints |
| `backend/tests/test_history_cleanup.py` | Regression guard: removed endpoints return 404 |

### Backend — Modified files
| File | Change |
|------|--------|
| `backend/src/api/app.py` | Register history router, remove runs router |
| `backend/src/api/routes_plans.py` | Remove list + plan_runs endpoints, keep plan detail |

### Backend — Deleted files
| File | Reason |
|------|--------|
| `backend/src/api/routes_runs.py` | Fully replaced by routes_history.py |

### Frontend — New files
| File | Responsibility |
|------|---------------|
| `frontend/src/stores/history-store.ts` | Zustand store: run list, filters, live state, surface_id mapping |
| `frontend/src/app/history/page.tsx` | History page: data fetching, WS integration, layout |
| `frontend/src/components/history/run-row.tsx` | Run timeline row: collapsed + expanded + approval variants |
| `frontend/src/components/history/history-filters.tsx` | Filter bar: search, status, source, date range |
| `frontend/src/components/history/run-detail-modal.tsx` | Tabbed detail modal: Steps, Plan, Events, Trace |
| `frontend/src/components/history/step-card.tsx` | Step detail card for modal Steps tab |
| `frontend/src/components/history/event-timeline.tsx` | Runtime event timeline for modal Events tab |
| `frontend/src/components/history/trace-summary.tsx` | Token/cost/agent metrics for modal Trace tab |

### Frontend — Modified files
| File | Change |
|------|--------|
| `frontend/src/lib/api.ts` | Add `fetchHistory()`, `fetchHistoryDetail()`, `retryRun()` |
| `frontend/src/components/layout/sidebar.tsx` | Add "History" nav item between Chat and Search |

---

## Task 1: Backend — Pydantic Response Models

**Files:**
- Create: `backend/src/api/schemas_history.py`
- Test: `backend/tests/test_routes_history.py` (started)

- [ ] **Step 1: Write schema validation tests**

```python
# backend/tests/test_routes_history.py
"""Tests for history API response schemas and endpoints."""

import pytest
from datetime import datetime, timezone


class TestHistorySchemas:
    def test_history_step_response_shape(self):
        from src.api.schemas_history import HistoryStepSummary

        step = HistoryStepSummary(
            step_id="step_001",
            name="Search emails",
            capability="email.search",
            status="completed",
            started_at=datetime(2026, 4, 13, 10, 0, 1, tzinfo=timezone.utc),
            completed_at=datetime(2026, 4, 13, 10, 0, 3, tzinfo=timezone.utc),
        )
        assert step.step_id == "step_001"
        assert step.status == "completed"

    def test_history_item_response_shape(self):
        from src.api.schemas_history import HistoryItemResponse

        item = HistoryItemResponse(
            run_id="run_001",
            plan_id="plan_001",
            goal="Send investor email",
            source="background",
            trigger_type="event",
            status="completed",
            risk_level=None,
            started_at=datetime(2026, 4, 13, 10, 0, tzinfo=timezone.utc),
            completed_at=datetime(2026, 4, 13, 10, 0, 18, tzinfo=timezone.utc),
            error=None,
            retry_count=0,
            step_count=3,
            completed_step_count=3,
            cost_usd=0.004,
            steps=[],
            approval=None,
            live_phase=None,
            surface_id=None,
        )
        assert item.run_id == "run_001"
        assert item.step_count == 3

    def test_history_list_response_shape(self):
        from src.api.schemas_history import HistoryListResponse

        resp = HistoryListResponse(items=[], total=0, limit=20, offset=0)
        assert resp.total == 0
        assert resp.limit == 20

    def test_history_detail_step_includes_output(self):
        from src.api.schemas_history import HistoryDetailStep

        step = HistoryDetailStep(
            step_id="step_001",
            name="Search emails",
            capability="email.search",
            status="completed",
            input_data={"query": "investor"},
            output_data={"result": "Found 3 threads"},
            started_at=datetime(2026, 4, 13, 10, 0, 1, tzinfo=timezone.utc),
            completed_at=datetime(2026, 4, 13, 10, 0, 3, tzinfo=timezone.utc),
            duration_ms=2340,
            error=None,
            artifacts=[],
        )
        assert step.output_data == {"result": "Found 3 threads"}
        assert step.duration_ms == 2340
```

- [ ] **Step 2: Run tests to verify they fail**

Run: `cd backend && python -m pytest tests/test_routes_history.py::TestHistorySchemas -v`
Expected: FAIL — `ModuleNotFoundError: No module named 'src.api.schemas_history'`

- [ ] **Step 3: Write the Pydantic models**

```python
# backend/src/api/schemas_history.py
"""Pydantic response models for the History API."""

from datetime import datetime

from pydantic import BaseModel


class HistoryStepSummary(BaseModel):
    """Compact step info for the history list view (no output_data)."""

    step_id: str
    name: str | None = None
    capability: str | None = None
    status: str
    started_at: datetime | None = None
    completed_at: datetime | None = None


class HistoryApprovalContext(BaseModel):
    """Embedded approval context for runs awaiting approval."""

    approval_id: str
    step_id: str | None = None
    step_description: str | None = None
    risk_level: str = "low"
    trust_level: str | None = None


class HistoryItemResponse(BaseModel):
    """Single run in the history list."""

    run_id: str
    plan_id: str | None = None
    goal: str | None = None
    source: str | None = None
    trigger_type: str | None = None
    status: str
    risk_level: str | None = None
    started_at: datetime | None = None
    completed_at: datetime | None = None
    error: dict | None = None
    retry_count: int = 0
    step_count: int = 0
    completed_step_count: int = 0
    cost_usd: float | None = None
    steps: list[HistoryStepSummary] = []
    approval: HistoryApprovalContext | None = None
    live_phase: str | None = None
    surface_id: str | None = None


class HistoryListResponse(BaseModel):
    """Paginated history list."""

    items: list[HistoryItemResponse]
    total: int
    limit: int
    offset: int


class HistoryArtifactRef(BaseModel):
    """Artifact reference in step detail."""

    artifact_id: str
    title: str | None = None
    artifact_type: str | None = None


class HistoryDetailStep(BaseModel):
    """Full step detail for the detail view (includes output_data)."""

    step_id: str
    name: str | None = None
    capability: str | None = None
    status: str
    input_data: dict | None = None
    output_data: dict | None = None
    started_at: datetime | None = None
    completed_at: datetime | None = None
    duration_ms: int | None = None
    error: dict | None = None
    artifacts: list[HistoryArtifactRef] = []


class HistoryApprovalRecord(BaseModel):
    """Approval decision record in detail view."""

    approval_id: str
    step_id: str | None = None
    status: str
    risk_level: str = "low"
    title: str | None = None
    decided_at: datetime | None = None
    decision_reason: str | None = None
    approved_by: str | None = None


class HistoryPlanContext(BaseModel):
    """Plan context in detail view."""

    plan_id: str
    goal: str | None = None
    reasoning_summary: str | None = None
    success_conditions: list | None = None
    trigger_type: str | None = None
    priority: str | None = None


class HistoryTraceInfo(BaseModel):
    """Trace/cost info in detail view."""

    trace_id: str | None = None
    input_tokens: int = 0
    output_tokens: int = 0
    cost_usd: float = 0.0
    duration_ms: int = 0
    agents_invoked: list[str] = []
    tools_called: list[str] = []


class HistoryEventEntry(BaseModel):
    """Runtime event in detail view."""

    event_type: str
    occurred_at: datetime
    step_id: str | None = None
    payload: dict = {}


class HistoryDetailResponse(BaseModel):
    """Full run detail for the detail modal."""

    run_id: str
    plan: HistoryPlanContext | None = None
    status: str
    source: str | None = None
    started_at: datetime | None = None
    completed_at: datetime | None = None
    error: dict | None = None
    steps: list[HistoryDetailStep] = []
    approvals: list[HistoryApprovalRecord] = []
    trace: HistoryTraceInfo | None = None
    events: list[HistoryEventEntry] = []


class RunActionResponse(BaseModel):
    """Response for cancel/resume/retry actions."""

    run_id: str
    status: str
    message: str = ""
```

- [ ] **Step 4: Run tests to verify they pass**

Run: `cd backend && python -m pytest tests/test_routes_history.py::TestHistorySchemas -v`
Expected: PASS (4 tests)

- [ ] **Step 5: Lint and commit**

```bash
cd backend && ruff check src/api/schemas_history.py tests/test_routes_history.py
git add src/api/schemas_history.py tests/test_routes_history.py
git commit -m "feat: add Pydantic response models for history API"
```

---

## Task 2: Backend — History List Endpoint

**Files:**
- Create: `backend/src/api/routes_history.py`
- Test: `backend/tests/test_routes_history.py` (append)

- [ ] **Step 1: Write failing test for the list endpoint**

Append to `backend/tests/test_routes_history.py`:

```python
from unittest.mock import AsyncMock, MagicMock, patch
from tests.conftest import TEST_USER_ID, TEST_WORKSPACE_ID, make_mock_settings


def _make_task_run(
    run_id="run_001",
    plan_id=None,
    status="completed",
    source="background",
    started_at=None,
    completed_at=None,
    error=None,
    retry_count=0,
    trace_id=None,
    workspace_id=TEST_WORKSPACE_ID,
    user_id=TEST_USER_ID,
):
    run = MagicMock()
    run.run_id = run_id
    run.plan_id = plan_id
    run.status = status
    run.source = source
    run.started_at = started_at or datetime(2026, 4, 13, 10, 0, tzinfo=timezone.utc)
    run.completed_at = completed_at
    run.error = error
    run.retry_count = retry_count
    run.trace_id = trace_id
    run.workspace_id = workspace_id
    run.user_id = user_id
    return run


def _make_task_step(
    step_id="step_001",
    run_id="run_001",
    name="Search emails",
    status="completed",
    started_at=None,
    completed_at=None,
):
    step = MagicMock()
    step.step_id = step_id
    step.run_id = run_id
    step.name = name
    step.status = status
    step.input_data = {"capability": "email.search"}
    step.started_at = started_at or datetime(2026, 4, 13, 10, 0, 1, tzinfo=timezone.utc)
    step.completed_at = completed_at or datetime(2026, 4, 13, 10, 0, 3, tzinfo=timezone.utc)
    return step


class _FakeScalars:
    def __init__(self, items):
        self._items = items

    def all(self):
        return self._items

    def unique(self):
        return self

    def first(self):
        return self._items[0] if self._items else None


class _FakeResult:
    def __init__(self, value=None, items=None):
        self._value = value
        self._items = items or []

    def scalar_one_or_none(self):
        return self._value

    def scalar(self):
        return self._value

    def scalars(self):
        return _FakeScalars(self._items)


@pytest.mark.asyncio
async def test_history_list_returns_paginated_runs():
    from src.api.routes_history import list_history

    run = _make_task_run(plan_id="plan_001")
    step = _make_task_step()
    plan = MagicMock()
    plan.goal = "Send investor email"
    plan.trigger_type = "event"
    plan.risk_level = "medium"

    call_count = 0

    async def fake_execute(stmt):
        nonlocal call_count
        call_count += 1
        if call_count == 1:
            # Count query
            return _FakeResult(value=1)
        elif call_count == 2:
            # Runs query
            return _FakeResult(items=[run])
        elif call_count == 3:
            # Steps for run
            return _FakeResult(items=[step])
        elif call_count == 4:
            # Plan for run
            return _FakeResult(value=plan)
        elif call_count == 5:
            # UISurface for live_phase
            return _FakeResult(value=None)
        elif call_count == 6:
            # Approval for run
            return _FakeResult(value=None)
        return _FakeResult()

    db = MagicMock()
    db.execute = AsyncMock(side_effect=fake_execute)

    result = await list_history(
        status="all",
        source="all",
        search="",
        date_from=None,
        date_to=None,
        limit=20,
        offset=0,
        user_id=TEST_USER_ID,
        workspace_id=TEST_WORKSPACE_ID,
        db=db,
    )

    assert result.total == 1
    assert len(result.items) == 1
    assert result.items[0].run_id == "run_001"
    assert result.items[0].goal == "Send investor email"
    assert result.items[0].step_count == 1
```

- [ ] **Step 2: Run test to verify it fails**

Run: `cd backend && python -m pytest tests/test_routes_history.py::test_history_list_returns_paginated_runs -v`
Expected: FAIL — `ModuleNotFoundError: No module named 'src.api.routes_history'`

- [ ] **Step 3: Implement the list endpoint**

```python
# backend/src/api/routes_history.py
"""History API — unified view of plans, runs, steps with live state."""

import logging
from datetime import datetime

from fastapi import APIRouter, Depends, HTTPException, Query
from sqlalchemy import func, select
from sqlalchemy.ext.asyncio import AsyncSession

from src.api.deps import get_current_user_id, get_current_workspace_id, get_session
from src.api.schemas_history import (
    HistoryApprovalContext,
    HistoryItemResponse,
    HistoryListResponse,
    HistoryStepSummary,
)
from src.models.approvals import Approval
from src.models.plans import Plan
from src.models.task_graph import TaskRun, TaskStep

logger = logging.getLogger(__name__)

router = APIRouter()


@router.get("/v1/history", response_model=HistoryListResponse)
async def list_history(
    status: str = Query("all"),
    source: str = Query("all"),
    search: str = Query(""),
    date_from: datetime | None = Query(None, alias="from"),
    date_to: datetime | None = Query(None, alias="to"),
    limit: int = Query(20, ge=1, le=50),
    offset: int = Query(0, ge=0),
    user_id: str = Depends(get_current_user_id),
    workspace_id: str = Depends(get_current_workspace_id),
    db: AsyncSession = Depends(get_session),
) -> HistoryListResponse:
    """List runs with embedded plan context, steps, and live state."""

    # Base filter
    base = select(TaskRun).where(
        TaskRun.user_id == user_id,
        TaskRun.workspace_id == workspace_id,
    )

    if status != "all":
        # Map frontend status labels to DB values
        status_map = {
            "executing": ["running", "pending"],
            "completed": ["completed"],
            "failed": ["failed"],
            "awaiting_approval": ["awaiting_approval"],
            "cancelled": ["cancelled"],
        }
        db_statuses = status_map.get(status, [status])
        base = base.where(TaskRun.status.in_(db_statuses))

    if source != "all":
        base = base.where(TaskRun.source == source)

    if date_from:
        base = base.where(TaskRun.created_at >= date_from)
    if date_to:
        base = base.where(TaskRun.created_at <= date_to)

    if search:
        # Search in plan goal via subquery
        plan_ids_q = select(Plan.plan_id).where(
            Plan.goal.ilike(f"%{search}%"),
            Plan.workspace_id == workspace_id,
        )
        base = base.where(TaskRun.plan_id.in_(plan_ids_q))

    # Count total
    count_q = select(func.count()).select_from(base.subquery())
    total_result = await db.execute(count_q)
    total = total_result.scalar() or 0

    # Fetch runs
    runs_q = base.order_by(TaskRun.created_at.desc()).limit(limit).offset(offset)
    runs_result = await db.execute(runs_q)
    runs = list(runs_result.scalars().all())

    # Build response items
    items: list[HistoryItemResponse] = []
    for run in runs:
        # Fetch steps
        steps_result = await db.execute(
            select(TaskStep)
            .where(TaskStep.run_id == run.run_id)
            .order_by(TaskStep.created_at)
        )
        steps = list(steps_result.scalars().all())

        # Fetch plan for goal
        goal = None
        trigger_type = None
        risk_level = None
        if run.plan_id:
            plan_result = await db.execute(
                select(Plan).where(Plan.plan_id == run.plan_id)
            )
            plan = plan_result.scalar_one_or_none()
            if plan:
                goal = plan.goal
                trigger_type = plan.trigger_type
                risk_level = plan.risk_level

        # Fetch live_phase from UISurface
        live_phase = None
        surface_id = None
        try:
            from src.models.ui_state import UISurface

            surf_result = await db.execute(
                select(UISurface).where(
                    UISurface.user_id == user_id,
                    UISurface.workspace_id == workspace_id,
                    UISurface.surface_type == "execution",
                )
            )
            for surf in surf_result.scalars().all():
                payload = surf.payload or {}
                if payload.get("source_run_id") == run.run_id:
                    surface_id = surf.surface_id
                    last_update = payload.get("last_surface_update", {})
                    live_phase = last_update.get("phase")
                    break
        except Exception:
            logger.debug("Failed to fetch UISurface for run %s", run.run_id, exc_info=True)

        # Fetch approval context if awaiting
        approval_ctx = None
        if run.status == "awaiting_approval":
            apr_result = await db.execute(
                select(Approval).where(
                    Approval.execution_id == run.run_id,
                    Approval.status == "pending",
                )
            )
            apr = apr_result.scalar_one_or_none()
            if apr:
                approval_ctx = HistoryApprovalContext(
                    approval_id=apr.approval_id,
                    step_id=apr.step_id,
                    step_description=apr.title,
                    risk_level=apr.risk_level or "low",
                )

        completed_count = sum(1 for s in steps if s.status == "completed")
        step_summaries = [
            HistoryStepSummary(
                step_id=s.step_id,
                name=s.name,
                capability=(s.input_data or {}).get("capability"),
                status=s.status,
                started_at=s.started_at,
                completed_at=s.completed_at,
            )
            for s in steps
        ]

        items.append(
            HistoryItemResponse(
                run_id=run.run_id,
                plan_id=run.plan_id,
                goal=goal,
                source=run.source,
                trigger_type=trigger_type,
                status=run.status,
                risk_level=risk_level,
                started_at=run.started_at,
                completed_at=run.completed_at,
                error=run.error,
                retry_count=run.retry_count or 0,
                step_count=len(steps),
                completed_step_count=completed_count,
                cost_usd=None,
                steps=step_summaries,
                approval=approval_ctx,
                live_phase=live_phase,
                surface_id=surface_id,
            )
        )

    return HistoryListResponse(items=items, total=total, limit=limit, offset=offset)
```

- [ ] **Step 4: Run test to verify it passes**

Run: `cd backend && python -m pytest tests/test_routes_history.py::test_history_list_returns_paginated_runs -v`
Expected: PASS

- [ ] **Step 5: Lint and commit**

```bash
cd backend && ruff check src/api/routes_history.py && ruff format src/api/routes_history.py
git add src/api/routes_history.py tests/test_routes_history.py
git commit -m "feat: add GET /v1/history list endpoint"
```

---

## Task 3: Backend — History Detail Endpoint

**Files:**
- Modify: `backend/src/api/routes_history.py`
- Test: `backend/tests/test_routes_history.py` (append)

- [ ] **Step 1: Write failing test**

Append to `backend/tests/test_routes_history.py`:

```python
@pytest.mark.asyncio
async def test_history_detail_returns_full_context():
    from src.api.routes_history import get_history_detail

    run = _make_task_run(run_id="run_d01", plan_id="plan_d01", status="completed")
    run.completed_at = datetime(2026, 4, 13, 10, 0, 18, tzinfo=timezone.utc)

    step = _make_task_step(step_id="step_d01", run_id="run_d01")
    step.input_data = {"capability": "email.search", "query": "investor"}
    step.output_data = {"result": "Found 3 threads"}
    step.completed_at = datetime(2026, 4, 13, 10, 0, 3, tzinfo=timezone.utc)
    step.error = None

    plan = MagicMock()
    plan.plan_id = "plan_d01"
    plan.goal = "Send investor email"
    plan.reasoning_summary = "Detected unread email"
    plan.success_conditions = ["Email sent"]
    plan.trigger_type = "event"
    plan.priority = "high"

    call_count = 0

    async def fake_execute(stmt):
        nonlocal call_count
        call_count += 1
        if call_count == 1:
            return _FakeResult(value=run)
        elif call_count == 2:
            return _FakeResult(items=[step])
        elif call_count == 3:
            return _FakeResult(value=plan)
        elif call_count == 4:
            return _FakeResult(items=[])  # approvals
        elif call_count == 5:
            return _FakeResult(items=[])  # events
        return _FakeResult()

    db = MagicMock()
    db.execute = AsyncMock(side_effect=fake_execute)

    result = await get_history_detail(
        run_id="run_d01",
        user_id=TEST_USER_ID,
        workspace_id=TEST_WORKSPACE_ID,
        db=db,
    )

    assert result.run_id == "run_d01"
    assert result.plan is not None
    assert result.plan.goal == "Send investor email"
    assert len(result.steps) == 1
    assert result.steps[0].output_data == {"result": "Found 3 threads"}


@pytest.mark.asyncio
async def test_history_detail_returns_404_for_missing_run():
    from src.api.routes_history import get_history_detail

    db = MagicMock()
    db.execute = AsyncMock(return_value=_FakeResult(value=None))

    with pytest.raises(HTTPException) as exc_info:
        await get_history_detail(
            run_id="run_nonexistent",
            user_id=TEST_USER_ID,
            workspace_id=TEST_WORKSPACE_ID,
            db=db,
        )
    assert exc_info.value.status_code == 404
```

Add `from fastapi import HTTPException` to the imports at the top of the test file.

- [ ] **Step 2: Run tests to verify they fail**

Run: `cd backend && python -m pytest tests/test_routes_history.py::test_history_detail_returns_full_context tests/test_routes_history.py::test_history_detail_returns_404_for_missing_run -v`
Expected: FAIL — `ImportError: cannot import name 'get_history_detail'`

- [ ] **Step 3: Implement the detail endpoint**

Add to `backend/src/api/routes_history.py` (after the list endpoint), adding new imports at the top:

```python
# Add to imports at top
from src.api.schemas_history import (
    # ... existing imports ...
    HistoryArtifactRef,
    HistoryApprovalRecord,
    HistoryDetailResponse,
    HistoryDetailStep,
    HistoryEventEntry,
    HistoryPlanContext,
    HistoryTraceInfo,
)
from src.models.runtime_event import RuntimeEvent


@router.get("/v1/history/{run_id}", response_model=HistoryDetailResponse)
async def get_history_detail(
    run_id: str,
    user_id: str = Depends(get_current_user_id),
    workspace_id: str = Depends(get_current_workspace_id),
    db: AsyncSession = Depends(get_session),
) -> HistoryDetailResponse:
    """Get full run detail with steps, approvals, trace, and events."""

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

    # Steps with full output
    steps_result = await db.execute(
        select(TaskStep)
        .where(TaskStep.run_id == run_id)
        .order_by(TaskStep.created_at)
    )
    steps = list(steps_result.scalars().all())

    detail_steps = []
    for s in steps:
        duration_ms = None
        if s.started_at and s.completed_at:
            duration_ms = int((s.completed_at - s.started_at).total_seconds() * 1000)

        # Fetch artifacts for this step
        artifacts = []
        try:
            from src.models.artifacts import Artifact

            art_result = await db.execute(
                select(Artifact).where(Artifact.step_id == s.step_id)
            )
            for art in art_result.scalars().all():
                artifacts.append(
                    HistoryArtifactRef(
                        artifact_id=art.artifact_id,
                        title=art.title,
                        artifact_type=art.artifact_type,
                    )
                )
        except Exception:
            pass

        detail_steps.append(
            HistoryDetailStep(
                step_id=s.step_id,
                name=s.name,
                capability=(s.input_data or {}).get("capability"),
                status=s.status,
                input_data=s.input_data,
                output_data=s.output_data,
                started_at=s.started_at,
                completed_at=s.completed_at,
                duration_ms=duration_ms,
                error=s.error if hasattr(s, "error") else None,
                artifacts=artifacts,
            )
        )

    # Plan context
    plan_ctx = None
    if run.plan_id:
        plan_result = await db.execute(
            select(Plan).where(Plan.plan_id == run.plan_id)
        )
        plan = plan_result.scalar_one_or_none()
        if plan:
            plan_ctx = HistoryPlanContext(
                plan_id=plan.plan_id,
                goal=plan.goal,
                reasoning_summary=plan.reasoning_summary,
                success_conditions=plan.success_conditions,
                trigger_type=plan.trigger_type,
                priority=plan.priority,
            )

    # Approvals
    apr_result = await db.execute(
        select(Approval)
        .where(Approval.execution_id == run_id)
        .order_by(Approval.created_at)
    )
    approvals = [
        HistoryApprovalRecord(
            approval_id=a.approval_id,
            step_id=a.step_id,
            status=a.status,
            risk_level=a.risk_level or "low",
            title=a.title,
            decided_at=a.decided_at,
            decision_reason=a.decision_reason,
            approved_by=a.approved_by,
        )
        for a in apr_result.scalars().all()
    ]

    # Runtime events
    evt_result = await db.execute(
        select(RuntimeEvent)
        .where(RuntimeEvent.run_id == run_id)
        .order_by(RuntimeEvent.occurred_at)
    )
    events = [
        HistoryEventEntry(
            event_type=e.event_type,
            occurred_at=e.occurred_at,
            step_id=e.step_id,
            payload=e.payload or {},
        )
        for e in evt_result.scalars().all()
    ]

    # Trace info (basic — from run metadata)
    trace = None
    if run.trace_id:
        duration_ms = 0
        if run.started_at and run.completed_at:
            duration_ms = int((run.completed_at - run.started_at).total_seconds() * 1000)
        trace = HistoryTraceInfo(
            trace_id=run.trace_id,
            duration_ms=duration_ms,
        )

    return HistoryDetailResponse(
        run_id=run.run_id,
        plan=plan_ctx,
        status=run.status,
        source=run.source,
        started_at=run.started_at,
        completed_at=run.completed_at,
        error=run.error,
        steps=detail_steps,
        approvals=approvals,
        trace=trace,
        events=events,
    )
```

- [ ] **Step 4: Run tests to verify they pass**

Run: `cd backend && python -m pytest tests/test_routes_history.py -v`
Expected: PASS (all tests including new ones)

- [ ] **Step 5: Lint and commit**

```bash
cd backend && ruff check src/api/routes_history.py && ruff format src/api/routes_history.py
git add src/api/routes_history.py tests/test_routes_history.py
git commit -m "feat: add GET /v1/history/{run_id} detail endpoint"
```

---

## Task 4: Backend — Retry Action + Move Cancel/Resume

**Files:**
- Modify: `backend/src/api/routes_history.py`
- Read: `backend/src/api/routes_runs.py:304-352` (resume), `backend/src/api/routes_runs.py:404-454` (cancel)
- Test: `backend/tests/test_routes_history.py` (append)

- [ ] **Step 1: Write failing test for retry**

Append to `backend/tests/test_routes_history.py`:

```python
@pytest.mark.asyncio
async def test_retry_transitions_failed_run_to_pending():
    from src.api.routes_history import retry_run

    run = _make_task_run(run_id="run_r01", status="failed")

    db = MagicMock()
    db.execute = AsyncMock(return_value=_FakeResult(value=run))
    db.commit = AsyncMock()

    with patch("src.api.routes_history.transition_run") as mock_transition:
        result = await retry_run(
            run_id="run_r01",
            user_id=TEST_USER_ID,
            workspace_id=TEST_WORKSPACE_ID,
            db=db,
        )

    assert result.run_id == "run_r01"
    mock_transition.assert_called_once_with(run, "pending")


@pytest.mark.asyncio
async def test_retry_rejects_non_failed_run():
    from src.api.routes_history import retry_run

    run = _make_task_run(run_id="run_r02", status="completed")

    db = MagicMock()
    db.execute = AsyncMock(return_value=_FakeResult(value=run))

    with pytest.raises(HTTPException) as exc_info:
        await retry_run(
            run_id="run_r02",
            user_id=TEST_USER_ID,
            workspace_id=TEST_WORKSPACE_ID,
            db=db,
        )
    assert exc_info.value.status_code == 400
```

- [ ] **Step 2: Run tests to verify they fail**

Run: `cd backend && python -m pytest tests/test_routes_history.py::test_retry_transitions_failed_run_to_pending tests/test_routes_history.py::test_retry_rejects_non_failed_run -v`
Expected: FAIL — `ImportError: cannot import name 'retry_run'`

- [ ] **Step 3: Add retry endpoint + move cancel and resume**

Read the existing cancel handler from `routes_runs.py:404-454` and resume handler from `routes_runs.py:304-352`, then add all three to `routes_history.py`. Add to the bottom of `backend/src/api/routes_history.py`:

```python
# Add to imports at top
from src.api.schemas_history import RunActionResponse
from src.config.settings import Settings, get_settings
from src.services.execution_state import transition_run


# ── Actions ─────────────────────────────────────────────────────────


@router.post("/v1/history/{run_id}/retry", response_model=RunActionResponse)
async def retry_run(
    run_id: str,
    user_id: str = Depends(get_current_user_id),
    workspace_id: str = Depends(get_current_workspace_id),
    db: AsyncSession = Depends(get_session),
) -> RunActionResponse:
    """Retry a failed or timed_out run."""
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
            detail=f"Cannot retry run in '{run.status}' state (must be failed or timed_out)",
        )

    transition_run(run, "pending")
    run.source = "approval_resume"
    run.error = None
    run.completed_at = None
    await db.commit()

    return RunActionResponse(run_id=run.run_id, status=run.status, message="Run queued for retry")


@router.post("/v1/runs/{run_id}/cancel", response_model=RunActionResponse)
async def cancel_run(
    run_id: str,
    user_id: str = Depends(get_current_user_id),
    workspace_id: str = Depends(get_current_workspace_id),
    db: AsyncSession = Depends(get_session),
    settings: Settings = Depends(get_settings),
) -> RunActionResponse:
    """Cancel a running or paused run."""
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

    terminal = ("completed", "failed", "cancelled", "archived")
    if run.status in terminal:
        raise HTTPException(
            status_code=400,
            detail=f"Cannot cancel run in '{run.status}' state",
        )

    from src.services.graph_executor import create_graph_executor

    executor = await create_graph_executor(settings=settings, db=db, workspace_id=workspace_id)
    try:
        await executor.cancel_run(run_id)
    except Exception:
        transition_run(run, "cancelled")
        logger.warning("Failed to cancel run %s via executor", run_id, exc_info=True)

    await db.commit()
    return RunActionResponse(run_id=run.run_id, status=run.status, message="Run cancelled")


@router.post("/v1/runs/{run_id}/resume", response_model=RunActionResponse)
async def resume_run(
    run_id: str,
    user_id: str = Depends(get_current_user_id),
    workspace_id: str = Depends(get_current_workspace_id),
    db: AsyncSession = Depends(get_session),
    settings: Settings = Depends(get_settings),
) -> RunActionResponse:
    """Resume a paused or awaiting_approval run."""
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

    resumable = ("paused", "awaiting_approval", "awaiting_input")
    if run.status not in resumable:
        raise HTTPException(
            status_code=400,
            detail=f"Cannot resume run in '{run.status}' state",
        )

    # Tag for scheduler pickup (scheduler has full agent loop deps)
    run.source = "approval_resume"
    await db.commit()

    return RunActionResponse(
        run_id=run.run_id,
        status=run.status,
        message="Run queued for resume",
    )
```

- [ ] **Step 4: Run all tests to verify they pass**

Run: `cd backend && python -m pytest tests/test_routes_history.py -v`
Expected: PASS (all tests)

- [ ] **Step 5: Lint and commit**

```bash
cd backend && ruff check src/api/routes_history.py && ruff format src/api/routes_history.py
git add src/api/routes_history.py tests/test_routes_history.py
git commit -m "feat: add retry, cancel, resume actions to history router"
```

---

## Task 5: Backend — Router Registration + Cleanup

**Files:**
- Modify: `backend/src/api/app.py` (lines 28, 30, 348, 351)
- Modify: `backend/src/api/routes_plans.py` (remove list + plan_runs endpoints)
- Delete: `backend/src/api/routes_runs.py`
- Create: `backend/tests/test_history_cleanup.py`

- [ ] **Step 1: Write regression guard test**

```python
# backend/tests/test_history_cleanup.py
"""Regression guard: verify removed endpoints no longer exist."""

from src.api.routes_history import router as history_router


def test_history_router_has_list_endpoint():
    paths = [r.path for r in history_router.routes]
    assert "/v1/history" in paths


def test_history_router_has_detail_endpoint():
    paths = [r.path for r in history_router.routes]
    assert "/v1/history/{run_id}" in paths


def test_history_router_has_retry_endpoint():
    paths = [r.path for r in history_router.routes]
    assert "/v1/history/{run_id}/retry" in paths


def test_history_router_has_cancel_endpoint():
    paths = [r.path for r in history_router.routes]
    assert "/v1/runs/{run_id}/cancel" in paths


def test_history_router_has_resume_endpoint():
    paths = [r.path for r in history_router.routes]
    assert "/v1/runs/{run_id}/resume" in paths


def test_routes_runs_module_is_deleted():
    """routes_runs.py should no longer be importable."""
    import importlib

    try:
        importlib.import_module("src.api.routes_runs")
        assert False, "src.api.routes_runs should have been deleted"
    except (ImportError, ModuleNotFoundError):
        pass


def test_plans_list_endpoint_is_removed():
    """GET /v1/plans should no longer exist."""
    from src.api.routes_plans import router as plans_router

    paths = [r.path for r in plans_router.routes]
    assert "/v1/plans" not in paths or all(
        "/v1/plans/{" in p for p in paths if p.startswith("/v1/plans")
    )
```

- [ ] **Step 2: Run tests to verify they fail (routes_runs still exists)**

Run: `cd backend && python -m pytest tests/test_history_cleanup.py -v`
Expected: FAIL — `test_routes_runs_module_is_deleted` fails because the file still exists

- [ ] **Step 3: Update app.py — register history router, remove runs router**

In `backend/src/api/app.py`:
- Remove the import: `from src.api.routes_runs import router as runs_router` (line 30)
- Add: `from src.api.routes_history import router as history_router`
- Remove: `app.include_router(runs_router, tags=["runs"])` (line 351)
- Add: `app.include_router(history_router, tags=["history"])`

- [ ] **Step 4: Trim routes_plans.py — remove list and plan_runs**

In `backend/src/api/routes_plans.py`:
- Remove the `list_plans()` function (lines 61–118) and its response model `PlanSummaryResponse`
- Remove the `get_plan_runs()` function (lines 170–212) and its response model `PlanRunResponse`
- Keep `get_plan()` (lines 121–167) and `PlanDetailResponse`

- [ ] **Step 5: Delete routes_runs.py**

```bash
cd backend && rm src/api/routes_runs.py
```

- [ ] **Step 6: Run cleanup tests + all history tests**

Run: `cd backend && python -m pytest tests/test_history_cleanup.py tests/test_routes_history.py -v`
Expected: PASS

- [ ] **Step 7: Run full test suite to check for breakage**

Run: `cd backend && python -m pytest tests/ -v --timeout=30 2>&1 | tail -20`
Expected: No new failures. If any test imports from `routes_runs`, update it.

- [ ] **Step 8: Lint and commit**

```bash
cd backend && ruff check src/api/app.py src/api/routes_plans.py src/api/routes_history.py
git add -A
git commit -m "refactor: replace routes_runs with routes_history, clean up unused endpoints"
```

---

## Task 6: Frontend — History Store + API Functions

**Files:**
- Create: `frontend/src/stores/history-store.ts`
- Modify: `frontend/src/lib/api.ts`

- [ ] **Step 1: Create the Zustand store**

```typescript
// frontend/src/stores/history-store.ts
import { create } from "zustand";

export interface HistoryStepSummary {
  step_id: string;
  name: string | null;
  capability: string | null;
  status: string;
  started_at: string | null;
  completed_at: string | null;
}

export interface HistoryApprovalContext {
  approval_id: string;
  step_id: string | null;
  step_description: string | null;
  risk_level: string;
  trust_level: string | null;
}

export interface HistoryItem {
  run_id: string;
  plan_id: string | null;
  goal: string | null;
  source: string | null;
  trigger_type: string | null;
  status: string;
  risk_level: string | null;
  started_at: string | null;
  completed_at: string | null;
  error: Record<string, unknown> | null;
  retry_count: number;
  step_count: number;
  completed_step_count: number;
  cost_usd: number | null;
  steps: HistoryStepSummary[];
  approval: HistoryApprovalContext | null;
  live_phase: string | null;
  surface_id: string | null;
}

export interface HistoryFilters {
  status: string;
  source: string;
  search: string;
  dateFrom: string | null;
  dateTo: string | null;
}

interface HistoryState {
  items: HistoryItem[];
  total: number;
  offset: number;
  filters: HistoryFilters;
  surfaceToRunMap: Record<string, string>;
  detailRunId: string | null;
  detailModalOpen: boolean;

  setItems: (items: HistoryItem[], total: number) => void;
  appendItems: (items: HistoryItem[], total: number) => void;
  setFilters: (filters: Partial<HistoryFilters>) => void;
  setOffset: (offset: number) => void;
  updateRunLiveState: (
    surfaceId: string,
    update: { phase?: string; steps?: HistoryStepSummary[]; current_step?: string | null; progress?: string; approval?: HistoryApprovalContext | null }
  ) => void;
  openDetail: (runId: string) => void;
  closeDetail: () => void;
}

export const useHistoryStore = create<HistoryState>((set, get) => ({
  items: [],
  total: 0,
  offset: 0,
  filters: { status: "all", source: "all", search: "", dateFrom: null, dateTo: null },
  surfaceToRunMap: {},
  detailRunId: null,
  detailModalOpen: false,

  setItems: (items, total) => {
    const map: Record<string, string> = {};
    for (const item of items) {
      if (item.surface_id) {
        map[item.surface_id] = item.run_id;
      }
    }
    set({ items, total, surfaceToRunMap: { ...get().surfaceToRunMap, ...map } });
  },

  appendItems: (newItems, total) => {
    const map = { ...get().surfaceToRunMap };
    for (const item of newItems) {
      if (item.surface_id) {
        map[item.surface_id] = item.run_id;
      }
    }
    set((s) => ({
      items: [...s.items, ...newItems],
      total,
      surfaceToRunMap: map,
    }));
  },

  setFilters: (partial) =>
    set((s) => ({ filters: { ...s.filters, ...partial }, offset: 0 })),

  setOffset: (offset) => set({ offset }),

  updateRunLiveState: (surfaceId, update) => {
    const runId = get().surfaceToRunMap[surfaceId];
    if (!runId) return;

    set((s) => ({
      items: s.items.map((item) => {
        if (item.run_id !== runId) return item;
        return {
          ...item,
          live_phase: update.phase ?? item.live_phase,
          steps: update.steps && update.steps.length > 0 ? update.steps : item.steps,
          status:
            update.phase === "completed"
              ? "completed"
              : update.phase === "failed"
                ? "failed"
                : update.phase === "approval_needed"
                  ? "awaiting_approval"
                  : item.status,
          approval: update.approval !== undefined ? update.approval : item.approval,
        };
      }),
    }));
  },

  openDetail: (runId) => set({ detailRunId: runId, detailModalOpen: true }),
  closeDetail: () => set({ detailRunId: null, detailModalOpen: false }),
}));
```

- [ ] **Step 2: Add API functions to lib/api.ts**

Add to `frontend/src/lib/api.ts`:

```typescript
// History API
export interface HistoryListResponse {
  items: import("@/stores/history-store").HistoryItem[];
  total: number;
  limit: number;
  offset: number;
}

export async function fetchHistory(params: {
  status?: string;
  source?: string;
  search?: string;
  from?: string;
  to?: string;
  limit?: number;
  offset?: number;
}): Promise<HistoryListResponse> {
  const qs = new URLSearchParams();
  if (params.status && params.status !== "all") qs.set("status", params.status);
  if (params.source && params.source !== "all") qs.set("source", params.source);
  if (params.search) qs.set("search", params.search);
  if (params.from) qs.set("from", params.from);
  if (params.to) qs.set("to", params.to);
  if (params.limit) qs.set("limit", String(params.limit));
  if (params.offset) qs.set("offset", String(params.offset));
  return api<HistoryListResponse>(`/history?${qs.toString()}`);
}

export async function fetchHistoryDetail(runId: string) {
  return api<Record<string, unknown>>(`/history/${runId}`);
}

export async function retryRun(runId: string) {
  return post<{ run_id: string; status: string; message: string }>(
    `/history/${runId}/retry`,
    {}
  );
}
```

- [ ] **Step 3: Commit**

```bash
cd frontend && npm run lint
git add src/stores/history-store.ts src/lib/api.ts
git commit -m "feat: add history store and API functions"
```

---

## Task 7: Frontend — RunRow Component

**Files:**
- Create: `frontend/src/components/history/run-row.tsx`

- [ ] **Step 1: Create the RunRow component**

This component handles all three row variants (collapsed, expanded active, expanded with approval). Refer to the spec's visual design section for the exact layout. Follow the existing pattern from `execution-surface.tsx` for status icon rendering.

```typescript
// frontend/src/components/history/run-row.tsx
"use client";

import { useCallback } from "react";
import { HistoryItem, useHistoryStore } from "@/stores/history-store";

const STATUS_ICONS: Record<string, { icon: string; color: string }> = {
  pending: { icon: "\u25CB", color: "text-gray-500 opacity-50" },
  ready: { icon: "\u25CB", color: "text-gray-500" },
  running: { icon: "\u25C9", color: "text-blue-400 animate-pulse" },
  completed: { icon: "\u2713", color: "text-green-400" },
  failed: { icon: "\u2717", color: "text-red-400" },
  waiting_approval: { icon: "\u25A0", color: "text-yellow-400" },
  skipped: { icon: "\u2014", color: "text-gray-500" },
  timed_out: { icon: "\u23F1", color: "text-orange-400" },
  cancelled: { icon: "\u2298", color: "text-gray-500" },
};

const RUN_STATUS_COLORS: Record<string, string> = {
  running: "bg-blue-900/40 text-blue-400",
  pending: "bg-blue-900/40 text-blue-400",
  completed: "bg-green-900/40 text-green-400",
  failed: "bg-red-900/40 text-red-400",
  awaiting_approval: "bg-yellow-900/40 text-yellow-400",
  cancelled: "bg-gray-800 text-gray-400",
};

const RUN_DOT_COLORS: Record<string, string> = {
  running: "bg-blue-400 shadow-[0_0_6px_theme(colors.blue.400)] animate-pulse",
  pending: "bg-blue-400 shadow-[0_0_6px_theme(colors.blue.400)] animate-pulse",
  completed: "bg-green-400",
  failed: "bg-red-400",
  awaiting_approval: "bg-yellow-400",
  cancelled: "bg-gray-500",
};

function formatRelativeTime(iso: string | null): string {
  if (!iso) return "";
  const diff = Date.now() - new Date(iso).getTime();
  const mins = Math.floor(diff / 60000);
  if (mins < 1) return "just now";
  if (mins < 60) return `${mins} min ago`;
  const hours = Math.floor(mins / 60);
  if (hours < 24) return `${hours}h ago`;
  return new Date(iso).toLocaleDateString();
}

function formatDuration(start: string | null, end: string | null): string {
  if (!start || !end) return "";
  const ms = new Date(end).getTime() - new Date(start).getTime();
  if (ms < 1000) return `${ms}ms`;
  return `${(ms / 1000).toFixed(1)}s`;
}

function statusLabel(status: string): string {
  const labels: Record<string, string> = {
    running: "executing",
    pending: "executing",
    completed: "completed",
    failed: "failed",
    awaiting_approval: "approval needed",
    cancelled: "cancelled",
  };
  return labels[status] ?? status;
}

export function RunRow({ item }: { item: HistoryItem }) {
  const openDetail = useHistoryStore((s) => s.openDetail);
  const isActive = ["running", "pending", "awaiting_approval"].includes(item.status);

  const handleClick = useCallback(() => {
    openDetail(item.run_id);
  }, [item.run_id, openDetail]);

  const dotColor = RUN_DOT_COLORS[item.status] ?? "bg-gray-500";
  const badgeColor = RUN_STATUS_COLORS[item.status] ?? "bg-gray-800 text-gray-400";

  return (
    <div
      className="border-b border-[#21262d] hover:bg-[#161b22]/50 cursor-pointer transition-colors"
      onClick={handleClick}
    >
      {/* Run header */}
      <div className="flex items-center gap-3 px-5 py-3.5">
        <div className={`w-2 h-2 rounded-full shrink-0 ${dotColor}`} />
        <div className="flex-1 min-w-0">
          <div className="text-sm font-medium text-[#e6edf3] truncate">
            {item.goal ?? `Run ${item.run_id}`}
          </div>
          <div className="text-xs text-[#8b949e] mt-0.5">
            {item.trigger_type ? `Triggered by: ${item.trigger_type}` : item.source}
            {" \u00B7 "}
            {formatRelativeTime(item.started_at)}
            {!isActive && item.steps.length > 0 && (
              <>
                {" \u00B7 "}
                {item.step_count} steps
              </>
            )}
            {!isActive && item.started_at && item.completed_at && (
              <>
                {" \u00B7 "}
                {formatDuration(item.started_at, item.completed_at)}
              </>
            )}
            {!isActive && item.cost_usd != null && (
              <>
                {" \u00B7 "}${item.cost_usd.toFixed(3)}
              </>
            )}
          </div>
        </div>
        <span className={`px-2.5 py-0.5 rounded-xl text-[11px] font-medium ${badgeColor}`}>
          {statusLabel(item.status)}
        </span>
        {isActive && (
          <span className="text-xs text-[#8b949e]">
            {item.completed_step_count}/{item.step_count} steps
          </span>
        )}
        {item.status === "failed" && (
          <button
            className="text-xs text-[#8b949e] border border-[#30363d] px-2.5 py-0.5 rounded-md hover:text-[#e6edf3] hover:border-[#484f58] transition-colors"
            onClick={(e) => {
              e.stopPropagation();
              // Retry handled by parent page
            }}
          >
            Retry
          </button>
        )}
      </div>

      {/* Inline steps for active runs */}
      {isActive && item.steps.length > 0 && (
        <div className="px-5 pb-3.5 pl-10">
          <div className="flex flex-col gap-1">
            {item.steps.map((step) => {
              const si = STATUS_ICONS[step.status] ?? STATUS_ICONS.pending;
              const isCurrent = step.status === "running";
              return (
                <div
                  key={step.step_id}
                  className={`flex items-center gap-2.5 px-3 py-1.5 rounded-md bg-[#161b22] ${
                    isCurrent ? "border-l-2 border-blue-400" : ""
                  } ${step.status === "pending" ? "opacity-50" : ""}`}
                >
                  <span className={`text-sm ${si.color}`}>{si.icon}</span>
                  <span
                    className={`text-[13px] flex-1 ${isCurrent ? "text-[#f0f6fc] font-medium" : "text-[#e6edf3]"}`}
                  >
                    {step.name ?? step.step_id}
                  </span>
                  {step.capability && (
                    <span className="text-[11px] text-[#8b949e]">{step.capability}</span>
                  )}
                  <span className="text-[11px] text-[#8b949e]">
                    {step.status === "running"
                      ? "running..."
                      : step.started_at && step.completed_at
                        ? formatDuration(step.started_at, step.completed_at)
                        : step.status === "pending"
                          ? ""
                          : step.status}
                  </span>
                </div>
              );
            })}
          </div>

          {/* Inline approval card */}
          {item.approval && (
            <div className="mt-2 px-3.5 py-2.5 bg-[#1c1e24] border border-[#30363d] rounded-lg flex items-center gap-3">
              <div className="flex-1">
                <div className="text-xs text-yellow-400 font-medium">Approval required</div>
                <div className="text-xs text-[#8b949e] mt-0.5">
                  {item.approval.step_description} &middot; {item.approval.risk_level} risk
                </div>
              </div>
              <div className="flex gap-1.5">
                <button
                  className="bg-[#238636] text-white px-3.5 py-1 rounded-md text-xs hover:bg-[#2ea043] transition-colors"
                  onClick={(e) => e.stopPropagation()}
                >
                  Approve
                </button>
                <button
                  className="bg-transparent text-red-400 border border-red-400 px-3.5 py-1 rounded-md text-xs hover:bg-red-900/20 transition-colors"
                  onClick={(e) => e.stopPropagation()}
                >
                  Reject
                </button>
              </div>
            </div>
          )}
        </div>
      )}
    </div>
  );
}
```

- [ ] **Step 2: Commit**

```bash
cd frontend && npm run lint
git add src/components/history/run-row.tsx
git commit -m "feat: add RunRow component for history timeline"
```

---

## Task 8: Frontend — History Filters + Detail Modal Components

**Files:**
- Create: `frontend/src/components/history/history-filters.tsx`
- Create: `frontend/src/components/history/step-card.tsx`
- Create: `frontend/src/components/history/event-timeline.tsx`
- Create: `frontend/src/components/history/trace-summary.tsx`
- Create: `frontend/src/components/history/run-detail-modal.tsx`

- [ ] **Step 1: Create HistoryFilters**

```typescript
// frontend/src/components/history/history-filters.tsx
"use client";

import { useHistoryStore } from "@/stores/history-store";

const STATUS_OPTIONS = ["all", "executing", "completed", "failed", "awaiting_approval", "cancelled"];
const SOURCE_OPTIONS = ["all", "background", "user_message", "schedule", "event"];
const DATE_PRESETS = [
  { label: "Last 24h", value: "24h" },
  { label: "Last 7 days", value: "7d" },
  { label: "Last 30 days", value: "30d" },
];

function datePresetToISO(preset: string): { from: string; to: string | null } {
  const now = new Date();
  const ms = preset === "24h" ? 86400000 : preset === "7d" ? 604800000 : 2592000000;
  return { from: new Date(now.getTime() - ms).toISOString(), to: null };
}

export function HistoryFilters() {
  const filters = useHistoryStore((s) => s.filters);
  const setFilters = useHistoryStore((s) => s.setFilters);

  return (
    <div className="flex items-center gap-3 px-5 py-3.5 border-b border-[#21262d]">
      <div className="flex-1">
        <input
          type="text"
          placeholder="Search runs, plans, steps..."
          value={filters.search}
          onChange={(e) => setFilters({ search: e.target.value })}
          className="w-full bg-[#0d1117] border border-[#30363d] rounded-md px-3 py-1.5 text-sm text-[#e6edf3] placeholder-[#484f58] focus:outline-none focus:border-[#58a6ff]"
        />
      </div>
      <select
        value={filters.status}
        onChange={(e) => setFilters({ status: e.target.value })}
        className="bg-[#21262d] text-[#e6edf3] border border-[#30363d] rounded-full px-3 py-1 text-xs cursor-pointer focus:outline-none"
      >
        {STATUS_OPTIONS.map((s) => (
          <option key={s} value={s}>
            {s === "all" ? "All Status" : s.replace("_", " ")}
          </option>
        ))}
      </select>
      <select
        value={filters.source}
        onChange={(e) => setFilters({ source: e.target.value })}
        className="bg-[#21262d] text-[#e6edf3] border border-[#30363d] rounded-full px-3 py-1 text-xs cursor-pointer focus:outline-none"
      >
        {SOURCE_OPTIONS.map((s) => (
          <option key={s} value={s}>
            {s === "all" ? "All Sources" : s.replace("_", " ")}
          </option>
        ))}
      </select>
      <div className="flex gap-1">
        {DATE_PRESETS.map((p) => (
          <button
            key={p.value}
            onClick={() => {
              const { from } = datePresetToISO(p.value);
              setFilters({ dateFrom: from, dateTo: null });
            }}
            className={`px-2.5 py-1 rounded-full text-xs border transition-colors ${
              filters.dateFrom ? "bg-[#21262d] text-[#e6edf3] border-[#30363d]" : "bg-[#21262d] text-[#8b949e] border-[#30363d]"
            } hover:text-[#e6edf3]`}
          >
            {p.label}
          </button>
        ))}
      </div>
    </div>
  );
}
```

- [ ] **Step 2: Create StepCard, EventTimeline, TraceSummary**

Create `frontend/src/components/history/step-card.tsx`, `event-timeline.tsx`, and `trace-summary.tsx` following the detail modal visual design from the spec. These are presentational components that receive data as props.

StepCard renders: status icon, name, capability badge, duration, expandable body with input_data/output_data/artifacts/approval record.

EventTimeline renders: vertical timeline with colored dots, timestamps, event_type, step name, payload.

TraceSummary renders: 4 metric cards (input tokens, output tokens, cost, duration) + agent/tool pill badges.

Each component should follow the existing Tailwind patterns in the codebase (dark theme: `bg-[#161b22]`, `border-[#21262d]`, `text-[#e6edf3]`, etc.).

- [ ] **Step 3: Create RunDetailModal**

```typescript
// frontend/src/components/history/run-detail-modal.tsx
"use client";

import { useCallback, useEffect, useState } from "react";
import { useHistoryStore } from "@/stores/history-store";
import { fetchHistoryDetail } from "@/lib/api";

type TabId = "steps" | "plan" | "events" | "trace";

const TABS: { id: TabId; label: string }[] = [
  { id: "steps", label: "Steps" },
  { id: "plan", label: "Plan" },
  { id: "events", label: "Events" },
  { id: "trace", label: "Trace" },
];

export function RunDetailModal() {
  const runId = useHistoryStore((s) => s.detailRunId);
  const isOpen = useHistoryStore((s) => s.detailModalOpen);
  const closeDetail = useHistoryStore((s) => s.closeDetail);
  const [activeTab, setActiveTab] = useState<TabId>("steps");
  const [detail, setDetail] = useState<Record<string, unknown> | null>(null);
  const [loading, setLoading] = useState(false);

  useEffect(() => {
    if (!runId || !isOpen) return;
    setLoading(true);
    setActiveTab("steps");
    fetchHistoryDetail(runId)
      .then(setDetail)
      .catch(() => setDetail(null))
      .finally(() => setLoading(false));
  }, [runId, isOpen]);

  const handleBackdropClick = useCallback(
    (e: React.MouseEvent) => {
      if (e.target === e.currentTarget) closeDetail();
    },
    [closeDetail]
  );

  if (!isOpen || !runId) return null;

  return (
    <div
      className="fixed inset-0 bg-black/60 z-50 flex items-center justify-center p-4"
      onClick={handleBackdropClick}
    >
      <div className="bg-[#0d1117] border border-[#30363d] rounded-xl w-full max-w-3xl max-h-[85vh] overflow-hidden flex flex-col">
        {/* Header */}
        <div className="px-6 pt-5 border-b border-[#21262d]">
          <div className="flex items-start gap-3">
            <div className="flex-1 min-w-0">
              <h2 className="text-base font-semibold text-[#f0f6fc] truncate">
                {(detail as Record<string, unknown>)?.plan
                  ? ((detail as Record<string, Record<string, unknown>>).plan?.goal as string)
                  : `Run ${runId}`}
              </h2>
              <p className="text-xs text-[#8b949e] mt-1">
                {runId}
                {detail && ` \u00B7 ${(detail as Record<string, string>).status}`}
              </p>
            </div>
            <button onClick={closeDetail} className="text-[#8b949e] hover:text-[#e6edf3] text-lg">
              &times;
            </button>
          </div>
          {/* Tabs */}
          <div className="flex gap-0 mt-4">
            {TABS.map((tab) => (
              <button
                key={tab.id}
                onClick={() => setActiveTab(tab.id)}
                className={`px-4 py-2 text-[13px] border-b-2 transition-colors ${
                  activeTab === tab.id
                    ? "text-[#f0f6fc] border-[#58a6ff]"
                    : "text-[#8b949e] border-transparent hover:text-[#e6edf3]"
                }`}
              >
                {tab.label}
              </button>
            ))}
          </div>
        </div>

        {/* Content */}
        <div className="flex-1 overflow-y-auto p-6">
          {loading ? (
            <div className="flex items-center justify-center py-12 text-[#8b949e] text-sm">
              Loading...
            </div>
          ) : !detail ? (
            <div className="flex items-center justify-center py-12 text-[#8b949e] text-sm">
              Failed to load run details
            </div>
          ) : (
            <>
              {activeTab === "steps" && <StepsTab detail={detail} />}
              {activeTab === "plan" && <PlanTab detail={detail} />}
              {activeTab === "events" && <EventsTab detail={detail} />}
              {activeTab === "trace" && <TraceTab detail={detail} />}
            </>
          )}
        </div>
      </div>
    </div>
  );
}

// Inline tab renderers — import from dedicated components once created
function StepsTab({ detail }: { detail: Record<string, unknown> }) {
  const steps = (detail.steps as Record<string, unknown>[]) ?? [];
  if (steps.length === 0) return <p className="text-[#8b949e] text-sm">No steps recorded</p>;
  return (
    <div className="space-y-4">
      {steps.map((s) => (
        <div key={s.step_id as string} className="border border-[#21262d] rounded-lg overflow-hidden">
          <div className="flex items-center gap-2.5 px-4 py-3 bg-[#161b22]">
            <span className="text-sm text-green-400">{(s.status as string) === "completed" ? "\u2713" : "\u25CB"}</span>
            <span className="text-[13px] font-medium text-[#e6edf3] flex-1">{(s.name as string) ?? (s.step_id as string)}</span>
            {s.capability && <span className="text-[11px] text-[#8b949e] bg-[#21262d] px-2 py-0.5 rounded">{s.capability as string}</span>}
            {s.duration_ms != null && <span className="text-[11px] text-[#8b949e]">{((s.duration_ms as number) / 1000).toFixed(1)}s</span>}
          </div>
          {s.output_data && (
            <div className="px-4 py-3 border-t border-[#21262d] text-xs">
              <div className="text-[#8b949e] uppercase tracking-wider text-[10px] mb-1.5">Output</div>
              <div className="text-[#c9d1d9] leading-relaxed whitespace-pre-wrap">
                {typeof (s.output_data as Record<string, unknown>)?.result === "string"
                  ? ((s.output_data as Record<string, string>).result)
                  : JSON.stringify(s.output_data, null, 2)}
              </div>
            </div>
          )}
        </div>
      ))}
    </div>
  );
}

function PlanTab({ detail }: { detail: Record<string, unknown> }) {
  const plan = detail.plan as Record<string, unknown> | null;
  if (!plan) return <p className="text-[#8b949e] text-sm">No plan context available</p>;
  return (
    <div className="space-y-4">
      <div>
        <div className="text-[10px] text-[#8b949e] uppercase tracking-wider mb-1">Goal</div>
        <div className="text-sm text-[#e6edf3]">{plan.goal as string}</div>
      </div>
      {plan.reasoning_summary && (
        <div>
          <div className="text-[10px] text-[#8b949e] uppercase tracking-wider mb-1">Reasoning</div>
          <div className="text-sm text-[#c9d1d9]">{plan.reasoning_summary as string}</div>
        </div>
      )}
      {plan.success_conditions && (
        <div>
          <div className="text-[10px] text-[#8b949e] uppercase tracking-wider mb-1">Success Conditions</div>
          <ul className="list-disc list-inside text-sm text-[#c9d1d9]">
            {(plan.success_conditions as string[]).map((c, i) => (
              <li key={i}>{c}</li>
            ))}
          </ul>
        </div>
      )}
    </div>
  );
}

function EventsTab({ detail }: { detail: Record<string, unknown> }) {
  const events = (detail.events as Record<string, unknown>[]) ?? [];
  if (events.length === 0) return <p className="text-[#8b949e] text-sm">No events recorded</p>;

  const typeColors: Record<string, string> = {
    run_started: "text-blue-400",
    step_started: "text-blue-400",
    step_completed: "text-green-400",
    run_completed: "text-green-400",
    tool_call_started: "text-purple-400",
    approval_requested: "text-yellow-400",
    approval_resolved: "text-green-400",
  };

  return (
    <div className="relative pl-6">
      <div className="absolute left-[7px] top-2 bottom-2 w-px bg-[#21262d]" />
      {events.map((evt, i) => {
        const color = typeColors[evt.event_type as string] ?? "text-[#8b949e]";
        const ts = new Date(evt.occurred_at as string);
        return (
          <div key={i} className="relative mb-4">
            <div className="absolute -left-[13px] top-1 w-2.5 h-2.5 rounded-full bg-[#21262d] border-2 border-[#30363d]" />
            <div className="text-xs">
              <span className="text-[#8b949e]">{ts.toLocaleTimeString()}</span>
              <span className={`ml-2 ${color}`}>{evt.event_type as string}</span>
              {evt.step_id && <span className="ml-1.5 text-[#8b949e]">{evt.step_id as string}</span>}
            </div>
          </div>
        );
      })}
    </div>
  );
}

function TraceTab({ detail }: { detail: Record<string, unknown> }) {
  const trace = detail.trace as Record<string, unknown> | null;
  if (!trace) return <p className="text-[#8b949e] text-sm">No trace data available</p>;

  const metrics = [
    { label: "Input Tokens", value: String(trace.input_tokens ?? 0) },
    { label: "Output Tokens", value: String(trace.output_tokens ?? 0) },
    { label: "Cost", value: `$${((trace.cost_usd as number) ?? 0).toFixed(4)}` },
    { label: "Duration", value: `${(((trace.duration_ms as number) ?? 0) / 1000).toFixed(1)}s` },
  ];

  return (
    <div>
      <div className="grid grid-cols-4 gap-4 mb-5">
        {metrics.map((m) => (
          <div key={m.label} className="bg-[#161b22] border border-[#21262d] rounded-lg p-3.5">
            <div className="text-[10px] text-[#8b949e] uppercase tracking-wider">{m.label}</div>
            <div className="text-xl font-semibold text-[#e6edf3] mt-1">{m.value}</div>
          </div>
        ))}
      </div>
      {(trace.agents_invoked as string[])?.length > 0 && (
        <div className="mb-4">
          <div className="text-[11px] text-[#8b949e] uppercase tracking-wider mb-2">Agents Invoked</div>
          <div className="flex flex-wrap gap-1.5">
            {(trace.agents_invoked as string[]).map((a) => (
              <span key={a} className="bg-blue-900/40 text-blue-400 px-2.5 py-1 rounded text-xs">{a}</span>
            ))}
          </div>
        </div>
      )}
      {(trace.tools_called as string[])?.length > 0 && (
        <div>
          <div className="text-[11px] text-[#8b949e] uppercase tracking-wider mb-2">Tools Called</div>
          <div className="flex flex-wrap gap-1.5">
            {(trace.tools_called as string[]).map((t) => (
              <span key={t} className="bg-[#21262d] text-[#c9d1d9] px-2.5 py-1 rounded text-xs">{t}</span>
            ))}
          </div>
        </div>
      )}
    </div>
  );
}
```

- [ ] **Step 4: Commit**

```bash
cd frontend && npm run lint
git add src/components/history/
git commit -m "feat: add history page components (filters, detail modal, tabs)"
```

---

## Task 9: Frontend — History Page + Sidebar Nav

**Files:**
- Create: `frontend/src/app/history/page.tsx`
- Modify: `frontend/src/components/layout/sidebar.tsx` (add nav item after Chat, ~line 110)

- [ ] **Step 1: Create the history page**

```typescript
// frontend/src/app/history/page.tsx
"use client";

import { useCallback, useEffect } from "react";
import { useQuery } from "@tanstack/react-query";
import { fetchHistory, retryRun } from "@/lib/api";
import { useHistoryStore } from "@/stores/history-store";
import { useJarvisWs } from "@/hooks/use-jarvis-ws";
import { RunRow } from "@/components/history/run-row";
import { HistoryFilters } from "@/components/history/history-filters";
import { RunDetailModal } from "@/components/history/run-detail-modal";

const LIMIT = 20;

export default function HistoryPage() {
  const { items, total, offset, filters, setItems, appendItems, setOffset, updateRunLiveState } =
    useHistoryStore();

  const queryParams = {
    status: filters.status,
    source: filters.source,
    search: filters.search,
    from: filters.dateFrom ?? undefined,
    to: filters.dateTo ?? undefined,
    limit: LIMIT,
    offset,
  };

  const { data, isLoading, refetch } = useQuery({
    queryKey: ["history", queryParams],
    queryFn: () => fetchHistory(queryParams),
    refetchInterval: 30000,
  });

  useEffect(() => {
    if (!data) return;
    if (offset === 0) {
      setItems(data.items, data.total);
    } else {
      appendItems(data.items, data.total);
    }
  }, [data, offset, setItems, appendItems]);

  // WebSocket — live execution updates
  const onSurfaceUpdate = useCallback(
    (update: { surface_id: string; phase?: string; steps?: unknown[]; current_step?: string | null; progress?: string; approval?: unknown }) => {
      updateRunLiveState(update.surface_id, update as Parameters<typeof updateRunLiveState>[1]);
    },
    [updateRunLiveState]
  );

  useJarvisWs({ onSurfaceUpdate });

  const handleLoadMore = useCallback(() => {
    if (items.length < total) {
      setOffset(offset + LIMIT);
    }
  }, [items.length, total, offset, setOffset]);

  const handleRetry = useCallback(
    async (runId: string) => {
      await retryRun(runId);
      refetch();
    },
    [refetch]
  );

  // Summary stats
  const activeCount = items.filter((i) => ["running", "pending", "awaiting_approval"].includes(i.status)).length;
  const completedToday = items.filter((i) => {
    if (i.status !== "completed" || !i.completed_at) return false;
    const today = new Date().toDateString();
    return new Date(i.completed_at).toDateString() === today;
  }).length;
  const failedCount = items.filter((i) => i.status === "failed").length;
  const dailyCost = items.reduce((sum, i) => sum + (i.cost_usd ?? 0), 0);

  return (
    <div className="flex-1 bg-[#0d1117] min-h-screen">
      <HistoryFilters />

      {/* Summary stats */}
      <div className="flex gap-6 px-5 py-2.5 border-b border-[#21262d] text-xs text-[#8b949e]">
        <span>
          <span className="text-green-400">{activeCount}</span> active
        </span>
        <span>
          <span className="text-[#e6edf3]">{completedToday}</span> completed today
        </span>
        <span>
          <span className="text-red-400">{failedCount}</span> failed
        </span>
        <span className="ml-auto">${dailyCost.toFixed(2)} today</span>
      </div>

      {/* Timeline */}
      {isLoading && items.length === 0 ? (
        <div className="flex items-center justify-center py-20 text-[#8b949e] text-sm">
          Loading history...
        </div>
      ) : items.length === 0 ? (
        <div className="flex items-center justify-center py-20 text-[#8b949e] text-sm">
          No runs found
        </div>
      ) : (
        <>
          {items.map((item) => (
            <RunRow key={item.run_id} item={item} />
          ))}
          {items.length < total && (
            <div className="py-4 text-center">
              <button
                onClick={handleLoadMore}
                className="text-[#58a6ff] text-sm hover:underline"
              >
                Load more runs...
              </button>
            </div>
          )}
        </>
      )}

      <RunDetailModal />
    </div>
  );
}
```

- [ ] **Step 2: Add History nav item to sidebar**

In `frontend/src/components/layout/sidebar.tsx`, add a History nav item after Chat (~line 110). Follow the exact pattern of the existing nav items. Use `href="/history"` and an appropriate icon (e.g., ClockIcon or ListBulletIcon from heroicons).

- [ ] **Step 3: Test in browser**

```bash
cd frontend && npm run dev
```

Open `http://localhost:3000/history` and verify:
- Page loads without errors
- Filter bar renders
- Summary stats bar renders
- If no runs exist, "No runs found" message shows
- Sidebar shows History nav item
- Clicking History in sidebar navigates to `/history`

- [ ] **Step 4: Commit**

```bash
cd frontend && npm run lint && npm run build
git add src/app/history/ src/components/layout/sidebar.tsx src/components/history/
git commit -m "feat: add history page with sidebar navigation"
```

---

## Task 10: Integration Testing + Final Verification

**Files:**
- All files from previous tasks

- [ ] **Step 1: Run backend test suite**

```bash
cd backend && python -m pytest tests/ -v --timeout=30 2>&1 | tail -30
```

Expected: All tests pass, no imports from deleted `routes_runs`.

- [ ] **Step 2: Run frontend build**

```bash
cd frontend && npm run build
```

Expected: Build succeeds with no type errors.

- [ ] **Step 3: Start both servers and test end-to-end**

```bash
# Terminal 1: Backend
cd backend && source .venv/bin/activate && python run.py

# Terminal 2: Frontend
cd frontend && npm run dev
```

Open `http://localhost:3000/history` and verify:
- History page loads with runs from the backend
- Active runs auto-expand with inline step list
- Clicking a run opens the detail modal
- Modal tabs (Steps, Plan, Events, Trace) work
- Filter bar filters runs
- "Load more" pagination works
- If a run is active, the pulsing blue dot animates

- [ ] **Step 4: Final commit**

```bash
git add -A
git commit -m "test: verify history page end-to-end integration"
```
