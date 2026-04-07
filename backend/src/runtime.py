"""RuntimeContainer — single composition root for API, worker, scheduler, and bot.

All service wiring happens here. Three tiers control startup behaviour:
  Tier 1 (fail fast): WorldModel, MemoryService, EmbeddingService
  Tier 2 (log + degrade): EventProcessor, Governor, Presenter,
                          AuditService, ToolRegistry, GraphExecutor
  Tier 3 (optional): VectorStore, GraphEngine, RerankerService,
                      TriSearchService, EventCorrelator, OAuthManager, Notifier
"""

import logging

from sqlalchemy.ext.asyncio import AsyncSession

from src.config.settings import Settings
from src.orchestrator.services import ServiceContainer

logger = logging.getLogger(__name__)


class RuntimeBuildError(RuntimeError):
    """Raised when a Tier 1 service fails to initialise."""


def build(settings: Settings, db: AsyncSession) -> ServiceContainer:
    """Build a fully-wired ServiceContainer from a long-lived db session.

    Tier 1 services raise on failure (the system cannot operate without them).
    Tier 2 services log a warning and degrade gracefully.
    Tier 3 services are optional — failures are debug-level.
    """
    svc = ServiceContainer()

    # ── Pre-flight: OAuth encryption key ──────────────────────────────
    if not settings.oauth_encryption_key:
        if getattr(settings, "environment", "development") == "production":
            raise RuntimeBuildError(
                "JARVIS_OAUTH_ENCRYPTION_KEY is required in production. "
                "OAuth tokens will be stored in PLAINTEXT without it."
            )
        logger.error(
            "JARVIS_OAUTH_ENCRYPTION_KEY is not set — "
            "OAuth tokens will be stored in PLAINTEXT. "
            "Set this variable to a Fernet-compatible key."
        )

    # ── Tier 1: fail fast ──────────────────────────────────────────
    try:
        from src.services.world_model import WorldModel

        svc.world_model = WorldModel(settings, db)
    except Exception as exc:
        raise RuntimeBuildError(f"Tier 1 failure: WorldModel — {exc}") from exc

    try:
        from src.services.memory_service import MemoryService

        svc.memory_service = MemoryService(settings=settings, db=db)
    except Exception as exc:
        raise RuntimeBuildError(f"Tier 1 failure: MemoryService — {exc}") from exc

    try:
        from src.services.embedding_service import EmbeddingService

        svc.extras["embedding_service"] = EmbeddingService(settings)
    except Exception as exc:
        raise RuntimeBuildError(f"Tier 1 failure: EmbeddingService — {exc}") from exc

    # ── Tier 2: log + degrade ──────────────────────────────────────
    try:
        from src.services.event_processor import EventProcessor

        svc.event_processor = EventProcessor(
            settings,
            db,
            world_model=svc.world_model,
            memory_service=svc.memory_service,
        )
    except Exception:
        logger.warning("Tier 2: EventProcessor unavailable", exc_info=True)

    try:
        from src.services.governor import Governor

        svc.governor = Governor(db)
    except Exception:
        logger.warning("Tier 2: Governor unavailable", exc_info=True)

    try:
        from src.services.presenter import Presenter

        svc.presenter = Presenter(settings, db)
    except Exception:
        logger.warning("Tier 2: Presenter unavailable", exc_info=True)

    try:
        from src.services.audit import AuditService

        svc.audit = AuditService(db)
    except Exception:
        logger.warning("Tier 2: AuditService unavailable", exc_info=True)

    try:
        from src.services.tool_registry import ToolRegistry

        svc.extras["tool_registry"] = ToolRegistry(db)
    except Exception:
        logger.warning("Tier 2: ToolRegistry unavailable", exc_info=True)

    # ── Tier 3: optional ───────────────────────────────────────────
    try:
        from src.services.vector_store import VectorStore

        svc.vector_store = VectorStore(settings)
    except Exception:
        logger.debug("Tier 3: VectorStore unavailable", exc_info=True)

    try:
        from src.services.graph_engine import GraphEngine

        if settings.neo4j_url:
            svc.graph_engine = GraphEngine(settings)
    except Exception:
        logger.debug("Tier 3: GraphEngine unavailable", exc_info=True)

    try:
        from src.services.reranker_service import RerankerService

        if settings.reranker_enabled:
            svc.reranker = RerankerService(settings)
    except Exception:
        logger.debug("Tier 3: RerankerService unavailable", exc_info=True)

    try:
        from src.services.tri_search import TriSearchService

        svc.tri_search = TriSearchService(
            settings=settings,
            vector_store=svc.vector_store,
            graph_engine=svc.graph_engine,
            reranker=svc.reranker,
            embedder=svc.extras.get("embedding_service"),
        )
    except Exception:
        logger.debug("Tier 3: TriSearchService unavailable", exc_info=True)

    try:
        from src.services.event_correlator import EventCorrelator

        svc.extras["event_correlator"] = EventCorrelator(db)
    except Exception:
        logger.debug("Tier 3: EventCorrelator unavailable", exc_info=True)

    try:
        from src.services.artifact_store import ArtifactStore

        svc.artifact_store = ArtifactStore(settings)
    except Exception:
        logger.debug("Tier 3: ArtifactStore unavailable", exc_info=True)

    # ── Wire execution layer ───────────────────────────────────────
    tool_registry = svc.extras.get("tool_registry")

    try:
        from src.services.graph_executor import GraphExecutor

        event_bus = None
        try:
            from src.services.event_bus import EventBus

            event_bus = EventBus(settings.redis_url)
        except Exception:
            logger.debug("EventBus unavailable for GraphExecutor", exc_info=True)

        notifier = None
        try:
            import redis.asyncio as aioredis

            from src.services.notifier import Notifier
            from src.services.surface_registry import SurfaceRegistry

            notifier_redis = aioredis.from_url(settings.redis_url, decode_responses=True)
            surface_registry = SurfaceRegistry(redis=notifier_redis)
            notifier = Notifier(
                surface_registry=surface_registry,
                redis=notifier_redis,
                db=db,
            )
            svc.notifier = notifier
        except Exception:
            logger.debug("Notifier unavailable for GraphExecutor", exc_info=True)

        verifier = None
        try:
            from src.services.verifier import Verifier

            verifier = Verifier(db, settings)
        except Exception:
            logger.debug("Verifier unavailable for GraphExecutor", exc_info=True)

        context_builder = None
        try:
            from src.services.context_builder import ContextBuilder

            context_builder = ContextBuilder(
                world_model=svc.world_model,
                memory_service=svc.memory_service,
                tool_registry=tool_registry,
                db=db,
            )
        except Exception:
            logger.debug("ContextBuilder unavailable for GraphExecutor", exc_info=True)

        svc.graph_executor = GraphExecutor(
            settings=settings,
            db=db,
            event_bus=event_bus,
            notifier=notifier,
            tool_registry=tool_registry,
            verifier=verifier,
            context_builder=context_builder,
            memory_service=svc.memory_service,
        )
    except Exception:
        logger.warning("Tier 2: GraphExecutor unavailable", exc_info=True)

    # ── Wire OAuthManager ──────────────────────────────────────────
    try:
        from src.models.database import get_session_factory
        from src.services.oauth_manager import OAuthManager

        svc.oauth_manager = OAuthManager(
            db_factory=get_session_factory(),
            settings=settings,
            encryption_key=settings.oauth_encryption_key,
        )
    except Exception:
        logger.debug("Tier 3: OAuthManager unavailable", exc_info=True)

    _log_summary(svc)
    return svc


def _log_summary(svc: ServiceContainer) -> None:
    """Log which services are available after build."""
    tier1 = ["world_model", "memory_service"]
    tier2 = [
        "event_processor",
        "governor",
        "presenter",
        "audit",
        "graph_executor",
    ]
    tier3 = ["vector_store", "graph_engine", "reranker", "tri_search", "artifact_store"]

    populated = []
    missing = []
    for name in tier1 + tier2 + tier3:
        if getattr(svc, name, None) is not None:
            populated.append(name)
        else:
            missing.append(name)

    logger.info(
        "RuntimeContainer built: %d/%d services (%s missing: %s)",
        len(populated),
        len(populated) + len(missing),
        len(missing),
        ", ".join(missing) if missing else "none",
    )
