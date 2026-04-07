"""Background workers for Jarvis.

StreamConsumerManager: Processes event bus streams via consumer groups.
"""

import asyncio
import logging
import os
import socket

from src.api.deps import resolve_workspace_id
from src.config.settings import Settings

logger = logging.getLogger(__name__)


def _get_consumer_name() -> str:
    """Generate unique consumer name from hostname + PID."""
    return f"worker-{socket.gethostname()}-{os.getpid()}"


NOTIFICATIONS_STREAM = "jarvis:notifications"


class StreamConsumerManager:
    """Manages event bus consumer groups for downstream processing.

    Subscribes to per-user event streams and dispatches to handlers:
    - entity_extractor: Extract entities from processed events
    - memory_extractor: Extract memories from event summaries
    - trigger_evaluator: Evaluate user-defined triggers

    Each consumer group runs in its own asyncio task for parallel
    processing — slow handlers (e.g., entity_extractor with Neo4j sync)
    don't block other groups.
    """

    CONSUMER_GROUPS = (
        "entity_extractor",
        "memory_extractor",
        "trigger_evaluator",
    )
    HANDLER_CONCURRENCY = 3  # max concurrent handler invocations per group

    def __init__(self, settings: Settings):
        self._settings = settings
        self._running = False
        self._vector_store = None
        self._tasks: list[asyncio.Task] = []

    async def _init_search(self) -> None:
        """Initialize shared VectorStore once at startup."""
        if self._settings.qdrant_url:
            from src.services.vector_store import VectorStore

            self._vector_store = VectorStore(self._settings)
            await self._vector_store.ensure_collections()

    async def run(self, user_ids: list[str]) -> None:
        """Main loop: consume from event bus streams.

        Launches one asyncio task per (user, consumer_group) pair so that
        slow handlers (e.g., entity extraction with Neo4j sync) don't
        block other consumer groups.
        """
        import redis.asyncio as aioredis

        from src.services.event_bus import EventBus

        if not user_ids:
            raise ValueError("user_ids must be provided — no default user")

        self._running = True
        r = aioredis.from_url(self._settings.redis_url, decode_responses=True)
        bus = EventBus(r)

        # Initialize shared Qdrant client once
        await self._init_search()

        # Build handler map
        handler_map = {
            "entity_extractor": self._handle_entity_extraction,
            "memory_extractor": self._handle_memory_extraction,
            "trigger_evaluator": self._handle_trigger_evaluation,
        }

        # Create consumer groups and launch parallel tasks
        for uid in user_ids:
            stream = bus.event_stream(uid)
            for group in self.CONSUMER_GROUPS:
                await bus.create_consumer_group(stream, group)
                task = asyncio.create_task(
                    self._consumer_loop(bus, stream, group, handler_map[group]),
                    name=f"consumer-{uid}-{group}",
                )
                self._tasks.append(task)

        logger.info(
            "StreamConsumerManager started: %d user(s), %d parallel tasks",
            len(user_ids),
            len(self._tasks),
        )

        try:
            await asyncio.gather(*self._tasks, return_exceptions=True)
        except asyncio.CancelledError:
            pass

        await r.aclose()
        logger.info("StreamConsumerManager stopped")

    async def _consumer_loop(self, bus, stream: str, group: str, handler) -> None:
        """Independent loop for one consumer group with concurrency semaphore."""
        sem = asyncio.Semaphore(self.HANDLER_CONCURRENCY)
        while self._running:
            try:
                async with sem:
                    await bus.subscribe(
                        stream,
                        group,
                        _get_consumer_name(),
                        handler,
                        count=10,
                        block_ms=2000,
                    )
            except Exception:
                logger.warning("Consumer %s error on %s", group, stream, exc_info=True)
                await asyncio.sleep(1)

    async def _handle_with_retry(
        self,
        handler,
        event,
        redis,
        dlq,
        bus,
        stream: str,
        group: str,
    ) -> None:
        """Wrap handler with retry tracking. After 3 failures -> DLQ."""
        event_id = event.payload.get("event_id", "unknown")
        retry_key = f"jarvis:worker:retry:{event_id}"

        try:
            await handler(event)
            # Success — clear retry counter
            if redis:
                await redis.delete(retry_key)
        except Exception as exc:
            attempts = 0
            if redis:
                attempts = await redis.incr(retry_key)
                await redis.expire(retry_key, 3600)
            else:
                attempts = 1

            if attempts > 3 and dlq:
                logger.error(
                    "Event %s exhausted %d retries, moving to DLQ",
                    event_id,
                    attempts,
                )
                await dlq.enqueue(
                    user_id=event.user_id,
                    operation_type=f"worker_{group}",
                    error_type=type(exc).__name__,
                    error_message=str(exc)[:500],
                    payload={"event_id": event_id, "group": group},
                )
                if bus:
                    await bus.ack(stream, group, event.message_id)
            else:
                logger.warning(
                    "Handler failed for %s (attempt %d): %s",
                    event_id,
                    attempts,
                    exc,
                )

    async def stop(self) -> None:
        self._running = False
        for task in self._tasks:
            task.cancel()
        self._tasks.clear()

    async def _handle_entity_extraction(self, event) -> None:
        """Extract entities from an event, then sync to Neo4j and Qdrant."""
        event_id = event.payload.get("event_id", "")
        user_id = event.user_id
        if not event_id:
            return

        from src.models.database import get_session_factory
        from src.services.world_model import WorldModel

        factory = get_session_factory()
        async with factory() as db:
            workspace_id = await resolve_workspace_id(db, user_id)
            world_model = WorldModel(
                settings=self._settings,
                db=db,
                vector_store=self._vector_store,
            )
            entity_ids = await world_model.extract_from_event(
                event_id, user_id, workspace_id=workspace_id
            )
            await db.commit()
            logger.info(
                "Entity extraction for event %s: %d entities",
                event_id,
                len(entity_ids),
            )

            # Sync extracted entities to Neo4j (batch)
            if entity_ids and self._settings.neo4j_url:
                try:
                    from src.services.graph_sync import GraphSyncService

                    graph_sync = GraphSyncService(self._settings, db)
                    result = await graph_sync.batch_sync_entities(entity_ids)
                    await graph_sync.close()
                    logger.info(
                        "Neo4j batch sync for event %s: %s",
                        event_id,
                        result,
                    )
                except Exception:
                    logger.warning(
                        "Neo4j sync failed for event %s",
                        event_id,
                        exc_info=True,
                    )

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
            memory_service = MemoryService(
                settings=self._settings,
                db=db,
                vector_store=self._vector_store,
            )
            memory_ids = await memory_service.extract_and_store(
                user_id=user_id,
                source_text=source_text,
                source_event_ids=[event_id],
                entity_ids=entity_ids,
                workspace_id=workspace_id,
            )
            await db.commit()
            logger.info(
                "Memory extraction for event %s: %d memories",
                event_id,
                len(memory_ids),
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
