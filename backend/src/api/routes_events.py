"""Event ingestion and listing endpoints."""

import logging
from datetime import datetime, timedelta, timezone

from fastapi import APIRouter, Depends, Query, Request
from pydantic import BaseModel
from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from src.api.deps import get_current_user_id, get_current_workspace_id, get_session
from src.api.schemas import EventIngestRequest, EventIngestResponse
from src.config.settings import Settings, get_settings
from src.models.events import NormalizedEvent
from src.services.event_processor import EventProcessor, RawEvent
from src.services.memory_service import MemoryService
from src.services.world_model import WorldModel

logger = logging.getLogger(__name__)

router = APIRouter()


class NormalizedEventSummary(BaseModel):
    event_id: str
    source: str
    event_type: str
    title: str | None = None
    summary: str | None = None
    occurred_at: str | None = None
    status: str = "pending"

    model_config = {"from_attributes": True}


@router.get("/v1/events", response_model=list[NormalizedEventSummary])
async def list_events(
    time_range_hours: int = Query(24, ge=1, le=168),
    source: str | None = Query(None),
    limit: int = Query(20, ge=1, le=100),
    user_id: str = Depends(get_current_user_id),
    workspace_id: str = Depends(get_current_workspace_id),
    db: AsyncSession = Depends(get_session),
):
    """List recent normalized events for the current workspace."""
    cutoff = datetime.now(timezone.utc) - timedelta(hours=time_range_hours)
    stmt = select(NormalizedEvent).where(
        NormalizedEvent.workspace_id == workspace_id,
        NormalizedEvent.occurred_at > cutoff,
    )
    if source:
        stmt = stmt.where(NormalizedEvent.source == source)
    stmt = stmt.order_by(NormalizedEvent.occurred_at.desc()).limit(limit)

    result = await db.execute(stmt)
    rows = result.scalars().all()
    return [
        NormalizedEventSummary(
            event_id=e.event_id,
            source=e.source,
            event_type=e.event_type,
            title=e.title,
            summary=e.summary,
            occurred_at=e.occurred_at.isoformat() if e.occurred_at else None,
            status=e.status,
        )
        for e in rows
    ]


async def _make_event_processor(settings: Settings, db: AsyncSession, redis=None) -> EventProcessor:
    """Build an EventProcessor wired to the event bus.

    Downstream processing (entity extraction, memory extraction, planning,
    event indexing) is handled by the StreamConsumerManager worker via Redis
    consumer groups — not inline callbacks. This avoids workspace_id issues
    and ensures all stores (ES, Qdrant, Neo4j) get updated.
    """
    from src.services.event_bus import EventBus

    event_bus = None
    if redis:
        event_bus = EventBus(redis)
    else:
        try:
            import redis.asyncio as aioredis

            r = aioredis.from_url(settings.redis_url, decode_responses=True)
            event_bus = EventBus(r)
        except Exception:
            logger.warning("Could not create EventBus for event processor")

    world_model = WorldModel(settings=settings, db=db)
    memory_service = MemoryService(settings=settings, db=db)

    return EventProcessor(
        settings=settings,
        db=db,
        event_bus=event_bus,
        world_model=world_model,
        memory_service=memory_service,
    )


@router.post("/v1/events/ingest", response_model=EventIngestResponse)
async def ingest_event(
    req: EventIngestRequest,
    request: Request,
    user_id: str = Depends(get_current_user_id),
    workspace_id: str = Depends(get_current_workspace_id),
    db: AsyncSession = Depends(get_session),
    settings: Settings = Depends(get_settings),
):
    """Ingest a generic event from any source.

    Called by the observer agent or external integrations after reading data
    from Gmail, Calendar, GitHub, Slack, or any other source. The backend
    normalizes, scores, deduplicates, and triggers downstream processing.
    """
    raw = RawEvent(
        source=req.source,
        source_account_id=f"{req.source}_default",
        event_type=req.event_type,
        entity_type=req.entity_type,
        entity_id=req.entity_id,
        title=req.title,
        summary=req.summary,
        actor=req.actor,
        occurred_at=req.occurred_at,
        raw_payload=req.raw_payload,
    )

    redis = getattr(request.app.state, "redis", None)
    processor = await _make_event_processor(settings, db, redis=redis)
    event_id = await processor.process(raw, user_id, workspace_id=workspace_id)

    if event_id is None:
        return EventIngestResponse(event_id=None, status="duplicate", importance_score=None)

    return EventIngestResponse(
        event_id=event_id,
        status="processed",
        importance_score=None,
    )
