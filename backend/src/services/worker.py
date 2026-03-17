"""Background workers for Jarvis.

StreamConsumerManager: Processes event bus streams via consumer groups.
"""

import asyncio
import logging

from src.api.deps import resolve_workspace_id
from src.config.settings import Settings

logger = logging.getLogger(__name__)

NOTIFICATIONS_STREAM = "jarvis:notifications"


class StreamConsumerManager:
    """Manages event bus consumer groups for downstream processing.

    Subscribes to per-user event streams and dispatches to handlers:
    - entity_extractor: Extract entities from processed events
    - memory_extractor: Extract memories from event summaries
    - planner: Auto-plan for high-importance events
    """

    def __init__(self, settings: Settings):
        self._settings = settings
        self._running = False

    async def run(self, user_ids: list[str]) -> None:
        """Main loop: consume from event bus streams."""
        import redis.asyncio as aioredis

        from src.services.event_bus import EventBus

        if not user_ids:
            raise ValueError("user_ids must be provided — no default user")

        self._running = True
        r = aioredis.from_url(self._settings.redis_url, decode_responses=True)
        bus = EventBus(r)

        # Create consumer groups for each user stream
        for uid in user_ids:
            stream = bus.event_stream(uid)
            for group in (
                "entity_extractor",
                "memory_extractor",
                "planner",
                "trigger_evaluator",
            ):
                await bus.create_consumer_group(stream, group)

        logger.info("StreamConsumerManager started for %d user(s)", len(user_ids))

        while self._running:
            try:
                for uid in user_ids:
                    stream = bus.event_stream(uid)

                    await bus.subscribe(
                        stream,
                        "entity_extractor",
                        "worker-1",
                        self._handle_entity_extraction,
                        count=10,
                        block_ms=1000,
                    )
                    await bus.subscribe(
                        stream,
                        "memory_extractor",
                        "worker-1",
                        self._handle_memory_extraction,
                        count=10,
                        block_ms=1000,
                    )
                    await bus.subscribe(
                        stream,
                        "planner",
                        "worker-1",
                        self._handle_proactive_planning,
                        count=10,
                        block_ms=1000,
                    )
                    await bus.subscribe(
                        stream,
                        "trigger_evaluator",
                        "worker-1",
                        self._handle_trigger_evaluation,
                        count=10,
                        block_ms=1000,
                    )
            except Exception:
                logger.warning("StreamConsumer loop error", exc_info=True)
                await asyncio.sleep(1)

        await r.aclose()
        logger.info("StreamConsumerManager stopped")

    async def stop(self) -> None:
        self._running = False

    async def _handle_entity_extraction(self, event) -> None:
        """Extract entities from an event."""
        event_id = event.payload.get("event_id", "")
        user_id = event.user_id
        if not event_id:
            return

        from src.models.database import get_session_factory
        from src.services.world_model import WorldModel

        factory = get_session_factory()
        async with factory() as db:
            workspace_id = await resolve_workspace_id(db, user_id)
            world_model = WorldModel(settings=self._settings, db=db)
            entity_ids = await world_model.extract_from_event(
                event_id, user_id, workspace_id=workspace_id
            )
            await db.commit()
            logger.info("Entity extraction for event %s: %d entities", event_id, len(entity_ids))

    async def _handle_memory_extraction(self, event) -> None:
        """Extract memories from an event, linked to relevant entities."""
        event_id = event.payload.get("event_id", "")
        user_id = event.user_id
        if not event_id:
            return

        from sqlalchemy import select

        from src.models.database import get_session_factory
        from src.models.events import NormalizedEvent
        from src.services.memory_service import MemoryService
        from src.services.world_model import WorldModel

        factory = get_session_factory()
        async with factory() as db:
            workspace_id = await resolve_workspace_id(db, user_id)

            result = await db.execute(
                select(NormalizedEvent).where(NormalizedEvent.event_id == event_id)
            )
            ev = result.scalar_one_or_none()
            if not ev:
                return

            # Find entities related to this event for entity-memory linking
            entity_ids = None
            if ev.title or ev.summary:
                wm = WorldModel(settings=self._settings, db=db)
                query = ev.title or ev.summary or ""
                entities = await wm.find_entity(user_id, query[:100], workspace_id=workspace_id)
                if entities:
                    entity_ids = [e["entity_id"] for e in entities[:5]]

            source_text = f"Title: {ev.title or ''}\nSummary: {ev.summary or ''}"
            memory_service = MemoryService(settings=self._settings, db=db)
            memory_ids = await memory_service.extract_and_store(
                user_id=user_id,
                source_text=source_text,
                source_event_ids=[event_id],
                entity_ids=entity_ids,
                workspace_id=workspace_id,
            )
            await db.commit()
            logger.info("Memory extraction for event %s: %d memories", event_id, len(memory_ids))

    async def _handle_proactive_planning(self, event) -> None:
        """Auto-trigger planning for high-importance events."""
        event_id = event.payload.get("event_id", "")
        user_id = event.user_id
        importance = event.payload.get("importance_score", 0)

        if not event_id or importance < 0.7:
            return

        from src.models.database import get_session_factory
        from src.services.planner import Planner

        factory = get_session_factory()
        async with factory() as db:
            workspace_id = await resolve_workspace_id(db, user_id)
            planner = Planner(settings=self._settings, db=db)
            plan = await planner.plan_for_event(event_id, user_id, workspace_id=workspace_id)
            await db.commit()
            if plan:
                logger.info(
                    "Proactive plan for event %s: %s (decision=%s)",
                    event_id,
                    plan.plan_id,
                    plan.decision,
                )

    async def _handle_trigger_evaluation(self, event) -> None:
        """Evaluate event against user-defined triggers."""
        from src.models.database import get_session_factory
        from src.services.trigger_engine import TriggerEngine

        factory = get_session_factory()
        async with factory() as db:
            user_id = event.user_id
            workspace_id = await resolve_workspace_id(db, user_id)
            engine = TriggerEngine(db)
            fired = await engine.evaluate(event, workspace_id=workspace_id)
            await db.commit()
            if fired:
                logger.info("Triggers fired for event: %d", len(fired))
