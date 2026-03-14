"""Entry point for running the Jarvis backend server."""

import argparse

import uvicorn

from src.config.settings import get_settings


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

        def run_worker():
            loop = asyncio.new_event_loop()
            asyncio.set_event_loop(loop)
            worker = CallbackWorker(settings)
            scheduler = SchedulerLoop(settings)
            loop.run_until_complete(asyncio.gather(worker.run(), scheduler.run()))

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
