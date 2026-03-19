"""Run API routes — view task run state, steps, traces, and artifacts."""

import logging

from fastapi import APIRouter, Depends, HTTPException
from pydantic import BaseModel
from sqlalchemy import select

from src.api.deps import get_current_user_id, get_current_workspace_id, get_session

router = APIRouter()
logger = logging.getLogger(__name__)


class StepResponse(BaseModel):
    step_id: str
    task_id: str
    name: str | None = None
    step_type: str | None = None
    status: str
    depends_on: list[str] | None = None
    input_data: dict | None = None
    output_data: dict | None = None
    error: dict | None = None
    started_at: str | None = None
    completed_at: str | None = None


class RunResponse(BaseModel):
    run_id: str
    plan_id: str | None = None
    user_id: str
    status: str
    started_at: str | None = None
    completed_at: str | None = None
    error: dict | None = None
    retry_count: int = 0
    step_count: int = 0


class RunDetailResponse(RunResponse):
    steps: list[StepResponse] = []


class RunTraceResponse(BaseModel):
    """Trace summary associated with a run."""

    trace_id: str
    status: str | None = None
    started_at: str | None = None
    ended_at: str | None = None
    duration_ms: int = 0
    total_input_tokens: int = 0
    total_output_tokens: int = 0
    total_cost_usd: float = 0.0
    span_count: int = 0
    agents_invoked: list[str] = []
    tools_called: list[str] = []
    final_result: str | None = None


class ArtifactResponse(BaseModel):
    artifact_id: str
    artifact_type: str
    title: str | None = None
    content: dict | None = None
    run_id: str | None = None
    step_id: str | None = None


@router.get("/v1/runs/{run_id}", response_model=RunDetailResponse)
async def get_run(
    run_id: str,
    user_id: str = Depends(get_current_user_id),
    workspace_id: str = Depends(get_current_workspace_id),
    db=Depends(get_session),
):
    """Get a task run with its steps."""
    from src.models.task_graph import TaskRun, TaskStep

    result = await db.execute(
        select(TaskRun).where(
            TaskRun.run_id == run_id,
            TaskRun.user_id == user_id,
            TaskRun.workspace_id == workspace_id,
        )
    )
    run = result.scalar_one_or_none()
    if not run:
        raise HTTPException(status_code=404, detail="Run not found")

    steps_result = await db.execute(
        select(TaskStep).where(TaskStep.run_id == run_id).order_by(TaskStep.step_order)
    )
    steps = steps_result.scalars().all()

    return RunDetailResponse(
        run_id=run.run_id,
        plan_id=run.plan_id,
        user_id=run.user_id,
        status=run.status,
        started_at=run.started_at.isoformat() if run.started_at else None,
        completed_at=run.completed_at.isoformat() if run.completed_at else None,
        error=run.error,
        retry_count=run.retry_count,
        step_count=len(steps),
        steps=[
            StepResponse(
                step_id=s.step_id,
                task_id=s.task_id,
                name=s.name,
                step_type=s.step_type,
                status=s.status,
                depends_on=s.depends_on,
                input_data=s.input_data,
                output_data=s.output_data,
                error=s.error,
                started_at=s.started_at.isoformat() if s.started_at else None,
                completed_at=s.completed_at.isoformat() if s.completed_at else None,
            )
            for s in steps
        ],
    )


@router.get("/v1/runs/{run_id}/steps", response_model=list[StepResponse])
async def get_run_steps(
    run_id: str,
    user_id: str = Depends(get_current_user_id),
    workspace_id: str = Depends(get_current_workspace_id),
    db=Depends(get_session),
):
    """Get all steps for a run."""
    from src.models.task_graph import TaskRun, TaskStep

    # Verify run exists and belongs to user/workspace
    exists = await db.execute(
        select(TaskRun.run_id).where(
            TaskRun.run_id == run_id,
            TaskRun.user_id == user_id,
            TaskRun.workspace_id == workspace_id,
        )
    )
    if not exists.scalar_one_or_none():
        raise HTTPException(status_code=404, detail="Run not found")

    result = await db.execute(
        select(TaskStep).where(TaskStep.run_id == run_id).order_by(TaskStep.step_order)
    )
    steps = result.scalars().all()

    return [
        StepResponse(
            step_id=s.step_id,
            task_id=s.task_id,
            name=s.name,
            step_type=s.step_type,
            status=s.status,
            depends_on=s.depends_on,
            input_data=s.input_data,
            output_data=s.output_data,
            error=s.error,
            started_at=s.started_at.isoformat() if s.started_at else None,
            completed_at=s.completed_at.isoformat() if s.completed_at else None,
        )
        for s in steps
    ]


@router.get("/v1/runs/{run_id}/trace", response_model=RunTraceResponse)
async def get_run_trace(
    run_id: str,
    user_id: str = Depends(get_current_user_id),
    workspace_id: str = Depends(get_current_workspace_id),
    db=Depends(get_session),
):
    """Get the trace associated with a run (if any)."""
    from src.models.task_graph import TaskRun

    result = await db.execute(
        select(TaskRun.trace_id).where(
            TaskRun.run_id == run_id,
            TaskRun.user_id == user_id,
            TaskRun.workspace_id == workspace_id,
        )
    )
    trace_id = result.scalar_one_or_none()

    if not trace_id:
        raise HTTPException(status_code=404, detail="No trace found for this run")

    from src.services.trace_store import TraceStore

    store = TraceStore(db_factory=_get_db_factory())
    trace = await store.get_trace(trace_id)
    if not trace:
        raise HTTPException(status_code=404, detail="Trace not found")

    return RunTraceResponse(
        trace_id=trace.get("trace_id", trace_id),
        status=trace.get("status"),
        started_at=trace.get("started_at"),
        ended_at=trace.get("ended_at"),
        duration_ms=trace.get("duration_ms", 0),
        total_input_tokens=trace.get("total_input_tokens", 0),
        total_output_tokens=trace.get("total_output_tokens", 0),
        total_cost_usd=trace.get("total_cost_usd", 0.0),
        span_count=trace.get("span_count", 0),
        agents_invoked=trace.get("agents_invoked", []),
        tools_called=trace.get("tools_called", []),
        final_result=trace.get("final_result"),
    )


@router.get("/v1/runs/{run_id}/artifacts", response_model=list[ArtifactResponse])
async def get_run_artifacts(
    run_id: str,
    user_id: str = Depends(get_current_user_id),
    workspace_id: str = Depends(get_current_workspace_id),
    db=Depends(get_session),
):
    """Get artifacts produced by a run."""
    from src.models.artifacts import Artifact
    from src.models.task_graph import TaskRun

    # Verify run belongs to user/workspace
    exists = await db.execute(
        select(TaskRun.run_id).where(
            TaskRun.run_id == run_id,
            TaskRun.user_id == user_id,
            TaskRun.workspace_id == workspace_id,
        )
    )
    if not exists.scalar_one_or_none():
        raise HTTPException(status_code=404, detail="Run not found")

    result = await db.execute(select(Artifact).where(Artifact.run_id == run_id))
    artifacts = result.scalars().all()

    return [
        ArtifactResponse(
            artifact_id=a.artifact_id,
            artifact_type=a.artifact_type,
            title=a.title,
            content=a.content,
            run_id=a.run_id,
            step_id=a.step_id,
        )
        for a in artifacts
    ]


@router.post("/v1/runs/{run_id}/resume", response_model=RunResponse)
async def resume_run(
    run_id: str,
    user_id: str = Depends(get_current_user_id),
    workspace_id: str = Depends(get_current_workspace_id),
    db=Depends(get_session),
):
    """Resume a paused or awaiting_approval run."""
    from src.config.settings import get_settings
    from src.models.task_graph import TaskRun

    settings = get_settings()

    result = await db.execute(
        select(TaskRun).where(
            TaskRun.run_id == run_id,
            TaskRun.workspace_id == workspace_id,
        )
    )
    run = result.scalar_one_or_none()
    if not run:
        raise HTTPException(status_code=404, detail="Run not found")
    if run.user_id != user_id:
        raise HTTPException(status_code=403, detail="Not your run")
    if run.status not in ("paused", "awaiting_approval"):
        raise HTTPException(
            status_code=400,
            detail=f"Run is not paused (status={run.status})",
        )

    from src.services.graph_executor import GraphExecutor

    executor = GraphExecutor(settings=settings, db=db)
    run = await executor.resume_run(run_id)

    return RunResponse(
        run_id=run.run_id,
        plan_id=run.plan_id,
        user_id=run.user_id,
        status=run.status,
        started_at=run.started_at.isoformat() if run.started_at else None,
        completed_at=run.completed_at.isoformat() if run.completed_at else None,
        error=run.error,
        retry_count=run.retry_count,
    )


def _get_db_factory():
    from src.models.database import get_session_factory

    return get_session_factory()
