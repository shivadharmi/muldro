"""Entry point for running the Jarvis backend server."""

import argparse
import logging

import uvicorn

from src.config.logging import configure_logging
from src.config.settings import get_settings


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
        from src.services.worker import CallbackWorker, StreamConsumerManager

        logger = logging.getLogger("jarvis.worker_thread")

        def run_worker():
            loop = asyncio.new_event_loop()
            asyncio.set_event_loop(loop)

            # Build orchestrator for scheduler to use (if services available)
            orchestrator = None
            try:
                from src.models.database import get_session_factory
                from src.orchestrator.jarvis import JarvisOrchestrator
                from src.tools import intelligence_server

                db_factory = get_session_factory()
                services = _build_services(settings, db_factory)
                intelligence_server.configure(db_factory, settings, services)
                orchestrator = JarvisOrchestrator(
                    settings=settings,
                    db_factory=db_factory,
                    services=services,
                )
                logger.info("Orchestrator initialized for scheduler")
            except Exception:
                logger.warning("Orchestrator not available, scheduled actions will fail")

            callback_worker = CallbackWorker(settings)
            stream_consumer = StreamConsumerManager(settings)
            scheduler = SchedulerLoop(settings, orchestrator=orchestrator)
            logger.info(
                "Worker thread starting "
                "(CallbackWorker + StreamConsumerManager + SchedulerLoop)"
            )
            try:
                loop.run_until_complete(
                    asyncio.gather(
                        callback_worker.run(),
                        stream_consumer.run(),
                        scheduler.run(),
                        return_exceptions=True,
                    )
                )
            except Exception:
                logger.exception("Worker thread crashed")

        worker_thread = threading.Thread(target=run_worker, daemon=True)
        worker_thread.start()

    if args.bot:
        # Start Telegram bot in background thread
        import asyncio
        import threading

        bot_logger = logging.getLogger("jarvis.bot_thread")

        def run_bot():
            loop = asyncio.new_event_loop()
            asyncio.set_event_loop(loop)
            bot_logger.info("Telegram bot thread starting")
            try:
                from src.interface.telegram import TelegramInterface
                from src.models.database import get_session_factory
                from src.orchestrator.jarvis import JarvisOrchestrator
                from src.services.notifier import Notifier
                from src.services.surface_registry import SurfaceRegistry
                from src.tools import intelligence_server

                # Build service dependencies for the orchestrator
                db_factory = get_session_factory()
                services = _build_services(settings, db_factory)
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
                loop.run_forever()
            except Exception:
                bot_logger.exception("Bot thread crashed")

        bot_thread = threading.Thread(target=run_bot, daemon=True)
        bot_thread.start()

    uvicorn.run(
        "src.api.app:app",
        host=settings.host,
        port=settings.port,
        reload=settings.debug,
    )


def _build_services(settings, db_factory) -> dict:
    """Build service instances for orchestrator.

    Returns a dict of service name -> service instance.
    Services are initialized lazily to avoid import issues.
    """
    services = {}

    try:
        from src.services.event_processor import EventProcessor

        services["event_processor"] = EventProcessor(settings)
    except Exception:
        pass

    try:
        from src.services.planner import Planner

        services["planner"] = Planner(settings)
    except Exception:
        pass

    try:
        from src.services.governor import Governor

        services["governor"] = Governor(settings)
    except Exception:
        pass

    try:
        from src.services.presenter import Presenter

        services["presenter"] = Presenter(settings)
    except Exception:
        pass

    try:
        from src.services.world_model import WorldModel

        db = db_factory()
        services["world_model"] = WorldModel(settings, db)
    except Exception:
        pass

    try:
        from src.services.memory_service import MemoryService

        db = db_factory()
        services["memory_service"] = MemoryService(settings, db)
        services["memory"] = services["memory_service"]  # alias for context assembler
    except Exception:
        pass

    try:
        from src.services.audit import AuditService

        services["audit"] = AuditService()
    except Exception:
        pass

    try:
        from src.services.vector_store import VectorStore

        services["vector_store"] = VectorStore(settings)
    except Exception:
        pass

    try:
        from src.services.search_service import SearchService

        services["search_service"] = SearchService(
            settings, vector_store=services.get("vector_store")
        )
    except Exception:
        pass

    try:
        from src.services.working_memory import WorkingMemoryService

        db = db_factory()
        services["working_memory"] = WorkingMemoryService(settings, db)
    except Exception:
        pass

    try:
        from src.services.event_correlator import EventCorrelator

        db = db_factory()
        services["event_correlator"] = EventCorrelator(db)
    except Exception:
        pass

    return services


if __name__ == "__main__":
    main()
