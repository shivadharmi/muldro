"""Entry point for running the Jarvis backend server."""

import argparse
import logging

import uvicorn

from src.config.logging import configure_logging
from src.config.settings import get_settings

# Cross-thread gate: set by the FastAPI lifespan after MCP bridge init, waited on
# by the worker thread before processing tasks. Sourced from a normal ``src.``
# module (NOT defined here) so the lifespan's ``from src.runtime_signals import``
# and this script's ``__main__`` bind the SAME Event object — see runtime_signals.
from src.runtime_signals import mcp_bridge_ready

_component_health: dict[str, dict] = {
    "worker": {"status": "not_started"},
}


def get_component_health() -> dict:
    """Get health status of the worker component."""
    return dict(_component_health)


def _ensure_worker_mcp_bridge(loop, settings, logger) -> None:
    """Ensure the MCP bridge is initialized in the worker's own process/loop.

    The session pool is a module-level global, shared across threads in ONE
    process but NOT across processes. Under ``uvicorn --reload`` (debug) the
    FastAPI lifespan runs in a forked CHILD process, so the global it sets is
    invisible to the worker thread living in the PARENT — every MCP tool call
    from the worker then fails with "MCP bridge not initialized".

    This bridges that gap by initializing the bridge in the worker's process
    when it has not already been wired:

    - Non-debug (single process): the lifespan already set the global, so
      ``get_session_pool()`` is non-None and we no-op (and ``initialize_mcp_bridge``
      is idempotent even if called).
    - Debug/reload: the parent global is None, so the worker wires its own pool.

    Never raises — failures are logged and the worker proceeds.
    """
    try:
        from src.connectors.mcp_bridge import get_session_pool, initialize_mcp_bridge

        if get_session_pool() is not None:
            return

        # Mirror src/api/app.py: a lightweight OAuthManager for MCP token
        # resolution. Failure leaves oauth_manager=None (bridge still wires).
        oauth_manager = None
        try:
            from src.models.database import get_session_factory
            from src.services.oauth_manager import OAuthManager

            oauth_manager = OAuthManager(
                db_factory=get_session_factory(),
                settings=settings,
                encryption_key=settings.oauth_encryption_key,
            )
        except Exception:
            logger.warning("OAuthManager unavailable for worker MCP bridge", exc_info=True)

        loop.run_until_complete(
            initialize_mcp_bridge(oauth_manager=oauth_manager, timeout_seconds=30)
        )
        logger.info("Worker initialized MCP bridge in-process (debug/reload split)")
    except Exception:
        logger.error(
            "Worker MCP bridge initialization FAILED — external MCP tool calls "
            "(perception + operator) will fail until startup is retried",
            exc_info=True,
        )


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
                from src.runtime import build_shared
                from src.tools import configure_tool_servers

                db_factory = get_session_factory()

                async def _build():
                    # Session-free shared services; DB-bound services are built
                    # per operation against a fresh session. The scheduler runs
                    # perception cycles concurrently (asyncio.gather), so the
                    # worker orchestrator must NOT hold a shared long-lived
                    # AsyncSession either (P2 #4).
                    services = build_shared(settings)
                    # Pass None: internal tools resolve the thread-local (per-loop)
                    # session factory. A shared global factory set here would be used
                    # cross-loop by the API thread and vice versa (Step 11 Phase 3).
                    configure_tool_servers(None, settings, services)

                    # Step 10C P2: build the durable worker-side AsyncPostgresSaver so the
                    # autonomous deep step-executor (run_autonomous_deep_step) persists its
                    # LangGraph checkpoints durably instead of the per-call MemorySaver.
                    # RESILIENT: any failure degrades to None (MemorySaver fallback) so a
                    # saver problem can NEVER crash the worker.
                    checkpointer_provider = None
                    try:
                        from src.deep_runtime.checkpointer import (
                            build_async_postgres_saver,
                        )

                        saver, _pool = await build_async_postgres_saver(settings.database_url)
                        # TODO(10D): the worker is a daemon thread running
                        # asyncio.gather with no clean shutdown seam, so the psycopg3
                        # pool lives for the worker's lifetime (a long-lived worker
                        # pool is acceptable). Wire _pool.close() when a worker
                        # shutdown hook lands.
                        checkpointer_provider = lambda s=saver: s  # noqa: E731
                        logger.info("[deep_runtime] worker durable checkpointer ready")
                    except Exception:
                        logger.error(
                            "[deep_runtime] worker checkpointer init failed — "
                            "autonomous deep steps fall back to MemorySaver",
                            exc_info=True,
                        )

                    return JarvisOrchestrator(
                        settings=settings,
                        db_factory=db_factory,
                        services=services,
                        checkpointer_provider=checkpointer_provider,
                    )

                orchestrator = loop.run_until_complete(_build())
                logger.info("Orchestrator initialized for scheduler")
            except Exception:
                # Fail LOUD, not silent: with no orchestrator the background +
                # approval-resume execution path is dead. Reflect the degraded
                # state in /health/readiness instead of only logging, but do
                # NOT crash — perception/streams may still be useful.
                logger.error(
                    "Orchestrator build FAILED — background tasks and "
                    "approval-resume runs will NOT execute (worker degraded)",
                    exc_info=True,
                )
                _component_health["worker"] = {"status": "degraded_no_orchestrator"}

            # Query active user IDs (scheduler) + workspace IDs (stream consumer)
            user_ids = []
            workspace_ids = []
            try:
                from sqlalchemy import select as sa_select

                from src.models.users import User, Workspace

                db_factory = get_session_factory()

                async def _get_user_ids():
                    async with db_factory() as db:
                        result = await db.execute(sa_select(User.user_id))
                        return [row[0] for row in result.all()]

                async def _get_workspace_ids():
                    async with db_factory() as db:
                        result = await db.execute(sa_select(Workspace.workspace_id))
                        return [row[0] for row in result.all()]

                user_ids = loop.run_until_complete(_get_user_ids())
                workspace_ids = loop.run_until_complete(_get_workspace_ids())
                logger.info("Worker serving %d user(s): %s", len(user_ids), user_ids)
                logger.info(
                    "Worker consuming %d workspace stream(s): %s",
                    len(workspace_ids),
                    workspace_ids,
                )
            except Exception:
                logger.warning("Could not load user/workspace IDs — worker will have no streams")

            stream_consumer = StreamConsumerManager(settings)
            scheduler = SchedulerLoop(settings, orchestrator=orchestrator, user_ids=user_ids)

            # Wait for the FastAPI lifespan to reach the point where the MCP
            # session pool is wired. Startup only registers server configs
            # (no background discovery); this handshake covers pool
            # construction + prior lifespan steps (Redis, seeds, Qdrant,
            # Neo4j). A 30s budget is generous for that work; exceeding it
            # usually means the API lifespan itself is stuck on an external
            # dependency.
            #
            # Under ``uvicorn --reload`` (settings.debug) the lifespan runs in a
            # separate child process, so this in-process Event can never be set
            # from there. Waiting would guarantee a misleading 30s timeout every
            # dev startup; skip it and proceed (the worker's own internal MCP
            # servers are already configured synchronously above).
            if settings.debug:
                logger.info(
                    "Reload mode (debug=True): API lifespan runs in a separate "
                    "process — skipping in-process MCP bridge handshake."
                )
            else:
                logger.info("Worker thread waiting for API lifespan handshake...")
                if not mcp_bridge_ready.wait(timeout=30):
                    logger.warning(
                        "API lifespan handshake not received within 30s — "
                        "worker starting anyway (API startup may still be in progress)"
                    )
                else:
                    logger.info("MCP bridge wired, worker proceeding")

            # Ensure the bridge is wired in THIS process. No-op when the
            # lifespan already set the global (single-process / non-debug);
            # the actual fix when reload forks the lifespan into a child.
            _ensure_worker_mcp_bridge(loop, settings, logger)

            logger.info("Worker thread starting (StreamConsumerManager + SchedulerLoop)")
            # Preserve the degraded marker if the orchestrator failed to build —
            # the worker still runs (perception/streams) but the resume path is
            # dead, and /health must keep reflecting that.
            if orchestrator is None:
                _component_health["worker"] = {"status": "degraded_no_orchestrator"}
            else:
                _component_health["worker"] = {"status": "running"}
            try:
                loop.run_until_complete(
                    asyncio.gather(
                        stream_consumer.run(workspace_ids=workspace_ids),
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
        # Use the modern (non-legacy) websockets implementation. The default
        # "auto" selects websockets' legacy server, which emits a per-connection
        # DeprecationWarning ("remove second argument of ws_handler") under
        # websockets >=14. "websockets-sansio" (uvicorn >=0.31) avoids it.
        ws="websockets-sansio",
    )


if __name__ == "__main__":
    main()
