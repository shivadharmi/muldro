"""FastAPI application — Jarvis backend entry point."""

from fastapi import FastAPI

from src.api.routes_approvals import router as approvals_router
from src.api.routes_briefings import router as briefings_router
from src.api.routes_command import router as command_router
from src.api.routes_meetings import router as meetings_router
from src.api.routes_search import router as search_router
from src.api.routes_system import router as system_router
from src.api.routes_tasks import router as tasks_router
from src.api.routes_webhooks import router as webhooks_router
from src.api.schemas import HealthResponse


def create_app() -> FastAPI:
    app = FastAPI(
        title="Jarvis Backend",
        description="Personal AI Operating System — Backend Services",
        version="0.1.0",
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

    # Webhook routes (called by OpenClaw plugin HTTP routes or directly by services)
    app.include_router(webhooks_router, tags=["webhooks"])

    # System routes (heartbeat, maintenance)
    app.include_router(system_router, tags=["system"])

    return app


app = create_app()
