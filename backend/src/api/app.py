"""FastAPI application — Jarvis backend entry point."""

import logging
from contextlib import asynccontextmanager

from fastapi import FastAPI
from fastapi.middleware.cors import CORSMiddleware

from src.api.routes_approvals import router as approvals_router
from src.api.routes_briefings import router as briefings_router
from src.api.routes_canvas import router as canvas_router
from src.api.routes_command import router as command_router
from src.api.routes_events import router as events_router
from src.api.routes_feedback import router as feedback_router
from src.api.routes_meetings import router as meetings_router
from src.api.routes_observation import router as observation_router
from src.api.routes_schedules import router as schedules_router
from src.api.routes_search import router as search_router
from src.api.routes_system import router as system_router
from src.api.routes_tasks import router as tasks_router
from src.api.routes_webhooks import router as webhooks_router
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

        yield

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

    # Core product routes (called by OpenClaw jarvis-tools plugin)
    app.include_router(command_router, tags=["command"])
    app.include_router(briefings_router, tags=["briefings"])
    app.include_router(approvals_router, tags=["approvals"])
    app.include_router(tasks_router, tags=["tasks"])
    app.include_router(search_router, tags=["search"])
    app.include_router(meetings_router, tags=["meetings"])
    app.include_router(canvas_router, tags=["canvas"])
    app.include_router(feedback_router, tags=["feedback"])

    # Event ingestion (called by OpenClaw agent after reading data from sources)
    app.include_router(events_router, tags=["events"])

    # Legacy webhook route (backwards compat)
    app.include_router(webhooks_router, tags=["webhooks"])

    # Observation health tracking
    app.include_router(observation_router, tags=["observations"])

    # Schedules (backend-owned dynamic scheduling)
    app.include_router(schedules_router, tags=["schedules"])

    # System routes (heartbeat, maintenance, metrics)
    app.include_router(system_router, tags=["system"])

    return app


app = create_app()
