"""Entry point for running the Jarvis backend server."""

import argparse
import logging
import threading

import uvicorn

from src.config.logging import configure_logging
from src.config.settings import get_settings

_component_health: dict[str, dict] = {
    "worker": {"status": "not_started"},
}

# Cross-thread gate: set by the FastAPI lifespan after MCP bridge init,
# waited on by the worker thread before processing tasks.
mcp_bridge_ready = threading.Event()


def get_component_health() -> dict:
    """Get health status of the worker component."""
    return dict(_component_health)


def main():
    parser = argparse.ArgumentParser(description="Jarvis Backend")
    parser.add_argument(
        "--worker", action="store_true", help="Start background worker alongside API"
    )
    args = parser.parse_args()

    settings = get_settings()
    configure_logging(json_output=settings.log_json, level=logging.INFO)

    # Fail fast on misconfiguration (missing Anthropic key, missing OAuth
    # encryption key in production) before spawning the worker thread.
    settings.validate_startup()

    if args.worker:
        # Start worker in background thread alongside API
        import asyncio
        import threading

        from src.services.scheduler import SchedulerLoop
        from src.services.worker import StreamConsumerManager

        logger = logging.getLogger("jarvis.worker_thread")

        def run_worker():
            _component_health["worker"] = {"status": "starting"}
            loop = asyncio.new_event_loop()
            asyncio.set_event_loop(loop)

            # Build orchestrator for scheduler to use (if services available)
            orchestrator = None
            try:
                from src.models.database import get_session_factory
                from src.orchestrator.jarvis import JarvisOrchestrator
                from src.runtime import build as build_runtime
                from src.tools import intelligence_server

                db_factory = get_session_factory()

                async def _build():
                    svc_db = db_factory()
                    services = build_runtime(settings, svc_db)
                    intelligence_server.configure(db_factory, settings, services)

                    return JarvisOrchestrator(
                        settings=settings,
                        db_factory=db_factory,
                        services=services,
                    )

                orchestrator = loop.run_until_complete(_build())
                logger.info("Orchestrator initialized for scheduler")
            except Exception:
                logger.exception("Orchestrator not available, scheduled actions will fail")

            # Query active user IDs for worker + scheduler
            user_ids = []
            try:
                from sqlalchemy import select as sa_select

                from src.models.users import User

                db_factory = get_session_factory()

                async def _get_user_ids():
                    async with db_factory() as db:
                        result = await db.execute(sa_select(User.user_id))
                        return [row[0] for row in result.all()]

                user_ids = loop.run_until_complete(_get_user_ids())
                logger.info("Worker serving %d user(s): %s", len(user_ids), user_ids)
            except Exception:
                logger.warning("Could not load user IDs — worker will have no users")

            stream_consumer = StreamConsumerManager(settings)
            scheduler = SchedulerLoop(settings, orchestrator=orchestrator, user_ids=user_ids)

            # Wait for the FastAPI lifespan to reach the point where the MCP
            # session pool is wired. Tool discovery runs in the background,
            # so this handshake only covers pool construction + prior lifespan
            # steps (Redis, seeds, Qdrant, Neo4j). A 30s budget is generous
            # for that work; exceeding it usually means the API lifespan
            # itself is stuck on an external dependency.
            logger.info("Worker thread waiting for API lifespan handshake...")
            if not mcp_bridge_ready.wait(timeout=30):
                logger.warning(
                    "API lifespan handshake not received within 30s — "
                    "worker starting anyway (API startup may still be in progress)"
                )
            else:
                logger.info("MCP bridge wired, worker proceeding")

            logger.info("Worker thread starting (StreamConsumerManager + SchedulerLoop)")
            _component_health["worker"] = {"status": "running"}
            try:
                loop.run_until_complete(
                    asyncio.gather(
                        stream_consumer.run(user_ids=user_ids),
                        scheduler.run(),
                        return_exceptions=True,
                    )
                )
            except Exception:
                _component_health["worker"] = {"status": "crashed"}
                logger.exception("Worker thread crashed")

        worker_thread = threading.Thread(target=run_worker, daemon=True)
        worker_thread.start()

    uvicorn.run(
        "src.api.app:app",
        host=settings.host,
        port=settings.port,
        reload=settings.debug,
    )


if __name__ == "__main__":
    main()
