"""Entry point for running the Jarvis backend server."""

import argparse
import logging

import uvicorn

from src.config.settings import get_settings

logging.basicConfig(
    level=logging.INFO,
    format="%(asctime)s %(levelname)s %(name)s: %(message)s",
)


def main():
    parser = argparse.ArgumentParser(description="Jarvis Backend")
    parser.add_argument(
        "--worker", action="store_true", help="Start background worker alongside API"
    )
    args = parser.parse_args()

    settings = get_settings()

    if args.worker:
        # Start worker in background thread alongside API
        import asyncio
        import threading

        from src.services.scheduler import SchedulerLoop
        from src.services.worker import CallbackWorker

        logger = logging.getLogger("jarvis.worker_thread")

        def run_worker():
            loop = asyncio.new_event_loop()
            asyncio.set_event_loop(loop)
            worker = CallbackWorker(settings)
            scheduler = SchedulerLoop(settings)
            logger.info("Worker thread starting (CallbackWorker + SchedulerLoop)")
            try:
                loop.run_until_complete(
                    asyncio.gather(
                        worker.run(), scheduler.run(), return_exceptions=True
                    )
                )
            except Exception:
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
