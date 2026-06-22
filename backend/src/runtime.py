"""RuntimeContainer — single composition root for API, worker, scheduler, and bot.

All service wiring happens here. Three tiers control startup behaviour:
  Tier 1 (fail fast): WorldModel, MemoryService, EmbeddingService
  Tier 2 (log + degrade): EventProcessor, Governor, Presenter,
                          AuditService, ToolRegistry, GraphExecutor
  Tier 3 (optional): VectorStore, GraphEngine, RerankerService,
                      TriSearchService, EventCorrelator, OAuthManager, Notifier

Session model (P2 #4 — reverses the old "one long-lived session" ADR §10):
  * ``build_shared(settings)`` builds **session-free** singletons (Qdrant /
    Neo4j / Bedrock clients, the Redis client, the shared EventBus, the
    OAuthManager which uses a db_factory). These are safe to share across
    concurrent requests.
  * ``attach_session(shared, settings, db)`` builds the **DB-bound** services
    against a single per-request ``AsyncSession``, reusing the shared singletons
    by identity. Each concurrent request gets its own session, so no
    ``AsyncSession`` is ever used by two requests at once.
  * ``build(settings, db)`` = ``attach_session(build_shared(settings), …)`` —
    a backwards-compatible composition root for single-flow callers (scheduler,
    one-off background tasks, tests). The API path builds ``shared`` once and
    calls ``attach_session`` per request.
"""

import logging

from sqlalchemy.ext.asyncio import AsyncSession

from src.config.settings import Settings
from src.orchestrator.services import ServiceContainer

logger = logging.getLogger(__name__)


class RuntimeBuildError(RuntimeError):
    """Raised when a Tier 1 service fails to initialise."""


def _check_oauth_key(settings: Settings) -> None:
    """Fail (prod) or warn (dev) when the OAuth encryption key is missing."""
    if settings.oauth_encryption_key:
        return
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


def build_shared(settings: Settings) -> ServiceContainer:
    """Build session-free singletons shared across all requests.

    These services hold no per-request ``AsyncSession`` (vector store, graph
    engine, reranker, tri-search, artifact store, OAuth manager, the Redis
    client, and the stateless EventBus). DB-bound services are left ``None``
    and built per-request by :func:`attach_session`.
    """
    _check_oauth_key(settings)
    svc = ServiceContainer()

    # ── Tier 1 (fail fast): EmbeddingService is session-free ───────
    try:
        from src.services.embedding_service import EmbeddingService

        svc.extras["embedding_service"] = EmbeddingService(settings)
    except Exception as exc:
        raise RuntimeBuildError(f"Tier 1 failure: EmbeddingService — {exc}") from exc

    # ── Tier 3 optional, session-free ──────────────────────────────
    try:
        from src.services.vector_store import VectorStore

        svc.vector_store = VectorStore(settings)
    except Exception:
        logger.warning(
            "Tier 3: VectorStore unavailable — semantic search and embedding disabled",
            exc_info=True,
        )

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
        from src.services.artifact_store import ArtifactStore

        svc.artifact_store = ArtifactStore(settings)
    except Exception:
        logger.debug("Tier 3: ArtifactStore unavailable", exc_info=True)

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

    # ── Shared Redis client + stateless EventBus ───────────────────
    try:
        import redis.asyncio as aioredis

        svc.extras["redis"] = aioredis.from_url(settings.redis_url, decode_responses=True)
    except Exception:
        logger.debug("Shared Redis client unavailable", exc_info=True)

    try:
        from src.services.event_bus import EventBus

        if svc.extras.get("redis") is not None:
            svc.extras["event_bus"] = EventBus(svc.extras["redis"])
    except Exception:
        logger.debug("Shared EventBus unavailable", exc_info=True)

    return svc


def attach_session(
    shared: ServiceContainer, settings: Settings, db: AsyncSession
) -> ServiceContainer:
    """Build a per-request ServiceContainer bound to ``db``.

    Reuses ``shared``'s session-free singletons by identity and constructs all
    DB-bound services against the given per-request session. Tier 1 failures
    raise :class:`RuntimeBuildError`; Tier 2/3 degrade gracefully.
    """
    svc = ServiceContainer()

    # Reuse session-free singletons by identity (no per-request churn).
    svc.vector_store = shared.vector_store
    svc.graph_engine = shared.graph_engine
    svc.reranker = shared.reranker
    svc.tri_search = shared.tri_search
    svc.artifact_store = shared.artifact_store
    svc.oauth_manager = shared.oauth_manager
    svc.extras = dict(shared.extras)  # keeps embedding_service, redis, event_bus

    shared_redis = shared.extras.get("redis")
    event_bus = shared.extras.get("event_bus")
    vector_store = shared.vector_store
    embedding_service = shared.extras.get("embedding_service")

    # Per-request DLQ for failed-embedding fallback in world_model/memory_service.
    dead_letter = None
    try:
        from src.services.dead_letter import DeadLetterService

        dead_letter = DeadLetterService(db)
    except Exception:
        logger.debug("DeadLetterService unavailable for per-request services", exc_info=True)

    # ── Tier 1: fail fast ──────────────────────────────────────────
    # Wire the shared vector_store / embedding / event_bus + per-request DLQ so
    # entities and memories reach Qdrant, emit domain events, and use the
    # failed-embedding DLQ fallback — matching how the hot paths build these.
    try:
        from src.services.world_model import WorldModel

        svc.world_model = WorldModel(
            settings,
            db,
            event_bus=event_bus,
            embedding_service=embedding_service,
            vector_store=vector_store,
            dead_letter=dead_letter,
        )
    except Exception as exc:
        raise RuntimeBuildError(f"Tier 1 failure: WorldModel — {exc}") from exc

    try:
        from src.services.memory_service import MemoryService

        svc.memory_service = MemoryService(
            settings=settings,
            db=db,
            event_bus=event_bus,
            vector_store=vector_store,
            dead_letter=dead_letter,
        )
    except Exception as exc:
        raise RuntimeBuildError(f"Tier 1 failure: MemoryService — {exc}") from exc

    # ── Tier 2: log + degrade ──────────────────────────────────────
    try:
        from src.services.event_processor import EventProcessor

        svc.event_processor = EventProcessor(
            settings,
            db,
            world_model=svc.world_model,
            memory_service=svc.memory_service,
            dead_letter=dead_letter,
            event_bus=event_bus,
            embedding_service=embedding_service,
            vector_store=vector_store,
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

    # ── Tier 3 db-bound ────────────────────────────────────────────
    try:
        if svc.graph_engine:
            from src.services.graph_sync import GraphSyncService

            svc.extras["graph_sync"] = GraphSyncService(settings, db)
    except Exception:
        logger.debug("Tier 3: GraphSyncService unavailable", exc_info=True)

    try:
        from src.services.event_correlator import EventCorrelator

        svc.extras["event_correlator"] = EventCorrelator(db)
    except Exception:
        logger.debug("Tier 3: EventCorrelator unavailable", exc_info=True)

    # ── Wire execution layer (db-bound) ────────────────────────────
    tool_registry = svc.extras.get("tool_registry")
    try:
        from src.services.graph_executor import GraphExecutor

        notifier = None
        try:
            from src.services.notifier import Notifier
            from src.services.surface_registry import SurfaceRegistry

            if shared_redis is not None:
                notifier = Notifier(
                    surface_registry=SurfaceRegistry(redis=shared_redis),
                    redis=shared_redis,
                    db=db,
                )
                svc.notifier = notifier
        except Exception:
            logger.debug("Notifier unavailable for GraphExecutor", exc_info=True)

        verifier = None
        try:
            from src.services.verifier import Verifier

            verifier = Verifier(settings, db)
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

        trust_engine = None
        try:
            from src.services.trust_engine import TrustEngine

            trust_engine = TrustEngine(db)
            svc.trust_engine = trust_engine
        except Exception:
            logger.debug("TrustEngine unavailable for GraphExecutor", exc_info=True)

        svc.graph_executor = GraphExecutor(
            settings=settings,
            db=db,
            event_bus=event_bus,
            notifier=notifier,
            tool_registry=tool_registry,
            verifier=verifier,
            context_builder=context_builder,
            memory_service=svc.memory_service,
            trust_engine=trust_engine,
            redis=shared_redis,
        )
    except Exception:
        logger.warning("Tier 2: GraphExecutor unavailable", exc_info=True)

    return svc


# DB-bound fields whose presence marks a container as "already wired to a
# session" (an injected mock/full container or a single-flow build()) rather
# than a session-free shared container from build_shared().
_DB_BOUND_FIELDS = (
    "world_model",
    "memory_service",
    "governor",
    "presenter",
    "audit",
    "event_processor",
)


def request_services(
    base: ServiceContainer | None, settings: Settings, db: AsyncSession
) -> ServiceContainer:
    """Return DB-bound services for the per-request session ``db``.

    Reuse ``base`` when it already carries DB-bound services — that means a full
    or partial container was injected (tests) or built single-flow (``build``),
    and its services already own a session. Only the session-free shared
    container (all DB-bound fields ``None``, from :func:`build_shared`) triggers
    a per-request :func:`attach_session`. This is the single discriminator used
    by every caller's ``_request_services`` bridge (P2 #4).
    """
    if base is not None and any(getattr(base, f, None) is not None for f in _DB_BOUND_FIELDS):
        return base
    return attach_session(base, settings, db)


def build(settings: Settings, db: AsyncSession) -> ServiceContainer:
    """Build a fully-wired ServiceContainer from a single db session.

    Backwards-compatible composition root for single-flow callers (scheduler,
    one-off background tasks, tests). The API path uses :func:`build_shared`
    once + :func:`attach_session` per request so concurrent requests never
    share an ``AsyncSession``.
    """
    svc = attach_session(build_shared(settings), settings, db)
    _log_summary(svc)
    return svc


def validate_tier3_health(settings: Settings, svc: ServiceContainer) -> list[str]:
    """Check configured-but-missing Tier 3 services. Returns degraded names."""
    degraded: list[str] = []

    if settings.neo4j_url and not svc.graph_engine:
        logger.warning(
            "DEGRADED: Neo4j configured (JARVIS_NEO4J_URL set) but GraphEngine "
            "failed to initialize. Entity graph traversal and sync are disabled."
        )
        degraded.append("neo4j")

    if settings.qdrant_url and not svc.vector_store:
        logger.warning(
            "DEGRADED: Qdrant configured (JARVIS_QDRANT_URL set) but VectorStore "
            "failed to initialize. Semantic search and embedding are disabled."
        )
        degraded.append("qdrant")

    if getattr(settings, "reranker_enabled", False) and not svc.reranker:
        logger.warning("DEGRADED: Reranker enabled but RerankerService failed to initialize.")
        degraded.append("reranker")

    svc.extras["degraded_services"] = degraded
    return degraded


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
