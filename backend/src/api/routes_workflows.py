"""Workflow endpoints — list, start, track runs on TaskRun substrate."""

import logging

from fastapi import APIRouter, Depends, HTTPException
from pydantic import BaseModel
from sqlalchemy import func, select
from sqlalchemy.ext.asyncio import AsyncSession

from src.api.deps import get_current_user_id, get_current_workspace_id, get_session
from src.models.ids import generate_id
from src.models.task_graph import TaskRun, TaskStep
from src.services.execution_state import transition_run, transition_step

logger = logging.getLogger(__name__)

router = APIRouter()


class WorkflowSummary(BaseModel):
    name: str
    description: str
    step_count: int
    tags: list[str] = []


class WorkflowRunResponse(BaseModel):
    run_id: str
    workflow_name: str
    status: str
    started_at: str | None = None
    steps_completed: int = 0
    steps_total: int = 0
    result: dict | None = None


def _get_registry():
    from src.workflows.inbox_triage import inbox_triage_workflow
    from src.workflows.research_agent import research_workflow
    from src.workflows.workflow_registry import WorkflowRegistry

    registry = WorkflowRegistry()
    registry.register(inbox_triage_workflow)
    registry.register(research_workflow)
    return registry


@router.get("/v1/workflows", response_model=list[WorkflowSummary])
async def list_workflows(
    user_id: str = Depends(get_current_user_id),
):
    """List all available workflows."""
    registry = _get_registry()
    return [
        WorkflowSummary(
            name=w.name,
            description=w.description,
            step_count=len(w.steps),
            tags=w.tags,
        )
        for w in registry.list_workflows()
    ]


class WorkflowStartRequest(BaseModel):
    params: dict | None = None


@router.post("/v1/workflows/{name}/start", response_model=WorkflowRunResponse)
async def start_workflow(
    name: str,
    req: WorkflowStartRequest | None = None,
    user_id: str = Depends(get_current_user_id),
    workspace_id: str = Depends(get_current_workspace_id),
    db: AsyncSession = Depends(get_session),
):
    """Start a workflow by name. Persists the run on TaskRun substrate."""
    registry = _get_registry()
    workflow = registry.get(name)
    if not workflow:
        raise HTTPException(status_code=404, detail=f"Workflow '{name}' not found")

    # Create a TaskRun to track this workflow
    run_id = generate_id("run")
    run = TaskRun(
        run_id=run_id,
        user_id=user_id,
        workspace_id=workspace_id,
        source="workflow",
        status="pending",
        policy_decision={"workflow_name": name},
    )
    db.add(run)

    # Create TaskSteps for each workflow step
    step_ids: list[str] = []
    for i, step_def in enumerate(workflow.steps):
        step_id = generate_id("step")
        step = TaskStep(
            step_id=step_id,
            run_id=run_id,
            workspace_id=workspace_id,
            step_type=step_def.name,
            step_index=i,
            status="pending",
            input_data={"requires_approval": step_def.requires_approval},
        )
        db.add(step)
        step_ids.append(step_id)

    await db.flush()

    # Transition run to running
    transition_run(run, "running")

    # Execute steps
    from src.workflows.context import WorkflowContext

    context = WorkflowContext.from_params(user_id, dict(req.params) if req and req.params else {})
    steps_completed = 0

    step_result = await db.execute(
        select(TaskStep).where(TaskStep.run_id == run_id).order_by(TaskStep.step_index)
    )
    db_steps = list(step_result.scalars().all())

    for i, step_def in enumerate(workflow.steps):
        db_step = db_steps[i] if i < len(db_steps) else None

        if step_def.requires_approval:
            if db_step:
                transition_step(db_step, "awaiting_approval")
            transition_run(run, "awaiting_approval")
            break

        if db_step:
            transition_step(db_step, "running")

        try:
            result = await step_def.handler(context)
            context.update(result)
            steps_completed += 1
            if db_step:
                db_step.output_data = {"summary": str(result)[:500]} if result else {}
                transition_step(db_step, "completed")
        except Exception as e:
            logger.error("Workflow %s step %s failed: %s", name, step_def.name, e)
            if db_step:
                db_step.error = str(e)[:1000]
                transition_step(db_step, "failed")
            transition_run(run, "failed")
            await db.commit()
            return WorkflowRunResponse(
                run_id=run_id,
                workflow_name=name,
                status="failed",
                started_at=run.created_at.isoformat() if run.created_at else None,
                steps_completed=steps_completed,
                steps_total=len(workflow.steps),
                result={"error": str(e), "failed_step": step_def.name},
            )

    if steps_completed == len(workflow.steps):
        transition_run(run, "completed")

    await db.commit()

    return WorkflowRunResponse(
        run_id=run_id,
        workflow_name=name,
        status=run.status,
        started_at=run.created_at.isoformat() if run.created_at else None,
        steps_completed=steps_completed,
        steps_total=len(workflow.steps),
    )


@router.get("/v1/workflows/{name}/runs", response_model=list[WorkflowRunResponse])
async def list_workflow_runs(
    name: str,
    limit: int = 20,
    user_id: str = Depends(get_current_user_id),
    workspace_id: str = Depends(get_current_workspace_id),
    db: AsyncSession = Depends(get_session),
):
    """List runs for a workflow from the TaskRun table."""
    result = await db.execute(
        select(TaskRun)
        .where(
            TaskRun.workspace_id == workspace_id,
            TaskRun.source == "workflow",
            TaskRun.policy_decision["workflow_name"].astext == name,
        )
        .order_by(TaskRun.created_at.desc())
        .limit(limit)
    )
    runs = result.scalars().all()

    responses = []
    for run in runs:
        step_count = (
            await db.scalar(
                select(func.count()).select_from(TaskStep).where(TaskStep.run_id == run.run_id)
            )
            or 0
        )
        completed_count = (
            await db.scalar(
                select(func.count())
                .select_from(TaskStep)
                .where(TaskStep.run_id == run.run_id, TaskStep.status == "completed")
            )
            or 0
        )

        responses.append(
            WorkflowRunResponse(
                run_id=run.run_id,
                workflow_name=name,
                status=run.status,
                started_at=run.created_at.isoformat() if run.created_at else None,
                steps_completed=completed_count,
                steps_total=step_count,
            )
        )

    return responses


@router.get("/v1/workflows/runs/{run_id}", response_model=WorkflowRunResponse)
async def get_workflow_run(
    run_id: str,
    user_id: str = Depends(get_current_user_id),
    workspace_id: str = Depends(get_current_workspace_id),
    db: AsyncSession = Depends(get_session),
):
    """Get details for a specific workflow run."""
    result = await db.execute(
        select(TaskRun).where(
            TaskRun.run_id == run_id,
            TaskRun.workspace_id == workspace_id,
        )
    )
    run = result.scalar_one_or_none()
    if not run:
        raise HTTPException(status_code=404, detail=f"Run {run_id} not found")

    step_count = (
        await db.scalar(select(func.count()).select_from(TaskStep).where(TaskStep.run_id == run_id))
        or 0
    )
    completed_count = (
        await db.scalar(
            select(func.count())
            .select_from(TaskStep)
            .where(TaskStep.run_id == run_id, TaskStep.status == "completed")
        )
        or 0
    )

    wf_name = ""
    if run.policy_decision and isinstance(run.policy_decision, dict):
        wf_name = run.policy_decision.get("workflow_name", "")

    return WorkflowRunResponse(
        run_id=run.run_id,
        workflow_name=wf_name,
        status=run.status,
        started_at=run.created_at.isoformat() if run.created_at else None,
        steps_completed=completed_count,
        steps_total=step_count,
    )
