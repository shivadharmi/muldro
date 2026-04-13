"""History API — unified view of plans, runs, steps with live state."""

import logging
from datetime import datetime

from fastapi import APIRouter, Depends, Query
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

# Map UI filter values to DB status sets
_STATUS_MAP: dict[str, list[str]] = {
    "executing": ["running", "pending"],
    "completed": ["completed"],
    "failed": ["failed"],
    "awaiting_approval": ["awaiting_approval"],
    "cancelled": ["cancelled"],
}


@router.get("/v1/history", response_model=HistoryListResponse)
async def list_history(
    status: str = Query("all"),
    source: str = Query("all"),
    search: str | None = Query(None),
    date_from: datetime | None = Query(None, alias="from"),
    date_to: datetime | None = Query(None, alias="to"),
    limit: int = Query(20, ge=1, le=50),
    offset: int = Query(0, ge=0),
    user_id: str = Depends(get_current_user_id),
    workspace_id: str = Depends(get_current_workspace_id),
    db: AsyncSession = Depends(get_session),
) -> HistoryListResponse:
    """Return paginated history of all task runs for the workspace."""

    # ------------------------------------------------------------------ #
    # Build base WHERE clause
    # ------------------------------------------------------------------ #
    base_filters = [
        TaskRun.user_id == user_id,
        TaskRun.workspace_id == workspace_id,
    ]

    if status != "all" and status in _STATUS_MAP:
        base_filters.append(TaskRun.status.in_(_STATUS_MAP[status]))

    if source != "all":
        base_filters.append(TaskRun.source == source)

    if date_from is not None:
        base_filters.append(TaskRun.created_at >= date_from)

    if date_to is not None:
        base_filters.append(TaskRun.created_at <= date_to)

    # Search filter requires joining Plan
    if search:
        plan_ids_stmt = select(Plan.plan_id).where(
            Plan.workspace_id == workspace_id,
            Plan.goal.ilike(f"%{search}%"),
        )
        plan_ids_result = await db.execute(plan_ids_stmt)
        matching_plan_ids = [row for row in plan_ids_result.scalars().all()]
        base_filters.append(TaskRun.plan_id.in_(matching_plan_ids))

    # ------------------------------------------------------------------ #
    # Count total
    # ------------------------------------------------------------------ #
    count_stmt = select(func.count()).select_from(TaskRun).where(*base_filters)
    count_result = await db.execute(count_stmt)
    total: int = count_result.scalar() or 0

    # ------------------------------------------------------------------ #
    # Fetch runs (ordered newest first)
    # ------------------------------------------------------------------ #
    runs_stmt = (
        select(TaskRun)
        .where(*base_filters)
        .order_by(TaskRun.created_at.desc())
        .limit(limit)
        .offset(offset)
    )
    runs_result = await db.execute(runs_stmt)
    runs: list[TaskRun] = runs_result.scalars().all()

    # ------------------------------------------------------------------ #
    # Enrich each run
    # ------------------------------------------------------------------ #
    items: list[HistoryItemResponse] = []
    for run in runs:
        # Steps (compact)
        steps_result = await db.execute(
            select(TaskStep).where(TaskStep.run_id == run.run_id).order_by(TaskStep.step_order)
        )
        steps = steps_result.scalars().all()

        step_summaries = [
            HistoryStepSummary(
                step_id=step.step_id,
                name=step.name,
                capability=_capability_from_step(step),
                status=step.status,
                started_at=step.started_at,
                completed_at=step.completed_at,
            )
            for step in steps
        ]

        completed_step_count = sum(1 for s in steps if s.status == "completed")

        # Plan context
        goal: str | None = None
        trigger_type: str | None = None
        risk_level: str | None = None
        if run.plan_id:
            plan_result = await db.execute(select(Plan).where(Plan.plan_id == run.plan_id))
            plan = plan_result.scalar_one_or_none()
            if plan:
                goal = plan.goal
                trigger_type = plan.trigger_type
                risk_level = plan.risk_level

        # Approval context (only for awaiting_approval runs)
        approval_ctx: HistoryApprovalContext | None = None
        if run.status == "awaiting_approval":
            appr_result = await db.execute(
                select(Approval).where(
                    Approval.run_id == run.run_id,
                    Approval.status == "pending",
                )
            )
            appr = appr_result.scalar_one_or_none()
            if appr:
                approval_ctx = HistoryApprovalContext(
                    approval_id=appr.approval_id,
                    step_id=appr.step_id,
                    step_description=appr.title,
                    risk_level=appr.risk_level or "low",
                )

        # Live surface state
        live_phase: str | None = None
        surface_id: str | None = None
        try:
            from src.models.ui_state import UISurface

            surf_result = await db.execute(
                select(UISurface).where(
                    UISurface.workspace_id == workspace_id,
                    UISurface.user_id == user_id,
                )
            )
            surfaces = surf_result.scalars().all()
            for surf in surfaces:
                payload = surf.payload or {}
                if payload.get("source_run_id") == run.run_id:
                    surface_id = surf.surface_id
                    last_update = payload.get("last_surface_update", {})
                    live_phase = last_update.get("phase")
                    break
        except Exception:
            pass

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
                retry_count=run.retry_count,
                step_count=len(steps),
                completed_step_count=completed_step_count,
                steps=step_summaries,
                approval=approval_ctx,
                live_phase=live_phase,
                surface_id=surface_id,
            )
        )

    return HistoryListResponse(
        items=items,
        total=total,
        limit=limit,
        offset=offset,
    )


def _capability_from_step(step: TaskStep) -> str | None:
    """Extract capability string from step input_data if present."""
    if step.input_data and isinstance(step.input_data, dict):
        return step.input_data.get("capability")
    return None
