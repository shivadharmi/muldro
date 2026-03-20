"""FastAPI application — Jarvis backend entry point."""

import logging
from contextlib import asynccontextmanager

from fastapi import FastAPI
from fastapi.middleware.cors import CORSMiddleware

from src.api.routes_agent_routes import router as agent_routes_router
from src.api.routes_agents import router as agents_router
from src.api.routes_approvals import router as approvals_router
from src.api.routes_artifacts import router as artifacts_router
from src.api.routes_auth import router as auth_router
from src.api.routes_briefings import router as briefings_router
from src.api.routes_canvas import router as canvas_router
from src.api.routes_chat import router as chat_router
from src.api.routes_command import router as command_router
from src.api.routes_connectors import router as connectors_router
from src.api.routes_conversations import router as conversations_router
from src.api.routes_events import router as events_router
from src.api.routes_executions import router as executions_router
from src.api.routes_feedback import router as feedback_router
from src.api.routes_goals import router as goals_router
from src.api.routes_graph import router as graph_router
from src.api.routes_health import router as health_router
from src.api.routes_meetings import router as meetings_router
from src.api.routes_memories import router as memories_router
from src.api.routes_metrics import router as metrics_router
from src.api.routes_notifications import router as notifications_router
from src.api.routes_observation import router as observation_router
from src.api.routes_realtime import router as realtime_router
from src.api.routes_runs import router as runs_router
from src.api.routes_schedules import router as schedules_router
from src.api.routes_search import router as search_router
from src.api.routes_settings import router as settings_router
from src.api.routes_system import router as system_router
from src.api.routes_tasks import router as tasks_router
from src.api.routes_traces import router as traces_router
from src.api.routes_triggers import router as triggers_router
from src.api.routes_ui import router as ui_router
from src.api.routes_webhooks import router as webhooks_router
from src.api.routes_workflows import router as workflows_router
from src.api.routes_ws import router as ws_router
from src.api.schemas import HealthResponse
from src.config.settings import get_settings
from src.middleware.observability import TracingMiddleware
from src.middleware.security import RateLimitMiddleware, RequestSizeLimitMiddleware

logger = logging.getLogger(__name__)


def create_app() -> FastAPI:
    settings = get_settings()

    @asynccontextmanager
    async def lifespan(app: FastAPI):
        # Startup: connect to Redis
        try:
            import redis.asyncio as aioredis

            app.state.redis = aioredis.from_url(settings.redis_url, decode_responses=True)
            await app.state.redis.ping()
            logger.info("Redis connected: %s", settings.redis_url)
        except Exception:
            logger.warning("Redis unavailable — using in-memory fallback for rate limiting")
            app.state.redis = None

        # Initialize surface registry
        from src.services.surface_registry import SurfaceRegistry

        app.state.surface_registry = SurfaceRegistry(redis=app.state.redis)

        # Seed default tool definitions and agent configurations
        try:
            from src.models.database import get_session_factory
            from src.services.agent_registry import AgentRegistry
            from src.services.route_resolver import RouteResolver
            from src.services.tool_registry import ToolRegistry

            async with get_session_factory()() as db:
                tool_count = await ToolRegistry(db).seed_defaults()
                agent_count = await AgentRegistry(db).seed_defaults()
                route_count = await RouteResolver(db).seed_defaults()

                from sqlalchemy import select as sa_select

                from src.models.users import User, WorkspaceMember
                from src.services.schedule_seeder import seed_default_schedules

                user_rows = await db.execute(sa_select(User.user_id))
                user_ids = [row[0] for row in user_rows.all()]

                # Build user → workspace mapping for schedule seeding
                wm_rows = await db.execute(
                    sa_select(WorkspaceMember.user_id, WorkspaceMember.workspace_id)
                )
                user_workspace = {r[0]: r[1] for r in wm_rows.all()}

                sched_count = 0
                for uid in user_ids:
                    sched_count += await seed_default_schedules(
                        db,
                        user_id=uid,
                        workspace_id=user_workspace.get(uid, ""),
                    )

                if tool_count or agent_count or route_count or sched_count:
                    await db.commit()
                    if tool_count:
                        logger.info("Seeded %d tool definitions", tool_count)
                    if agent_count:
                        logger.info("Seeded %d agent definitions", agent_count)
                    if sched_count:
                        logger.info("Seeded %d default schedules", sched_count)
        except Exception:
            logger.debug(
                "Registry seed skipped (DB not ready)",
                exc_info=True,
            )

        # Ensure Elasticsearch indices exist
        try:
            from src.services.search_service import SearchService

            search_svc = SearchService(settings)
            await search_svc.ensure_indices()
        except Exception:
            logger.debug("ES index init skipped", exc_info=True)

        # Ensure Qdrant collections exist
        try:
            from src.services.vector_store import VectorStore

            vector_store = VectorStore(settings)
            await vector_store.ensure_collections()
        except Exception:
            logger.debug("Qdrant collection init skipped", exc_info=True)

        # Initialize MCP bridge to external servers (Google Workspace, GitHub, Slack, etc.)
        try:
            from src.connectors.mcp_bridge import initialize_mcp_bridge

            await initialize_mcp_bridge()
        except Exception:
            logger.debug("MCP bridge init skipped", exc_info=True)

        yield

        # Shutdown: close shared Anthropic client
        try:
            from src.config.settings import close_anthropic_client

            await close_anthropic_client()
        except Exception:
            pass

        # Shutdown: close MCP bridge
        try:
            from src.connectors.mcp_bridge import shutdown_mcp_bridge

            await shutdown_mcp_bridge()
        except Exception:
            pass

        # Shutdown: close long-lived orchestrator DB session
        try:
            from src.api.routes_chat import _module_svc_db_ref

            for db_ref in _module_svc_db_ref:
                await db_ref.close()
            _module_svc_db_ref.clear()
            logger.info("Orchestrator DB sessions closed")
        except Exception:
            pass

        # Shutdown: dispose DB engine pool (returns all connections)
        try:
            from src.models.database import get_engine

            engine = get_engine()
            await engine.dispose()
            logger.info("Database engine disposed")
        except Exception:
            pass

        # Shutdown: close Redis
        if getattr(app.state, "redis", None):
            await app.state.redis.aclose()
            logger.info("Redis connection closed")

    app = FastAPI(
        title="Jarvis Backend",
        description="Personal AI Operating System — Backend Services",
        version="0.1.0",
        lifespan=lifespan,
    )

    # --- Middleware (outermost first) ---
    app.add_middleware(RequestSizeLimitMiddleware, max_bytes=settings.max_request_body_bytes)
    app.add_middleware(RateLimitMiddleware, requests_per_minute=settings.rate_limit_rpm)
    app.add_middleware(TracingMiddleware)

    # CORS — only if origins configured
    if settings.cors_allowed_origins:
        origins = [o.strip() for o in settings.cors_allowed_origins.split(",") if o.strip()]
        app.add_middleware(
            CORSMiddleware,
            allow_origins=origins,
            allow_methods=["*"],
            allow_headers=["*"],
        )

    # Health check
    @app.get("/v1/health", response_model=HealthResponse)
    async def health():
        return HealthResponse()

    # Core product routes
    app.include_router(chat_router, tags=["chat"])
    app.include_router(command_router, tags=["command"])
    app.include_router(briefings_router, tags=["briefings"])
    app.include_router(approvals_router, tags=["approvals"])
    app.include_router(tasks_router, tags=["tasks"])
    app.include_router(search_router, tags=["search"])
    app.include_router(meetings_router, tags=["meetings"])
    app.include_router(canvas_router, tags=["canvas"])
    app.include_router(feedback_router, tags=["feedback"])

    # Event ingestion
    app.include_router(events_router, tags=["events"])

    # Legacy webhook route (backwards compat)
    app.include_router(webhooks_router, tags=["webhooks"])

    # Observation health tracking
    app.include_router(observation_router, tags=["observations"])

    # Schedules (backend-owned dynamic scheduling)
    app.include_router(schedules_router, tags=["schedules"])

    # System routes (heartbeat, maintenance, metrics)
    app.include_router(system_router, tags=["system"])

    # OAuth authentication callbacks
    app.include_router(auth_router, tags=["auth"])

    # A2UI surface state REST endpoints
    app.include_router(ui_router, tags=["ui"])

    # WebSocket for real-time A2UI surface streaming
    app.include_router(ws_router, tags=["websocket"])

    # System health dashboard
    app.include_router(health_router, tags=["health"])

    # User settings
    app.include_router(settings_router, tags=["settings"])

    # Connectors
    app.include_router(connectors_router, tags=["connectors"])

    # Prometheus metrics
    app.include_router(metrics_router, tags=["metrics"])

    # Memories
    app.include_router(memories_router, tags=["memories"])

    # Executions
    app.include_router(executions_router, tags=["executions"])

    # Triggers
    app.include_router(triggers_router, tags=["triggers"])

    # Traces (observability)
    app.include_router(traces_router, tags=["traces"])

    # Conversations (chat session persistence)
    app.include_router(conversations_router, tags=["conversations"])

    # Goals
    app.include_router(goals_router, tags=["goals"])

    # Artifacts
    app.include_router(artifacts_router, tags=["artifacts"])

    # Realtime SSE streaming
    app.include_router(realtime_router, tags=["realtime"])

    # Notifications
    app.include_router(notifications_router, tags=["notifications"])

    # Workflows
    app.include_router(workflows_router, tags=["workflows"])

    # Runs (task execution runs)
    app.include_router(runs_router, tags=["runs"])
    # Agent management
    app.include_router(agents_router, tags=["agents"])
    app.include_router(agent_routes_router, tags=["agent-routes"])

    # Knowledge graph (Neo4j)
    app.include_router(graph_router, tags=["graph"])

    return app


app = create_app()
