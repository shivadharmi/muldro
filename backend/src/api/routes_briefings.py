"""Briefing endpoints — daily briefing generation, list/detail/lifecycle."""

from datetime import date

from fastapi import APIRouter, Depends, HTTPException, Query
from pydantic import BaseModel
from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from src.api.deps import get_current_user_id, get_current_workspace_id, get_session
from src.api.schemas import BriefingResponse
from src.config.settings import Settings, get_settings
from src.services.presenter import Presenter

router = APIRouter()


class GoalBriefingResponse(BaseModel):
    goal_id: str
    title: str
    progress: float
    status: str
    recent_runs: list[dict] = []
    pending_approvals: list[dict] = []
    summary: str | None = None


@router.get("/v1/briefings/goal/{goal_id}", response_model=GoalBriefingResponse)
async def get_goal_briefing(
    goal_id: str,
    user_id: str = Depends(get_current_user_id),
    workspace_id: str = Depends(get_current_workspace_id),
    db: AsyncSession = Depends(get_session),
):
    """Get a briefing focused on a specific goal's progress, runs, and approvals."""
    from src.models.approvals import Approval
    from src.models.goals import Goal
    from src.models.task_graph import TaskRun
    from src.models.tasks import Task

    result = await db.execute(
        select(Goal).where(
            Goal.goal_id == goal_id, Goal.user_id == user_id, Goal.workspace_id == workspace_id
        )
    )
    goal = result.scalar_one_or_none()
    if not goal:
        raise HTTPException(status_code=404, detail="Goal not found")

    # Find tasks linked to this goal → their task_ids → runs referencing them
    tasks_result = await db.execute(select(Task.task_id).where(Task.goal_id == goal_id).limit(50))
    task_ids = list(tasks_result.scalars().all())

    # Recent runs for tasks under this goal
    recent_runs: list[dict] = []
    if task_ids:
        runs_result = await db.execute(
            select(TaskRun)
            .where(TaskRun.task_id_ref.in_(task_ids))
            .order_by(TaskRun.created_at.desc())
            .limit(10)
        )
        recent_runs = [
            {
                "run_id": r.run_id,
                "status": r.status,
                "source": r.source,
                "started_at": r.started_at.isoformat() if r.started_at else None,
                "completed_at": r.completed_at.isoformat() if r.completed_at else None,
            }
            for r in runs_result.scalars().all()
        ]

    # Pending approvals for this goal's runs
    pending_approvals: list[dict] = []
    run_ids = [r["run_id"] for r in recent_runs]
    if run_ids:
        apr_result = await db.execute(
            select(Approval)
            .where(
                Approval.execution_id.in_(run_ids),
                Approval.status == "pending",
            )
            .limit(10)
        )
        pending_approvals = [
            {
                "approval_id": a.approval_id,
                "title": a.title,
                "risk_level": a.risk_level,
            }
            for a in apr_result.scalars().all()
        ]

    return GoalBriefingResponse(
        goal_id=goal.goal_id,
        title=goal.title,
        progress=goal.progress or 0.0,
        status=goal.status,
        recent_runs=recent_runs,
        pending_approvals=pending_approvals,
    )


@router.get("/v1/briefings/{briefing_date}", response_model=BriefingResponse)
async def get_briefing(
    briefing_date: str,
    user_id: str = Depends(get_current_user_id),
    workspace_id: str = Depends(get_current_workspace_id),
    db: AsyncSession = Depends(get_session),
    settings: Settings = Depends(get_settings),
):
    """Fetch or generate the daily briefing.

    If a briefing for this date exists, return it.
    Otherwise, trigger generation from recent events.
    """
    try:
        parsed_date = date.fromisoformat(briefing_date)
    except ValueError:
        parsed_date = date.today()

    presenter = Presenter(settings=settings, db=db)
    briefing = await presenter.generate_briefing(user_id, parsed_date, workspace_id=workspace_id)

    return BriefingResponse(
        briefing_id=briefing.briefing_id,
        date=briefing.briefing_date,
        headline=briefing.headline,
        top_priorities=briefing.top_priorities or [],
        changes_since_last=briefing.changes_since_last or [],
        pending_approvals=briefing.pending_approvals or [],
        recommended_actions=briefing.recommended_actions or [],
        full_text=briefing.full_text,
    )


# ── Read-model endpoints ──────────────────────────────────────────


@router.get("/v1/briefings")
async def list_briefings(
    limit: int = Query(20, ge=1, le=100),
    offset: int = Query(0, ge=0),
    status: str | None = None,
    workspace_id: str = Depends(get_current_workspace_id),
    db: AsyncSession = Depends(get_session),
):
    """List briefings with pagination and optional status filter."""
    from src.services.briefing_read_model import BriefingReadModel

    model = BriefingReadModel(db, workspace_id)
    return await model.list_briefings(limit=limit, offset=offset, status=status)


@router.get("/v1/briefings/detail/{briefing_id}")
async def get_briefing_detail(
    briefing_id: str,
    workspace_id: str = Depends(get_current_workspace_id),
    db: AsyncSession = Depends(get_session),
):
    """Get full briefing detail with evidence and related items."""
    from src.services.briefing_read_model import BriefingReadModel

    model = BriefingReadModel(db, workspace_id)
    detail = await model.get_detail(briefing_id)
    if not detail:
        raise HTTPException(status_code=404, detail="Briefing not found")
    return detail


@router.post("/v1/briefings/{briefing_id}/pin")
async def pin_briefing(
    briefing_id: str,
    workspace_id: str = Depends(get_current_workspace_id),
    db: AsyncSession = Depends(get_session),
):
    """Pin a briefing for easy access."""
    from src.services.briefing_read_model import BriefingReadModel

    model = BriefingReadModel(db, workspace_id)
    success = await model.pin_briefing(briefing_id)
    await db.commit()
    if not success:
        raise HTTPException(status_code=404, detail="Briefing not found")
    return {"status": "pinned"}


@router.post("/v1/briefings/{briefing_id}/snooze")
async def snooze_briefing(
    briefing_id: str,
    workspace_id: str = Depends(get_current_workspace_id),
    db: AsyncSession = Depends(get_session),
):
    """Snooze a briefing (hide temporarily)."""
    from src.services.briefing_read_model import BriefingReadModel

    model = BriefingReadModel(db, workspace_id)
    success = await model.snooze_briefing(briefing_id)
    await db.commit()
    if not success:
        raise HTTPException(status_code=404, detail="Briefing not found")
    return {"status": "snoozed"}


@router.post("/v1/briefings/{briefing_id}/archive")
async def archive_briefing(
    briefing_id: str,
    workspace_id: str = Depends(get_current_workspace_id),
    db: AsyncSession = Depends(get_session),
):
    """Archive a briefing."""
    from src.services.briefing_read_model import BriefingReadModel

    model = BriefingReadModel(db, workspace_id)
    success = await model.archive_briefing(briefing_id)
    await db.commit()
    if not success:
        raise HTTPException(status_code=404, detail="Briefing not found")
    return {"status": "archived"}
