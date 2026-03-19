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
    - event_indexer: Index events to ES + Qdrant
    """

    def __init__(self, settings: Settings):
        self._settings = settings
        self._running = False
        self._search_service = None
        self._vector_store = None

    async def _init_search(self) -> None:
        """Initialize shared SearchService and VectorStore once at startup."""
        if self._settings.qdrant_url:
            from src.services.vector_store import VectorStore

            self._vector_store = VectorStore(self._settings)
            await self._vector_store.ensure_collections()

        if self._settings.elasticsearch_url or self._vector_store:
            from src.services.search_service import SearchService

            self._search_service = SearchService(self._settings, vector_store=self._vector_store)
            await self._search_service.ensure_indices()

    async def run(self, user_ids: list[str]) -> None:
        """Main loop: consume from event bus streams."""
        import redis.asyncio as aioredis

        from src.services.event_bus import EventBus

        if not user_ids:
            raise ValueError("user_ids must be provided — no default user")

        self._running = True
        r = aioredis.from_url(self._settings.redis_url, decode_responses=True)
        bus = EventBus(r)

        # Initialize shared ES + Qdrant clients once
        await self._init_search()

        # Create consumer groups for each user stream
        for uid in user_ids:
            stream = bus.event_stream(uid)
            for group in (
                "entity_extractor",
                "memory_extractor",
                "planner",
                "trigger_evaluator",
                "event_indexer",
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
                    await bus.subscribe(
                        stream,
                        "event_indexer",
                        "worker-1",
                        self._handle_event_indexing,
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
        """Extract entities from an event, then sync to Neo4j and ES+Qdrant."""
        event_id = event.payload.get("event_id", "")
        user_id = event.user_id
        if not event_id:
            return

        from sqlalchemy import select

        from src.models.database import get_session_factory
        from src.models.entities import Entity
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

            # Sync extracted entities to Neo4j
            if entity_ids and self._settings.neo4j_url:
                try:
                    from src.services.graph_sync import GraphSyncService

                    graph_sync = GraphSyncService(self._settings, db)
                    for eid in entity_ids:
                        await graph_sync.sync_entity_by_id(eid)
                        await graph_sync.sync_relationships_for_entity(eid)
                    await graph_sync.close()
                    logger.info(
                        "Neo4j sync for %d entities from event %s",
                        len(entity_ids),
                        event_id,
                    )
                except Exception:
                    logger.warning("Neo4j sync failed for event %s", event_id, exc_info=True)

            # Index entities to ES + Qdrant
            if entity_ids and self._search_service:
                try:
                    result = await db.execute(
                        select(Entity).where(Entity.entity_id.in_(entity_ids))
                    )
                    for ent in result.scalars().all():
                        await self._search_service.index_entity(
                            ent.entity_id,
                            user_id,
                            {
                                "entity_type": ent.entity_type,
                                "canonical_name": ent.canonical_name,
                                "attributes": ent.attributes or {},
                            },
                        )
                    logger.info(
                        "ES+Qdrant indexed %d entities from event %s",
                        len(entity_ids),
                        event_id,
                    )
                except Exception:
                    logger.warning(
                        "ES+Qdrant entity indexing failed for event %s",
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

            # Index memories to ES + Qdrant
            if memory_ids and self._search_service:
                try:
                    from src.models.memory import Memory

                    mem_result = await db.execute(
                        select(Memory).where(Memory.memory_id.in_(memory_ids))
                    )
                    for mem in mem_result.scalars().all():
                        await self._search_service.index_memory(
                            mem.memory_id,
                            user_id,
                            {
                                "memory_type": mem.memory_type,
                                "fact_text": mem.fact_text,
                                "confidence": mem.confidence,
                            },
                        )
                    logger.info(
                        "ES+Qdrant indexed %d memories from event %s",
                        len(memory_ids),
                        event_id,
                    )
                except Exception:
                    logger.warning(
                        "ES+Qdrant memory indexing failed for event %s",
                        event_id,
                        exc_info=True,
                    )

    async def _handle_event_indexing(self, event) -> None:
        """Index processed events to ES + Qdrant for hybrid search."""
        event_id = event.payload.get("event_id", "")
        user_id = event.user_id
        if not event_id:
            return

        if not self._search_service:
            return

        from sqlalchemy import select

        from src.models.database import get_session_factory
        from src.models.events import NormalizedEvent

        factory = get_session_factory()
        async with factory() as db:
            result = await db.execute(
                select(NormalizedEvent).where(NormalizedEvent.event_id == event_id)
            )
            ev = result.scalar_one_or_none()
            if not ev:
                return

            try:
                await self._search_service.index_event(
                    ev.event_id,
                    user_id,
                    {
                        "event_type": ev.event_type,
                        "source": ev.source,
                        "title": ev.title or "",
                        "summary": ev.summary or "",
                        "occurred_at": (ev.occurred_at.isoformat() if ev.occurred_at else None),
                    },
                )
                logger.info("ES+Qdrant indexed event %s", event_id)
            except Exception:
                logger.warning("Event indexing failed for %s", event_id, exc_info=True)

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
