"""Schedule CRUD endpoints — backend-owned dynamic scheduling."""

from datetime import datetime, timezone

from croniter import croniter
from fastapi import APIRouter, Depends, HTTPException
from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession
from ulid import ULID

from src.api.deps import get_current_user, get_session
from src.api.schemas import ScheduleCreateRequest, ScheduleResponse, ScheduleUpdateRequest
from src.models.schedules import Schedule

router = APIRouter()

VALID_ACTION_TYPES = {
    "observe_source",
    "generate_briefing",
    "meeting_prep",
    "heartbeat",
    "custom_agent_task",
    "wake_agent",
}

VALID_SCHEDULE_TYPES = {"recurring", "one_shot"}
VALID_PRIORITIES = {"low", "medium", "high"}
VALID_SOURCES = {"system", "user", "reflection"}


def _compute_next_run(cron_expr: str, after: datetime) -> datetime:
    return croniter(cron_expr, after).get_next(datetime)


def _to_response(sched: Schedule) -> ScheduleResponse:
    return ScheduleResponse(
        schedule_id=sched.schedule_id,
        user_id=sched.user_id,
        name=sched.name,
        description=sched.description,
        schedule_type=sched.schedule_type,
        cron_expr=sched.cron_expr,
        run_at=sched.run_at,
        action_type=sched.action_type,
        action_config=sched.action_config,
        enabled=sched.enabled,
        last_run_at=sched.last_run_at,
        next_run_at=sched.next_run_at,
        run_count=sched.run_count,
        consecutive_failures=sched.consecutive_failures,
        last_error=sched.last_error,
        source=sched.source,
        priority=sched.priority,
        created_at=sched.created_at,
        updated_at=sched.updated_at,
    )


@router.post("/v1/schedules", response_model=ScheduleResponse, status_code=201)
async def create_schedule(
    req: ScheduleCreateRequest,
    user_id: str = Depends(get_current_user),
    db: AsyncSession = Depends(get_session),
):
    """Create a new schedule."""
    if req.schedule_type not in VALID_SCHEDULE_TYPES:
        raise HTTPException(400, f"Invalid schedule_type: {req.schedule_type}")
    if req.action_type not in VALID_ACTION_TYPES:
        raise HTTPException(400, f"Invalid action_type: {req.action_type}")
    if req.priority not in VALID_PRIORITIES:
        raise HTTPException(400, f"Invalid priority: {req.priority}")
    if req.source not in VALID_SOURCES:
        raise HTTPException(400, f"Invalid source: {req.source}")

    now = datetime.now(timezone.utc)
    next_run_at = None

    if req.schedule_type == "recurring":
        if not req.cron_expr:
            raise HTTPException(400, "cron_expr required for recurring schedules")
        try:
            next_run_at = _compute_next_run(req.cron_expr, now)
        except (ValueError, KeyError) as e:
            raise HTTPException(400, f"Invalid cron_expr: {e}") from e
    elif req.schedule_type == "one_shot":
        if not req.run_at:
            raise HTTPException(400, "run_at required for one_shot schedules")
        next_run_at = req.run_at

    sched = Schedule(
        schedule_id=f"sched_{ULID()}",
        user_id=user_id,
        name=req.name,
        description=req.description,
        schedule_type=req.schedule_type,
        cron_expr=req.cron_expr,
        run_at=req.run_at,
        action_type=req.action_type,
        action_config=req.action_config,
        enabled=req.enabled,
        next_run_at=next_run_at,
        run_count=0,
        consecutive_failures=0,
        source=req.source,
        priority=req.priority,
    )
    db.add(sched)
    await db.commit()
    await db.refresh(sched)
    return _to_response(sched)


@router.get("/v1/schedules", response_model=list[ScheduleResponse])
async def list_schedules(
    enabled: bool | None = None,
    action_type: str | None = None,
    source: str | None = None,
    user_id: str = Depends(get_current_user),
    db: AsyncSession = Depends(get_session),
):
    """List schedules with optional filters."""
    query = select(Schedule).where(Schedule.user_id == user_id)
    if enabled is not None:
        query = query.where(Schedule.enabled.is_(enabled))
    if action_type:
        query = query.where(Schedule.action_type == action_type)
    if source:
        query = query.where(Schedule.source == source)
    query = query.order_by(Schedule.created_at)

    result = await db.execute(query)
    return [_to_response(s) for s in result.scalars().all()]


@router.get("/v1/schedules/{schedule_id}", response_model=ScheduleResponse)
async def get_schedule(
    schedule_id: str,
    user_id: str = Depends(get_current_user),
    db: AsyncSession = Depends(get_session),
):
    """Get a single schedule."""
    result = await db.execute(
        select(Schedule).where(Schedule.schedule_id == schedule_id, Schedule.user_id == user_id)
    )
    sched = result.scalar_one_or_none()
    if not sched:
        raise HTTPException(404, "Schedule not found")
    return _to_response(sched)


@router.patch("/v1/schedules/{schedule_id}", response_model=ScheduleResponse)
async def update_schedule(
    schedule_id: str,
    req: ScheduleUpdateRequest,
    user_id: str = Depends(get_current_user),
    db: AsyncSession = Depends(get_session),
):
    """Update schedule fields. Recomputes next_run_at if cron_expr changes."""
    result = await db.execute(
        select(Schedule).where(Schedule.schedule_id == schedule_id, Schedule.user_id == user_id)
    )
    sched = result.scalar_one_or_none()
    if not sched:
        raise HTTPException(404, "Schedule not found")

    if req.name is not None:
        sched.name = req.name
    if req.description is not None:
        sched.description = req.description
    if req.action_type is not None:
        if req.action_type not in VALID_ACTION_TYPES:
            raise HTTPException(400, f"Invalid action_type: {req.action_type}")
        sched.action_type = req.action_type
    if req.action_config is not None:
        sched.action_config = req.action_config
    if req.enabled is not None:
        sched.enabled = req.enabled
    if req.priority is not None:
        if req.priority not in VALID_PRIORITIES:
            raise HTTPException(400, f"Invalid priority: {req.priority}")
        sched.priority = req.priority

    # Recompute next_run_at if cron_expr changed
    if req.cron_expr is not None:
        try:
            now = datetime.now(timezone.utc)
            sched.cron_expr = req.cron_expr
            sched.next_run_at = _compute_next_run(req.cron_expr, now)
        except (ValueError, KeyError) as e:
            raise HTTPException(400, f"Invalid cron_expr: {e}") from e

    if req.run_at is not None:
        sched.run_at = req.run_at
        if sched.schedule_type == "one_shot":
            sched.next_run_at = req.run_at

    await db.commit()
    await db.refresh(sched)
    return _to_response(sched)


@router.delete("/v1/schedules/{schedule_id}", status_code=204)
async def delete_schedule(
    schedule_id: str,
    user_id: str = Depends(get_current_user),
    db: AsyncSession = Depends(get_session),
):
    """Delete a schedule."""
    result = await db.execute(
        select(Schedule).where(Schedule.schedule_id == schedule_id, Schedule.user_id == user_id)
    )
    sched = result.scalar_one_or_none()
    if not sched:
        raise HTTPException(404, "Schedule not found")
    await db.delete(sched)
    await db.commit()


@router.post("/v1/schedules/{schedule_id}/pause", response_model=ScheduleResponse)
async def pause_schedule(
    schedule_id: str,
    user_id: str = Depends(get_current_user),
    db: AsyncSession = Depends(get_session),
):
    """Pause a schedule (set enabled=False)."""
    result = await db.execute(
        select(Schedule).where(Schedule.schedule_id == schedule_id, Schedule.user_id == user_id)
    )
    sched = result.scalar_one_or_none()
    if not sched:
        raise HTTPException(404, "Schedule not found")
    sched.enabled = False
    await db.commit()
    await db.refresh(sched)
    return _to_response(sched)


@router.post("/v1/schedules/{schedule_id}/resume", response_model=ScheduleResponse)
async def resume_schedule(
    schedule_id: str,
    user_id: str = Depends(get_current_user),
    db: AsyncSession = Depends(get_session),
):
    """Resume a schedule (set enabled=True, recompute next_run_at from now)."""
    result = await db.execute(
        select(Schedule).where(Schedule.schedule_id == schedule_id, Schedule.user_id == user_id)
    )
    sched = result.scalar_one_or_none()
    if not sched:
        raise HTTPException(404, "Schedule not found")

    sched.enabled = True
    now = datetime.now(timezone.utc)
    if sched.schedule_type == "recurring" and sched.cron_expr:
        sched.next_run_at = _compute_next_run(sched.cron_expr, now)
    elif sched.schedule_type == "one_shot" and sched.run_at:
        sched.next_run_at = sched.run_at
    sched.consecutive_failures = 0
    sched.last_error = None

    await db.commit()
    await db.refresh(sched)
    return _to_response(sched)
