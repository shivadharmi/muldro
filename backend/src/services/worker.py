"""Background worker that processes the jarvis:callbacks stream.

Handles async callbacks that were previously run inline during event ingestion:
- entity_extraction: Extract entities from events into the world model
- memory_extraction: Extract memories from event summaries
- proactive_planning: Auto-plan for high-importance events
"""

import asyncio
import logging

from src.config.settings import Settings

logger = logging.getLogger(__name__)

CALLBACKS_STREAM = "jarvis:callbacks"
NOTIFICATIONS_STREAM = "jarvis:notifications"


class CallbackWorker:
    """Background worker that processes async callback tasks."""

    def __init__(self, settings: Settings):
        self._settings = settings
        self._running = False

    async def run(self) -> None:
        """Main loop: read from stream, dispatch to handlers."""
        import redis.asyncio as aioredis

        self._running = True
        r = aioredis.from_url(self._settings.redis_url, decode_responses=True)

        from src.services.task_queue import TaskQueue

        queue = TaskQueue(r)
        await queue.ensure_group(CALLBACKS_STREAM)

        logger.info("CallbackWorker started, listening on %s", CALLBACKS_STREAM)

        while self._running:
            try:
                processed = await queue.process_stream(
                    CALLBACKS_STREAM,
                    self._handle_callback,
                    count=10,
                    block_ms=5000,
                )
                if processed > 0:
                    logger.info("Processed %d callbacks", processed)
            except Exception:
                logger.warning("Worker loop error", exc_info=True)
                await asyncio.sleep(1)

        await r.aclose()
        logger.info("CallbackWorker stopped")

    async def stop(self) -> None:
        """Signal the worker to stop."""
        self._running = False

    async def _handle_callback(self, task_type: str, payload: dict) -> None:
        """Dispatch a callback task to the appropriate handler."""
        event_id = payload.get("event_id", "")
        user_id = payload.get("user_id", "")

        logger.info("Processing callback: %s for event %s", task_type, event_id)

        if task_type == "entity_extraction":
            await self._handle_entity_extraction(event_id, user_id)
        elif task_type == "memory_extraction":
            await self._handle_memory_extraction(event_id, user_id)
        elif task_type == "proactive_planning":
            await self._handle_proactive_planning(event_id, user_id)
        else:
            logger.warning("Unknown callback type: %s", task_type)

    async def _handle_entity_extraction(self, event_id: str, user_id: str) -> None:
        """Extract entities from an event into the world model."""
        from src.models.database import get_session_factory
        from src.services.world_model import WorldModel

        factory = get_session_factory()
        async with factory() as db:
            world_model = WorldModel(settings=self._settings, db=db)
            entity_ids = await world_model.extract_from_event(event_id, user_id)
            await db.commit()
            logger.info("Entity extraction for event %s: %d entities", event_id, len(entity_ids))

    async def _handle_memory_extraction(self, event_id: str, user_id: str) -> None:
        """Extract memories from an event summary."""
        from sqlalchemy import select

        from src.models.database import get_session_factory
        from src.models.events import NormalizedEvent
        from src.services.memory_service import MemoryService

        factory = get_session_factory()
        async with factory() as db:
            result = await db.execute(
                select(NormalizedEvent).where(NormalizedEvent.event_id == event_id)
            )
            event = result.scalar_one_or_none()
            if not event:
                logger.warning("Event not found for memory extraction: %s", event_id)
                return

            source_text = f"Title: {event.title or ''}\nSummary: {event.summary or ''}"
            memory_service = MemoryService(settings=self._settings, db=db)
            memory_ids = await memory_service.extract_and_store(
                user_id=user_id,
                source_text=source_text,
                source_event_ids=[event_id],
            )
            await db.commit()
            logger.info("Memory extraction for event %s: %d memories", event_id, len(memory_ids))

    async def _handle_proactive_planning(self, event_id: str, user_id: str) -> None:
        """Auto-trigger planning for high-importance events."""
        from src.models.database import get_session_factory
        from src.services.planner import Planner

        factory = get_session_factory()
        async with factory() as db:
            planner = Planner(settings=self._settings, db=db)
            plan = await planner.plan_for_event(event_id, user_id)
            await db.commit()
            if plan:
                logger.info(
                    "Proactive plan created for event %s: %s (decision=%s)",
                    event_id,
                    plan.plan_id,
                    plan.decision,
                )
            else:
                logger.info("No plan needed for event %s", event_id)
