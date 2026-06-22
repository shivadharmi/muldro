"""Factory for ``GraphExecutor`` — consistent dependency wiring.

Extracted from ``graph_executor.py`` (god-object decomposition, 2026-06-20) so the
~130-line dependency-assembly function lives apart from the executor class itself.
``GraphExecutor`` re-exports ``create_graph_executor`` so existing callers/tests
(``from src.services.graph_executor import create_graph_executor`` and
``patch("src.services.graph_executor.create_graph_executor")``) keep working.

``GraphExecutor`` is imported lazily inside the function so this module imports
*down* only — ``graph_executor`` imports this factory for the re-export, and this
factory never imports ``graph_executor`` at module load time (no cycle).
"""

from __future__ import annotations

import logging
from typing import TYPE_CHECKING

from sqlalchemy.ext.asyncio import AsyncSession

from src.config.settings import Settings

if TYPE_CHECKING:
    from src.services.graph_executor import GraphExecutor

logger = logging.getLogger(__name__)


async def create_graph_executor(
    settings: Settings,
    db: AsyncSession,
    workspace_id: str = "",
    db_factory=None,
    execute_tool_fn=None,
    budget=None,
    circuit_breaker=None,
) -> GraphExecutor:
    """Factory that creates a GraphExecutor with all deps consistently resolved.

    Use this instead of instantiating GraphExecutor directly so that every
    callsite (API routes, orchestrator, runtime) gets the same dep set.
    """
    from src.services.event_bus import EventBus
    from src.services.graph_executor import GraphExecutor
    from src.services.notifier import Notifier
    from src.services.tool_registry import ToolRegistry

    event_bus: EventBus | None = None
    try:
        import redis.asyncio as aioredis

        event_bus = EventBus(aioredis.from_url(settings.redis_url, decode_responses=True))
    except Exception:
        logger.debug("EventBus unavailable for GraphExecutor", exc_info=True)

    notifier: Notifier | None = None
    try:
        import redis.asyncio as aioredis

        from src.services.surface_registry import SurfaceRegistry

        notifier_redis = aioredis.from_url(settings.redis_url, decode_responses=True)
        surface_registry = SurfaceRegistry(redis=notifier_redis)
        notifier = Notifier(
            surface_registry=surface_registry,
            redis=notifier_redis,
            db=db,
        )
    except Exception:
        logger.debug("Notifier unavailable for GraphExecutor", exc_info=True)

    tool_registry: ToolRegistry | None = None
    try:
        tool_registry = ToolRegistry(db)
    except Exception:
        logger.debug("ToolRegistry unavailable for GraphExecutor", exc_info=True)

    world_model = None
    try:
        from src.services.world_model import WorldModel

        world_model = WorldModel(settings, db)
    except Exception:
        logger.debug("WorldModel unavailable for GraphExecutor", exc_info=True)

    memory_service = None
    try:
        from src.services.memory_service import MemoryService

        memory_service = MemoryService(settings=settings, db=db)
    except Exception:
        logger.debug("MemoryService unavailable for GraphExecutor", exc_info=True)

    context_builder = None
    try:
        from src.services.context_builder import ContextBuilder

        context_builder = ContextBuilder(
            world_model=world_model,
            memory_service=memory_service,
            tool_registry=tool_registry,
            db=db,
        )
    except Exception:
        logger.debug("ContextBuilder unavailable for GraphExecutor", exc_info=True)

    verifier = None
    try:
        from src.services.verifier import Verifier

        verifier = Verifier(settings, db)
    except Exception:
        logger.debug("Verifier unavailable for GraphExecutor", exc_info=True)

    trust_engine = None
    try:
        from src.services.trust_engine import TrustEngine

        trust_engine = TrustEngine(db, workspace_id)
    except Exception:
        logger.debug("TrustEngine unavailable for GraphExecutor", exc_info=True)

    redis_conn = None
    try:
        import redis.asyncio as aioredis

        redis_conn = aioredis.from_url(settings.redis_url, decode_responses=True)
    except Exception:
        logger.debug("Redis unavailable for GraphExecutor", exc_info=True)

    trace_store = None
    try:
        from src.services.trace_store import TraceStore

        trace_store = TraceStore(db_factory=db_factory)
    except Exception:
        logger.debug("TraceStore unavailable for GraphExecutor", exc_info=True)

    return GraphExecutor(
        settings=settings,
        db=db,
        event_bus=event_bus,
        notifier=notifier,
        tool_registry=tool_registry,
        verifier=verifier,
        context_builder=context_builder,
        memory_service=memory_service,
        world_model=world_model,
        db_factory=db_factory,
        execute_tool_fn=execute_tool_fn,
        budget=budget,
        circuit_breaker=circuit_breaker,
        trust_engine=trust_engine,
        redis=redis_conn,
        trace_store=trace_store,
    )
