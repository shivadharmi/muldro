"""EventPublisher — owns the lazy event bus and runtime-event emission.

Extracted from ``JarvisOrchestrator`` (god-object decomposition, 2026-06-19).
This is a leaf collaborator: it depends only on settings, the service container
(for the process-wide shared ``EventBus``/Redis), and the DB session factory.
Surfaces, tools, and perception depend downward on this class.
"""

import asyncio
import logging

from src.config.settings import Settings
from src.orchestrator.services import ServiceContainer

logger = logging.getLogger(__name__)


class EventPublisher:
    """Lazily-initialized event bus plus best-effort event publishing.

    The bus is created on first use and guarded by an ``asyncio.Lock`` so two
    concurrent callers cannot open two Redis connections (C5). When a process-wide
    ``EventBus`` was injected via the shared container extras, it is reused as-is.
    """

    def __init__(
        self,
        settings: Settings,
        services: ServiceContainer | None,
        db_factory_provider,
    ):
        self._settings = settings
        self._services = services
        # Provider (not a captured value) so the orchestrator stays the single
        # source of truth for db_factory — reassigning it there propagates here.
        self._db_factory_provider = db_factory_provider
        self._event_bus = None  # Lazy-init when Redis available
        self._event_bus_lock = asyncio.Lock()  # C5: guard lazy EventBus init
        self._event_bus_redis = None

    @property
    def _db_factory(self):
        """Resolve the current DB session factory live via the provider."""
        return self._db_factory_provider()

    @property
    def event_bus(self):
        """The cached event bus instance, or None if not yet initialized."""
        return self._event_bus

    @property
    def event_bus_redis(self):
        """The Redis client backing the event bus, or None."""
        return self._event_bus_redis

    async def ensure_event_bus(self):
        """Lazily initialize the event bus. Returns the bus or None on failure.

        Uses asyncio.Lock to prevent race condition where two concurrent
        requests both create a Redis connection (C5).
        """
        if self._event_bus is not None:
            return self._event_bus
        async with self._event_bus_lock:
            # Double-check after acquiring lock
            if self._event_bus is not None:
                return self._event_bus

            from src.services.event_bus import EventBus

            # Prefer the process-wide EventBus + Redis client from build_shared
            # (container extras) so we don't open a second Redis connection.
            extras = self._services.extras if self._services else {}
            shared_bus = extras.get("event_bus")
            if isinstance(shared_bus, EventBus):
                self._event_bus = shared_bus
                shared_redis = extras.get("redis")
                if shared_redis is not None:
                    self._event_bus_redis = shared_redis
                return self._event_bus

            try:
                import redis.asyncio as aioredis

                self._event_bus_redis = aioredis.from_url(
                    self._settings.redis_url, decode_responses=True
                )
                self._event_bus = EventBus(self._event_bus_redis)
            except Exception:
                logger.debug("Failed to init event_bus", exc_info=True)
        return self._event_bus

    async def publish_event(
        self,
        event_type: str,
        user_id: str,
        payload: dict,
        workspace_id: str = "",
        trace_id: str | None = None,
    ) -> None:
        """Publish an agent action event to the event bus (best-effort)."""
        try:
            event_bus = await self.ensure_event_bus()
            if event_bus is None:
                return

            stream = event_bus.agent_stream(workspace_id)
            metadata = {"trace_id": trace_id} if trace_id else {}
            await event_bus.publish(
                stream, event_type, payload, user_id, workspace_id=workspace_id, metadata=metadata
            )
        except Exception:
            logger.debug("Failed to publish event %s to bus", event_type, exc_info=True)

    async def emit_runtime_event(
        self,
        event_type: str,
        *,
        workspace_id: str,
        user_id: str,
        run_id: str | None = None,
        step_id: str | None = None,
        payload: dict | None = None,
    ) -> None:
        """Emit a durable runtime event to DB + Redis (best-effort)."""
        try:
            async with self._db_factory() as db:
                from src.services.runtime_events import RuntimeEventEmitter

                emitter = RuntimeEventEmitter(db, workspace_id, self._event_bus)
                await emitter.emit(
                    event_type,
                    run_id=run_id,
                    step_id=step_id,
                    user_id=user_id,
                    payload=payload,
                )
                await db.commit()
        except Exception:
            logger.warning("Failed to emit runtime event %s", event_type, exc_info=True)
