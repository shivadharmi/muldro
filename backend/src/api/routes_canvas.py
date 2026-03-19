"""Canvas endpoints — structured data for UI rendering."""

from datetime import date, datetime, timedelta, timezone

from fastapi import APIRouter, Depends
from sqlalchemy import func, select
from sqlalchemy.ext.asyncio import AsyncSession

from src.api.deps import get_current_user_id, get_current_workspace_id, get_session
from src.api.schemas import (
    DashboardApproval,
    DashboardEvent,
    DashboardGoal,
    DashboardMeeting,
    DashboardResponse,
    DashboardTask,
    DashboardTrace,
)
from src.config.settings import Settings, get_settings
from src.models.approvals import Approval
from src.models.briefings import Briefing
from src.models.events import NormalizedEvent
from src.models.goals import Goal
from src.models.plans import Plan, PlanTask
from src.models.tasks import Task
from src.models.traces import Trace

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

    # Recent traces (last 5 completed today)
    today_start = datetime.combine(today, datetime.min.time(), tzinfo=timezone.utc)
    traces_result = await db.execute(
        select(Trace)
        .where(
            Trace.workspace_id == workspace_id,
            Trace.status == "completed",
            Trace.started_at >= today_start,
        )
        .order_by(Trace.started_at.desc())
        .limit(5)
    )
    traces = traces_result.scalars().all()
    dashboard_traces = [
        DashboardTrace(
            trace_id=t.trace_id,
            trigger=t.trigger,
            agents_invoked=t.agents_invoked or [],
            duration_ms=t.duration_ms,
            total_cost_usd=t.total_cost_usd,
        )
        for t in traces
    ]

    # Active goals (top 3)
    goals_result = await db.execute(
        select(Goal)
        .where(Goal.workspace_id == workspace_id, Goal.status == "active")
        .order_by(Goal.priority.desc(), Goal.created_at.desc())
        .limit(3)
    )
    goals = goals_result.scalars().all()
    dashboard_goals = []
    for g in goals:
        task_count = (
            await db.scalar(select(func.count()).select_from(Task).where(Task.goal_id == g.goal_id))
            or 0
        )
        completed_task_count = (
            await db.scalar(
                select(func.count())
                .select_from(Task)
                .where(Task.goal_id == g.goal_id, Task.status == "completed")
            )
            or 0
        )
        dashboard_goals.append(
            DashboardGoal(
                goal_id=g.goal_id,
                title=g.title,
                progress=g.progress,
                priority=g.priority,
                task_count=task_count,
                completed_task_count=completed_task_count,
            )
        )

    # Recent events (last 8)
    events_result = await db.execute(
        select(NormalizedEvent)
        .where(NormalizedEvent.workspace_id == workspace_id)
        .order_by(NormalizedEvent.occurred_at.desc())
        .limit(8)
    )
    events = events_result.scalars().all()
    dashboard_events = [
        DashboardEvent(
            source=e.source,
            event_type=e.event_type,
            title=e.title,
            occurred_at=e.occurred_at,
        )
        for e in events
    ]

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
        recent_traces=dashboard_traces,
        active_goals=dashboard_goals,
        recent_events=dashboard_events,
    )
