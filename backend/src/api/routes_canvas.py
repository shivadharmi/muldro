"""Canvas endpoints — structured data for UI rendering."""

from datetime import date, datetime, timedelta, timezone

from fastapi import APIRouter, Depends
from sqlalchemy import func, select
from sqlalchemy.ext.asyncio import AsyncSession

from src.api.deps import get_current_user_id, get_current_workspace_id, get_session
from src.api.schemas import (
    DashboardApproval,
    DashboardMeeting,
    DashboardResponse,
    DashboardTask,
)
from src.config.settings import Settings, get_settings
from src.models.approvals import Approval
from src.models.briefings import Briefing
from src.models.events import NormalizedEvent
from src.models.plans import Plan, PlanTask

router = APIRouter()


@router.get("/v1/canvas/dashboard", response_model=DashboardResponse)
async def get_dashboard(
    user_id: str = Depends(get_current_user_id),
    workspace_id: str = Depends(get_current_workspace_id),
    db: AsyncSession = Depends(get_session),
    settings: Settings = Depends(get_settings),
):
    """Unified dashboard data for Canvas UI rendering."""
    today = date.today()

    # Get today's briefing headline if available
    briefing_result = await db.execute(
        select(Briefing).where(
            Briefing.user_id == user_id,
            Briefing.briefing_date == today,
        )
    )
    briefing = briefing_result.scalar_one_or_none()

    # Pending approvals
    approvals_result = await db.execute(
        select(Approval)
        .where(Approval.user_id == user_id, Approval.status == "pending")
        .order_by(Approval.created_at.desc())
        .limit(10)
    )
    approvals = approvals_result.scalars().all()

    # Active tasks with step progress
    plans_result = await db.execute(
        select(Plan)
        .where(
            Plan.user_id == user_id,
            Plan.status.in_(["created", "policy_checked", "executing"]),
        )
        .order_by(Plan.created_at.desc())
        .limit(10)
    )
    plans = plans_result.scalars().all()

    dashboard_tasks = []
    for plan in plans:
        step_count = await db.scalar(
            select(func.count()).select_from(PlanTask).where(PlanTask.plan_id == plan.plan_id)
        )
        steps_completed = await db.scalar(
            select(func.count())
            .select_from(PlanTask)
            .where(PlanTask.plan_id == plan.plan_id, PlanTask.status == "completed")
        )
        dashboard_tasks.append(
            DashboardTask(
                task_id=plan.plan_id,
                goal=plan.goal,
                priority=plan.priority,
                status=plan.status,
                decision=plan.decision,
                step_count=step_count or 0,
                steps_completed=steps_completed or 0,
                created_at=plan.created_at,
            )
        )

    # Upcoming meetings (next 36 hours)
    now = datetime.now(timezone.utc)
    meetings_result = await db.execute(
        select(NormalizedEvent)
        .where(
            NormalizedEvent.user_id == user_id,
            NormalizedEvent.workspace_id == workspace_id,
            NormalizedEvent.source == "calendar",
            NormalizedEvent.occurred_at >= now,
            NormalizedEvent.occurred_at <= now + timedelta(hours=36),
        )
        .order_by(NormalizedEvent.occurred_at.asc())
        .limit(10)
    )
    meetings = meetings_result.scalars().all()

    dashboard_meetings = []
    for m in meetings:
        attendee_count = len(m.actor_entities) if m.actor_entities else 0
        dashboard_meetings.append(
            DashboardMeeting(
                event_id=m.event_id,
                title=m.title or "Untitled Meeting",
                starts_at=m.occurred_at,
                attendee_count=attendee_count,
            )
        )

    return DashboardResponse(
        headline=briefing.headline if briefing else None,
        date=today,
        pending_approvals=[
            DashboardApproval(
                approval_id=a.approval_id,
                title=a.title,
                summary=a.summary,
                risk_level=a.risk_level,
                approval_type=a.approval_type,
                created_at=a.created_at,
            )
            for a in approvals
        ],
        active_tasks=dashboard_tasks,
        upcoming_meetings=dashboard_meetings,
        recommended_actions=(
            briefing.recommended_actions if briefing and briefing.recommended_actions else []
        ),
        briefing_id=briefing.briefing_id if briefing else None,
    )
