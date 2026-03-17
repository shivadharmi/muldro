"""Event ingestion endpoint — generic entry point for all event sources."""

import logging

from fastapi import APIRouter, Depends
from sqlalchemy.ext.asyncio import AsyncSession

from src.api.deps import get_current_user_id, get_current_workspace_id, get_session
from src.api.schemas import EventIngestRequest, EventIngestResponse
from src.config.settings import Settings, get_settings
from src.services.event_processor import EventProcessor, RawEvent
from src.services.memory_service import MemoryService
from src.services.planner import Planner
from src.services.world_model import WorldModel

logger = logging.getLogger(__name__)

router = APIRouter()


def _make_event_processor(settings: Settings, db: AsyncSession) -> EventProcessor:
    """Build an EventProcessor with the full callback pipeline.

    Callbacks (in order):
    1. Entity extraction (WorldModel)
    2. Memory extraction (MemoryService)
    3. Proactive planning (Planner — for high-importance events)
    """
    world_model = WorldModel(settings=settings, db=db)
    memory_service = MemoryService(settings=settings, db=db)
    planner = Planner(
        settings=settings,
        db=db,
        world_model=world_model,
        memory_service=memory_service,
    )

    async def _extract_entities(event_id: str, user_id: str) -> None:
        await world_model.extract_from_event(event_id, user_id)

    async def _extract_memories(event_id: str, user_id: str) -> None:
        from sqlalchemy import select as sa_select

        from src.models.events import NormalizedEvent

        result = await db.execute(
            sa_select(NormalizedEvent).where(NormalizedEvent.event_id == event_id)
        )
        event = result.scalar_one_or_none()
        if event and event.summary:
            await memory_service.extract_and_store(user_id, event.summary, [event_id])

    async def _proactive_plan(event_id: str, user_id: str) -> None:
        """Auto-trigger planning for high-importance events."""
        plan = await planner.plan_for_event(event_id, user_id)
        if plan:
            logger.info(
                "Proactive plan created: %s decision=%s for event %s",
                plan.plan_id,
                plan.decision,
                event_id,
            )

    return EventProcessor(
        settings=settings,
        db=db,
        on_event_processed=[_extract_entities, _extract_memories, _proactive_plan],
        world_model=world_model,
        memory_service=memory_service,
    )


@router.post("/v1/events/ingest", response_model=EventIngestResponse)
async def ingest_event(
    req: EventIngestRequest,
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

    processor = _make_event_processor(settings, db)
    event_id = await processor.process(raw, user_id, workspace_id=workspace_id)

    if event_id is None:
        return EventIngestResponse(event_id=None, status="duplicate", importance_score=None)

    return EventIngestResponse(
        event_id=event_id,
        status="processed",
        importance_score=None,
    )
