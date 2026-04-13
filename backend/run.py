"""Entry point for running the Jarvis backend server."""

import argparse
import logging
import threading

import uvicorn

from src.config.logging import configure_logging
from src.config.settings import get_settings

_component_health: dict[str, dict] = {
    "worker": {"status": "not_started"},
    "bot": {"status": "not_started"},
}

# Cross-thread gate: set by the FastAPI lifespan after MCP bridge init,
# waited on by the worker thread before processing tasks.
mcp_bridge_ready = threading.Event()


def get_component_health() -> dict:
    """Get health status of worker and bot components."""
    return dict(_component_health)


def main():
    parser = argparse.ArgumentParser(description="Jarvis Backend")
    parser.add_argument(
        "--worker", action="store_true", help="Start background worker alongside API"
    )
    parser.add_argument("--bot", action="store_true", help="Start Telegram bot alongside API")
    args = parser.parse_args()

    settings = get_settings()
    configure_logging(json_output=settings.log_json, level=logging.INFO)

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

            # Wait for the FastAPI lifespan to initialize the MCP bridge
            # so external MCP tool calls don't hit "bridge not initialized".
            logger.info("Worker thread waiting for MCP bridge initialization...")
            if not mcp_bridge_ready.wait(timeout=60):
                logger.warning(
                    "MCP bridge ready signal not received within 60s — "
                    "worker starting anyway (external MCP tools may fail)"
                )
            else:
                logger.info("MCP bridge ready, worker proceeding")

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

    if args.bot:
        # Start Telegram bot in background thread
        import asyncio
        import threading

        bot_logger = logging.getLogger("jarvis.bot_thread")

        def run_bot():
            _component_health["bot"] = {"status": "starting"}
            loop = asyncio.new_event_loop()
            asyncio.set_event_loop(loop)
            bot_logger.info("Telegram bot thread starting")
            try:
                from src.interface.telegram import TelegramInterface
                from src.models.database import get_session_factory
                from src.orchestrator.jarvis import JarvisOrchestrator
                from src.runtime import build as build_runtime
                from src.services.notifier import Notifier
                from src.services.surface_registry import SurfaceRegistry
                from src.tools import intelligence_server

                # Build service dependencies for the orchestrator
                db_factory = get_session_factory()
                svc_db = db_factory()
                services = build_runtime(settings, svc_db)
                intelligence_server.configure(db_factory, settings, services)

                orchestrator = JarvisOrchestrator(
                    settings=settings,
                    db_factory=db_factory,
                    services=services,
                )

                # Set up surface registry and notifier
                surface_registry = SurfaceRegistry()
                bot = TelegramInterface(
                    settings,
                    orchestrator,
                    surface_registry=surface_registry,
                )
                notifier = Notifier(
                    surface_registry=surface_registry,
                    telegram_sender=bot.send_message,
                )
                bot._notifier = notifier

                loop.run_until_complete(bot.start())
                _component_health["bot"] = {"status": "running"}
                loop.run_forever()
            except Exception:
                _component_health["bot"] = {"status": "crashed"}
                bot_logger.exception("Bot thread crashed")

        bot_thread = threading.Thread(target=run_bot, daemon=True)
        bot_thread.start()

    uvicorn.run(
        "src.api.app:app",
        host=settings.host,
        port=settings.port,
        reload=settings.debug,
    )


if __name__ == "__main__":
    main()
