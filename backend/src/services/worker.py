"""Background workers for Jarvis.

StreamConsumerManager: Processes event bus streams via consumer groups.
"""

import asyncio
import logging
import os
import socket

from src.config.settings import Settings
from src.models.database import get_session_factory
from src.services.workspace_resolver import resolve_workspace_id

logger = logging.getLogger(__name__)


def _get_consumer_name() -> str:
    """Generate unique consumer name from hostname + PID."""
    return f"worker-{socket.gethostname()}-{os.getpid()}"


NOTIFICATIONS_STREAM = "jarvis:notifications"

_EXTRACTION_TIERS = {"skip", "light", "full"}


def _event_tier(ev) -> str:
    """Extraction tier persisted on the event (``importance_signals.tier``,
    set by ``TriageResult.to_signals()``). Missing/garbled -> 'full' (recall-preserving:
    an unrecognized tier must never silently suppress extraction)."""
    signals = getattr(ev, "importance_signals", None) or {}
    tier = signals.get("tier")
    return tier if tier in _EXTRACTION_TIERS else "full"


def _event_category(ev) -> str:
    """Triage category persisted on the event (``importance_signals.category``)."""
    signals = getattr(ev, "importance_signals", None) or {}
    return signals.get("category") or ""


class StreamConsumerManager:
    """Manages event bus consumer groups for downstream processing.

    Subscribes to per-workspace event streams and dispatches to handlers:
    - entity_extractor: Extract entities from processed events (main stream)
    - memory_extractor: Extract memories from event summaries (main stream)
    - trigger_evaluator: Evaluate user-defined triggers (main stream)
    - contradiction_checker: Check memory contradictions on new memory events (main stream)
    - graph_syncer: Sync entity changes to Neo4j (agent events stream)

    Each consumer group runs in its own asyncio task for parallel
    processing — slow handlers don't block other groups.
    """

    MAIN_STREAM_GROUPS = (
        "entity_extractor",
        "memory_extractor",
        "trigger_evaluator",
        "contradiction_checker",
    )
    AGENT_STREAM_GROUPS = ("graph_syncer",)
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
            await self._vector_store.ensure_indexes()

    async def run(self, workspace_ids: list[str]) -> None:
        """Main loop: consume from event bus streams.

        Launches one asyncio task per (workspace, consumer_group) pair so that
        slow handlers (e.g., entity extraction with Neo4j sync) don't
        block other consumer groups.
        """
        import redis.asyncio as aioredis

        from src.services.event_bus import EventBus

        if not workspace_ids:
            raise ValueError("workspace_ids must be provided — no default workspace")

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
            "contradiction_checker": self._handle_contradiction_check,
            "graph_syncer": self._handle_graph_sync,
        }

        # Create consumer groups and launch parallel tasks
        for ws_id in workspace_ids:
            main_stream = bus.event_stream(ws_id)
            agent_stream = bus.agent_stream(ws_id)

            for group in self.MAIN_STREAM_GROUPS:
                await bus.create_consumer_group(main_stream, group)
                task = asyncio.create_task(
                    self._consumer_loop(bus, main_stream, group, handler_map[group]),
                    name=f"consumer-{ws_id}-{group}",
                )
                self._tasks.append(task)

            for group in self.AGENT_STREAM_GROUPS:
                await bus.create_consumer_group(agent_stream, group)
                task = asyncio.create_task(
                    self._consumer_loop(bus, agent_stream, group, handler_map[group]),
                    name=f"consumer-{ws_id}-{group}",
                )
                self._tasks.append(task)

        logger.info(
            "StreamConsumerManager started: %d workspace(s), %d parallel tasks",
            len(workspace_ids),
            len(self._tasks),
        )

        try:
            await asyncio.gather(*self._tasks, return_exceptions=True)
        except asyncio.CancelledError:
            pass

        await r.aclose()
        logger.info("StreamConsumerManager stopped")

    async def _consumer_loop(self, bus, stream: str, group: str, handler) -> None:
        """Independent loop for one consumer group with concurrency semaphore.

        Retry + dead-letter handling lives in ``EventBus.subscribe`` (XAUTOCLAIM
        reclaim of stale pending messages, then dead-letter after
        ``DLQ_MAX_DELIVERIES``). We supply ``on_dead_letter`` so exhausted
        messages are durably captured in the DLQ rather than logged and dropped.
        """
        sem = asyncio.Semaphore(self.HANDLER_CONCURRENCY)
        on_dead_letter = self._build_dead_letter_handler(group)
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
                        on_dead_letter=on_dead_letter,
                    )
            except Exception:
                logger.warning("Consumer %s error on %s", group, stream, exc_info=True)
                await asyncio.sleep(1)

    def _build_dead_letter_handler(self, group: str):
        """Return an ``on_dead_letter`` callback bound to this consumer group."""

        async def _on_dead_letter(ctx) -> None:
            await self._persist_dead_letter(group, ctx)

        return _on_dead_letter

    async def _persist_dead_letter(self, group: str, ctx) -> None:
        """Capture an exhausted message in the dead-letter queue.

        ``ctx.data`` is the raw stream entry (parsing may have been the failure),
        so we read ids defensively and never re-raise out of the callback.
        """
        from src.services.dead_letter import DeadLetterService

        data = ctx.data if isinstance(ctx.data, dict) else {}
        user_id = data.get("user_id", "")
        event_id = data.get("event_id", "")
        # The event now carries workspace_id directly; prefer it. Fall back to
        # resolving from user_id only when the raw entry lacks a workspace.
        workspace_id = data.get("workspace_id", "")

        factory = get_session_factory()
        async with factory() as db:
            if not workspace_id and user_id:
                try:
                    workspace_id = await resolve_workspace_id(db, user_id)
                except Exception:
                    workspace_id = ""
            dlq = DeadLetterService(db)
            await dlq.enqueue(
                user_id=user_id,
                operation_type=f"worker_{group}",
                error_type=type(ctx.error).__name__ if ctx.error else "UnknownError",
                error_message=str(ctx.error) if ctx.error else "handler failed",
                source_id=event_id or None,
                payload={"event_id": event_id, "group": group, "stream": ctx.stream},
                workspace_id=workspace_id,
            )
            await db.commit()

    async def stop(self) -> None:
        self._running = False
        for task in self._tasks:
            task.cancel()
        self._tasks.clear()

    async def _handle_entity_extraction(self, event) -> None:
        """Extract entities from an event, then sync to Neo4j and Qdrant.

        Filters to ``event_processed`` only — the main stream carries other
        event types (``trigger.fired``, ``initiative.high_priority``) that also
        include ``event_id`` in their payload. Without this filter, extraction
        runs redundantly per event and can also hit the "Event not found"
        warning when a stale message arrives after retention eviction.

        When ``settings.perception_triage_enabled``, extraction is gated on the
        triage tier persisted on the event: skip/light tiers do no entity
        extraction (see ``_handle_memory_extraction`` for the memory-only
        light-tier path). A full-tier ``calendar_invite`` whose meeting entity
        already exists (a recurring series occurrence) also skips re-extraction.
        """
        if getattr(event, "event_type", "") != "event_processed":
            return
        event_id = event.payload.get("event_id", "")
        user_id = event.user_id
        if not event_id:
            return
        workspace_id = getattr(event, "workspace_id", "") or ""
        if not workspace_id:
            logger.warning(
                "Skipping entity extraction: empty workspace_id (user=%s, event=%s)",
                user_id,
                event_id,
            )
            return

        from sqlalchemy import select

        from src.models.events import NormalizedEvent
        from src.services.world_model import WorldModel

        factory = get_session_factory()
        async with factory() as db:
            event_result = await db.execute(
                select(NormalizedEvent).where(NormalizedEvent.event_id == event_id)
            )
            ev = event_result.scalar_one_or_none()
            if ev is None:
                return

            world_model = WorldModel(
                settings=self._settings,
                db=db,
                vector_store=self._vector_store,
            )

            if self._settings.perception_triage_enabled:
                tier = _event_tier(ev)
                if tier in {"skip", "light"}:
                    logger.info("Tier=%s event %s: no entity extraction", tier, event_id)
                    return
                if _event_category(ev) == "calendar_invite":
                    try:
                        existing = await world_model.find_entity(
                            user_id, ev.title or "", workspace_id=workspace_id
                        )
                    except Exception:
                        # Dedup is a cost optimization, not correctness — a lookup
                        # failure must fall back to extracting rather than skip it.
                        existing = []
                        logger.warning(
                            "Calendar-recurrence dedup lookup failed for event %s; "
                            "proceeding with extraction",
                            event_id,
                            exc_info=True,
                        )
                    if any((e.get("entity_type") == "meeting") for e in (existing or [])):
                        logger.info(
                            "Recurring meeting for event %s already extracted; skipping",
                            event_id,
                        )
                        return

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
        """Extract memories from an event, linked to relevant entities.

        Filters to ``event_processed`` only. See ``_handle_entity_extraction``
        for the rationale.

        When ``settings.perception_triage_enabled``, skip-tier events do no
        memory extraction. Light and full tiers both extract memories — the
        founder spend/receipt ledger is a light-tier's entire value.
        """
        if getattr(event, "event_type", "") != "event_processed":
            return
        event_id = event.payload.get("event_id", "")
        user_id = event.user_id
        if not event_id:
            return
        workspace_id = getattr(event, "workspace_id", "") or ""
        if not workspace_id:
            logger.warning(
                "Skipping memory extraction: empty workspace_id (user=%s, event=%s)",
                user_id,
                event_id,
            )
            return

        from sqlalchemy import select

        from src.models.events import NormalizedEvent
        from src.services.memory_service import MemoryService
        from src.services.world_model import WorldModel

        factory = get_session_factory()
        async with factory() as db:
            result = await db.execute(
                select(NormalizedEvent).where(NormalizedEvent.event_id == event_id)
            )
            ev = result.scalar_one_or_none()
            if not ev:
                return

            if self._settings.perception_triage_enabled and _event_tier(ev) == "skip":
                logger.info("Skip-tier event %s: no memory extraction", event_id)
                return

            # Find entities related to this event for entity-memory linking
            entity_ids = None
            if ev.title or ev.summary:
                wm = WorldModel(settings=self._settings, db=db)
                query = ev.title or ev.summary or ""
                entities = await wm.resolve_entities(
                    user_id, query[:100], workspace_id=workspace_id
                )
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
        from src.services.trigger_engine import TriggerEngine

        user_id = event.user_id
        workspace_id = getattr(event, "workspace_id", "") or ""
        if not workspace_id:
            logger.warning(
                "Skipping trigger evaluation: empty workspace_id (user=%s)",
                user_id,
            )
            return

        factory = get_session_factory()
        async with factory() as db:
            engine = TriggerEngine(db)
            fired = await engine.evaluate(event, workspace_id=workspace_id)
            await db.commit()
            if fired:
                logger.info("Triggers fired for event: %d", len(fired))

    async def _handle_graph_sync(self, event) -> None:
        """Sync entity/relationship changes to Neo4j.

        Triggered by entity.created / entity.updated / relationship.created
        events on the agent events stream (jarvis:agent_events:{workspace_id}).
        Skips silently when neo4j_url is not configured.
        """
        if not self._settings.neo4j_url:
            return

        entity_id = event.payload.get("entity_id", "")
        relation_id = event.payload.get("relationship_id", event.payload.get("relation_id", ""))
        if not entity_id and not relation_id:
            return
        workspace_id = getattr(event, "workspace_id", "") or ""
        if not workspace_id:
            logger.warning(
                "Skipping graph sync: empty workspace_id (user=%s)",
                event.user_id,
            )
            return

        from src.services.graph_sync import GraphSyncService

        factory = get_session_factory()
        async with factory() as db:
            graph_sync = GraphSyncService(self._settings, db)
            try:
                if entity_id:
                    await graph_sync.on_entity_change(event)
                    logger.debug("Graph sync completed for entity %s", entity_id)
                elif relation_id:
                    await graph_sync.on_relationship_change(event)
                    logger.debug("Graph sync completed for relationship %s", relation_id)
            except Exception:
                logger.warning(
                    "Neo4j graph sync failed for %s",
                    entity_id or relation_id,
                    exc_info=True,
                )
            finally:
                await graph_sync.close()

    async def _handle_contradiction_check(self, event) -> None:
        """Check if a newly stored memory contradicts existing ones.

        Triggered by memory.stored events on the main events stream
        (jarvis:events:{workspace_id}).  Skips silently when:
        - memory_id is absent/empty in the payload
        - fact_text is absent/empty in the payload
        """
        memory_id = event.payload.get("memory_id", "")
        fact_text = event.payload.get("fact_text", "")
        if not memory_id or not fact_text:
            return
        user_id = event.user_id
        workspace_id = getattr(event, "workspace_id", "") or ""
        if not workspace_id:
            logger.warning(
                "Skipping contradiction check: empty workspace_id (user=%s, memory=%s)",
                user_id,
                memory_id,
            )
            return

        from src.services.memory_service import MemoryService

        factory = get_session_factory()
        async with factory() as db:
            memory_service = MemoryService(
                settings=self._settings,
                db=db,
                vector_store=self._vector_store,
            )
            superseded = await memory_service.check_contradictions(
                user_id=user_id,
                new_fact=fact_text,
                new_memory_id=memory_id,
                workspace_id=workspace_id,
            )
            if superseded:
                await db.commit()
                logger.info(
                    "Contradiction check for memory %s: %d superseded",
                    memory_id,
                    len(superseded),
                )
