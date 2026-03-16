"""Workflow endpoints — list, start, and track workflow runs."""

import logging
from datetime import datetime, timezone

from fastapi import APIRouter, Depends, HTTPException
from pydantic import BaseModel

from src.api.deps import get_current_user_id

logger = logging.getLogger(__name__)

router = APIRouter()


class WorkflowSummary(BaseModel):
    name: str
    description: str
    step_count: int
    tags: list[str] = []


class WorkflowRunResponse(BaseModel):
    workflow_name: str
    status: str
    started_at: str
    steps_completed: int = 0
    steps_total: int = 0
    result: dict | None = None


# In-memory run tracking (would be DB-backed in production)
_active_runs: dict[str, dict] = {}


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
):
    """Start a workflow by name."""
    registry = _get_registry()
    workflow = registry.get(name)
    if not workflow:
        raise HTTPException(status_code=404, detail=f"Workflow '{name}' not found")

    context = req.params if req and req.params else {}
    context["user_id"] = user_id

    started_at = datetime.now(timezone.utc).isoformat()
    steps_completed = 0

    # Execute steps sequentially
    for step in workflow.steps:
        if step.requires_approval:
            # In production: create approval, pause, wait for resume
            break
        try:
            result = await step.handler(context)
            context.update(result)
            steps_completed += 1
        except Exception as e:
            logger.error("Workflow %s step %s failed: %s", name, step.name, e)
            return WorkflowRunResponse(
                workflow_name=name,
                status="failed",
                started_at=started_at,
                steps_completed=steps_completed,
                steps_total=len(workflow.steps),
                result={"error": str(e), "failed_step": step.name},
            )

    status = "completed" if steps_completed == len(workflow.steps) else "awaiting_approval"
    return WorkflowRunResponse(
        workflow_name=name,
        status=status,
        started_at=started_at,
        steps_completed=steps_completed,
        steps_total=len(workflow.steps),
        result=context,
    )


@router.get("/v1/workflows/{name}/runs", response_model=list[WorkflowRunResponse])
async def list_workflow_runs(
    name: str,
    user_id: str = Depends(get_current_user_id),
):
    """List runs for a workflow."""
    # In production: query DB. For now return empty.
    return []
