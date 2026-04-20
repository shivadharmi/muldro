"""History API — unified view of plans, runs, steps with live state."""

import logging
from datetime import datetime

from fastapi import APIRouter, Depends, HTTPException, Query
from sqlalchemy import func, select
from sqlalchemy.ext.asyncio import AsyncSession

from src.api.deps import get_current_user_id, get_current_workspace_id, get_session
from src.api.schemas_history import (
    HistoryApprovalContext,
    HistoryApprovalRecord,
    HistoryArtifactRef,
    HistoryDetailResponse,
    HistoryDetailStep,
    HistoryEventEntry,
    HistoryItemResponse,
    HistoryListResponse,
    HistoryPlanContext,
    HistoryStepSummary,
    HistoryTraceInfo,
    RunActionResponse,
)
from src.config.settings import Settings, get_settings
from src.models.approvals import Approval
from src.models.plans import Plan
from src.models.runtime_event import RuntimeEvent
from src.models.task_graph import TaskRun, TaskStep
from src.services.execution_state import transition_run

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
                name=_name_from_step(step),
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


@router.get("/v1/history/{run_id}", response_model=HistoryDetailResponse)
async def get_history_detail(
    run_id: str,
    user_id: str = Depends(get_current_user_id),
    workspace_id: str = Depends(get_current_workspace_id),
    db: AsyncSession = Depends(get_session),
) -> HistoryDetailResponse:
    """Return full context for a single task run (used by the detail modal)."""

    # ------------------------------------------------------------------ #
    # 1. Fetch TaskRun — 404 if not found or not owned by this user/workspace
    # ------------------------------------------------------------------ #
    run_result = await db.execute(
        select(TaskRun).where(
            TaskRun.run_id == run_id,
            TaskRun.user_id == user_id,
            TaskRun.workspace_id == workspace_id,
        )
    )
    run: TaskRun | None = run_result.scalar_one_or_none()
    if run is None:
        raise HTTPException(status_code=404, detail="Run not found")

    # ------------------------------------------------------------------ #
    # 2. Fetch all TaskSteps ordered by created_at
    # ------------------------------------------------------------------ #
    steps_result = await db.execute(
        select(TaskStep).where(TaskStep.run_id == run_id).order_by(TaskStep.created_at)
    )
    raw_steps: list[TaskStep] = steps_result.scalars().all()

    # ------------------------------------------------------------------ #
    # 3. Build detail steps (with duration + artifacts)
    # ------------------------------------------------------------------ #
    detail_steps: list[HistoryDetailStep] = []
    for s in raw_steps:
        # Duration from started_at / completed_at
        duration_ms: int | None = None
        if s.started_at and s.completed_at:
            delta = s.completed_at - s.started_at
            duration_ms = int(delta.total_seconds() * 1000)

        # Artifacts — try/except because model may not exist in all envs
        artifacts: list[HistoryArtifactRef] = []
        try:
            from src.models.artifacts import Artifact

            art_result = await db.execute(select(Artifact).where(Artifact.step_id == s.step_id))
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
                name=_name_from_step(s),
                capability=_capability_from_step(s),
                status=s.status,
                input_data=s.input_data,
                output_data=s.output_data,
                started_at=s.started_at,
                completed_at=s.completed_at,
                duration_ms=duration_ms,
                error=s.error,
                artifacts=artifacts,
            )
        )

    # ------------------------------------------------------------------ #
    # 4. Plan context
    # ------------------------------------------------------------------ #
    plan_ctx: HistoryPlanContext | None = None
    if run.plan_id:
        plan_result = await db.execute(select(Plan).where(Plan.plan_id == run.plan_id))
        plan: Plan | None = plan_result.scalar_one_or_none()
        if plan:
            plan_ctx = HistoryPlanContext(
                plan_id=plan.plan_id,
                goal=plan.goal,
                reasoning_summary=plan.reasoning_summary,
                success_conditions=(
                    list(plan.success_conditions) if plan.success_conditions else None
                ),
                trigger_type=plan.trigger_type,
                priority=plan.priority,
            )

    # ------------------------------------------------------------------ #
    # 5. Approvals linked to this run (execution_id == run_id)
    # ------------------------------------------------------------------ #
    approvals_result = await db.execute(select(Approval).where(Approval.execution_id == run_id))
    approval_records: list[HistoryApprovalRecord] = [
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
        for a in approvals_result.scalars().all()
    ]

    # ------------------------------------------------------------------ #
    # 6. RuntimeEvents ordered by occurred_at
    # ------------------------------------------------------------------ #
    events_result = await db.execute(
        select(RuntimeEvent)
        .where(
            RuntimeEvent.workspace_id == workspace_id,
            RuntimeEvent.run_id == run_id,
        )
        .order_by(RuntimeEvent.occurred_at)
    )
    event_entries: list[HistoryEventEntry] = [
        HistoryEventEntry(
            event_type=e.event_type,
            occurred_at=e.occurred_at,
            step_id=e.step_id,
            payload=e.payload or {},
        )
        for e in events_result.scalars().all()
    ]

    # ------------------------------------------------------------------ #
    # 7. Trace info — resolve Trace via run.trace_id, fall back to the
    #    reverse index (traces.run_id) for legacy runs, finally fall back
    #    to the run.cost_usd / run.input_tokens rollup cached on the
    #    TaskRun row itself.
    # ------------------------------------------------------------------ #
    from src.api.schemas_history import HistoryTraceStep
    from src.models.traces import ModelCall
    from src.models.traces import Trace as TraceModel

    trace: HistoryTraceInfo | None = None
    run_duration_ms = 0
    if run.started_at and run.completed_at:
        delta = run.completed_at - run.started_at
        run_duration_ms = int(delta.total_seconds() * 1000)

    trace_row = None
    if run.trace_id:
        trace_row = (
            await db.execute(select(TraceModel).where(TraceModel.trace_id == run.trace_id))
        ).scalar_one_or_none()
    if trace_row is None:
        # Defensive fallback: resolve by the reverse index
        trace_row = (
            await db.execute(select(TraceModel).where(TraceModel.run_id == run.run_id))
        ).scalar_one_or_none()

    step_breakdown: list[HistoryTraceStep] = []
    if trace_row is not None:
        # Per-step breakdown: ModelCall entries grouped by the span's
        # associated step_id (stored via decision field or in metadata).
        # ModelCall rows don't natively carry step_id, so we group by
        # agent_name as a reasonable proxy — this still gives the user
        # visibility into which agents consumed the tokens.
        calls = (
            (await db.execute(select(ModelCall).where(ModelCall.trace_id == trace_row.trace_id)))
            .scalars()
            .all()
        )

        by_agent: dict[str, HistoryTraceStep] = {}
        for c in calls:
            key = c.agent_name or "unknown"
            entry = by_agent.get(key)
            if entry is None:
                entry = HistoryTraceStep(step_id=key, agent=key, model=c.model)
                by_agent[key] = entry
            entry.calls += 1
            entry.input_tokens += c.input_tokens or 0
            entry.output_tokens += c.output_tokens or 0
            entry.cost_usd = round(entry.cost_usd + float(c.cost_usd or 0), 6)
            entry.duration_ms += c.duration_ms or 0
        step_breakdown = list(by_agent.values())

        trace = HistoryTraceInfo(
            trace_id=trace_row.trace_id,
            input_tokens=trace_row.total_input_tokens or 0,
            output_tokens=trace_row.total_output_tokens or 0,
            cost_usd=float(trace_row.total_cost_usd or 0.0),
            duration_ms=trace_row.duration_ms or run_duration_ms,
            agents_invoked=trace_row.agents_invoked or [],
            tools_called=trace_row.tools_called or [],
            step_breakdown=step_breakdown,
        )

    # Secondary fallback: rollup columns on the TaskRun. Populated by
    # GraphExecutor._finalize_trace even when the Trace row persist step
    # fails, so the UI always has a non-zero result for a completed run
    # that made real API calls.
    if trace is None and (
        (run.input_tokens or 0) or (run.output_tokens or 0) or (run.cost_usd or 0)
    ):
        trace = HistoryTraceInfo(
            trace_id=run.trace_id,
            input_tokens=int(run.input_tokens or 0),
            output_tokens=int(run.output_tokens or 0),
            cost_usd=float(run.cost_usd or 0.0),
            duration_ms=run_duration_ms,
        )

    # Final fallback: duration-only from run timestamps
    if trace is None and run_duration_ms:
        trace = HistoryTraceInfo(
            trace_id=run.trace_id,
            duration_ms=run_duration_ms,
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
        approvals=approval_records,
        trace=trace,
        events=event_entries,
    )


def _capability_from_step(step: TaskStep) -> str | None:
    """Extract capability string from step input_data if present."""
    if step.input_data and isinstance(step.input_data, dict):
        return step.input_data.get("capability") or step.input_data.get("task_type")
    return None


def _name_from_step(step: TaskStep) -> str | None:
    """Extract a human-readable name from a step, checking all available fields."""
    if step.name:
        return step.name
    if step.input_data and isinstance(step.input_data, dict):
        return (
            step.input_data.get("description")
            or step.input_data.get("goal")
            or step.input_data.get("capability")
            or step.input_data.get("task_type")
        )
    return None


# ---------------------------------------------------------------------------
# Action endpoints: retry / cancel / resume
# ---------------------------------------------------------------------------

_TERMINAL_STATUSES = {"completed", "failed", "cancelled", "archived", "timed_out"}


@router.post("/v1/history/{run_id}/retry", response_model=RunActionResponse)
async def retry_run(
    run_id: str,
    user_id: str = Depends(get_current_user_id),
    workspace_id: str = Depends(get_current_workspace_id),
    db: AsyncSession = Depends(get_session),
) -> RunActionResponse:
    """Retry a failed or timed-out run by resetting it to pending."""
    result = await db.execute(
        select(TaskRun).where(
            TaskRun.run_id == run_id,
            TaskRun.user_id == user_id,
            TaskRun.workspace_id == workspace_id,
        )
    )
    run: TaskRun | None = result.scalar_one_or_none()
    if run is None:
        raise HTTPException(status_code=404, detail="Run not found")

    if run.status not in ("failed", "timed_out"):
        raise HTTPException(
            status_code=400,
            detail=f"Run cannot be retried (status={run.status}). "
            "Only failed or timed_out runs can be retried.",
        )

    transition_run(run, "pending")
    run.source = "approval_resume"
    run.error = None
    run.completed_at = None
    await db.commit()

    return RunActionResponse(run_id=run.run_id, status=run.status, message="Run queued for retry.")


@router.post("/v1/runs/{run_id}/cancel", response_model=RunActionResponse)
async def cancel_run(
    run_id: str,
    user_id: str = Depends(get_current_user_id),
    workspace_id: str = Depends(get_current_workspace_id),
    settings: Settings = Depends(get_settings),
    db: AsyncSession = Depends(get_session),
) -> RunActionResponse:
    """Cancel a running or paused run."""
    result = await db.execute(
        select(TaskRun).where(
            TaskRun.run_id == run_id,
            TaskRun.user_id == user_id,
            TaskRun.workspace_id == workspace_id,
        )
    )
    run: TaskRun | None = result.scalar_one_or_none()
    if run is None:
        raise HTTPException(status_code=404, detail="Run not found")

    if run.status in _TERMINAL_STATUSES:
        raise HTTPException(
            status_code=400,
            detail=f"Run is already in terminal state (status={run.status})",
        )

    try:
        from src.services.graph_executor import create_graph_executor

        executor = await create_graph_executor(settings=settings, db=db, workspace_id=workspace_id)
        await executor.cancel_run(run_id)
    except Exception:
        logger.exception("GraphExecutor cancel failed for run %s — falling back", run_id)
        transition_run(run, "cancelled")

    await db.commit()

    return RunActionResponse(run_id=run_id, status=run.status, message="Run cancelled.")


@router.post("/v1/runs/{run_id}/resume", response_model=RunActionResponse)
async def resume_run(
    run_id: str,
    user_id: str = Depends(get_current_user_id),
    workspace_id: str = Depends(get_current_workspace_id),
    db: AsyncSession = Depends(get_session),
) -> RunActionResponse:
    """Queue a paused or awaiting run for resume on the next scheduler tick."""
    result = await db.execute(
        select(TaskRun).where(
            TaskRun.run_id == run_id,
            TaskRun.user_id == user_id,
            TaskRun.workspace_id == workspace_id,
        )
    )
    run: TaskRun | None = result.scalar_one_or_none()
    if run is None:
        raise HTTPException(status_code=404, detail="Run not found")

    resumable = {"paused", "awaiting_approval", "awaiting_input"}
    if run.status not in resumable:
        raise HTTPException(
            status_code=400,
            detail=f"Run cannot be resumed (status={run.status}). "
            "Only paused, awaiting_approval, or awaiting_input runs can be resumed.",
        )

    run.source = "approval_resume"
    await db.commit()

    return RunActionResponse(run_id=run.run_id, status=run.status, message="Run queued for resume.")
