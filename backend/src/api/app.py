"""FastAPI application — Jarvis backend entry point."""

import logging
from contextlib import asynccontextmanager

from fastapi import FastAPI
from fastapi.middleware.cors import CORSMiddleware

from src.api.routes_approvals import router as approvals_router
from src.api.routes_artifacts import router as artifacts_router
from src.api.routes_auth import router as auth_router
from src.api.routes_briefings import router as briefings_router
from src.api.routes_chat import router as chat_router
from src.api.routes_conversations import router as conversations_router
from src.api.routes_events import router as events_router
from src.api.routes_feedback import router as feedback_router
from src.api.routes_graph import router as graph_router
from src.api.routes_health import router as health_router
from src.api.routes_history import router as history_router
from src.api.routes_insights import router as insights_router
from src.api.routes_integrations import router as integrations_router
from src.api.routes_jwks import router as jwks_router
from src.api.routes_knowledge import router as knowledge_router
from src.api.routes_mcp import router as mcp_router
from src.api.routes_meetings import router as meetings_router
from src.api.routes_memories import router as memories_router
from src.api.routes_metrics import router as metrics_router
from src.api.routes_notifications import router as notifications_router
from src.api.routes_observation import router as observation_router
from src.api.routes_plans import router as plans_router
from src.api.routes_realtime import router as realtime_router
from src.api.routes_runtime import router as runtime_router
from src.api.routes_search import router as search_router
from src.api.routes_settings import router as settings_router
from src.api.routes_surface_detail import router as surface_detail_router
from src.api.routes_system import router as system_router
from src.api.routes_traces import router as traces_router
from src.api.routes_trust import router as trust_router
from src.api.routes_ui import router as ui_router
from src.api.routes_webhooks import router as webhooks_router
from src.api.routes_workspace_settings import router as workspace_settings_router
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
        # Fail fast on misconfiguration (covers `uvicorn app:app` started outside
        # run.py). Idempotent with run.py's check; raising here aborts startup.
        settings.validate_startup()

        # Startup: connect to Redis
        try:
            import redis.asyncio as aioredis

            app.state.redis = aioredis.from_url(settings.redis_url, decode_responses=True)
            await app.state.redis.ping()
            logger.info("Redis connected: %s", settings.redis_url)
        except Exception:
            logger.warning("Redis unavailable — using in-memory fallback for rate limiting")
            app.state.redis = None

        # Initialize the durable deep-runtime checkpointer (Step 6A.5).
        app.state.deep_checkpointer = None
        app.state.deep_checkpointer_pool = None
        app.state.deep_checkpointer_degraded = False
        try:
            from src.deep_runtime.checkpointer import build_async_postgres_saver

            saver, pool = await build_async_postgres_saver(settings.database_url)
            app.state.deep_checkpointer = saver
            app.state.deep_checkpointer_pool = pool
            logger.info("[deep_runtime] durable checkpointer ready at lifespan")
        except Exception:
            logger.error(
                "[deep_runtime] checkpointer init failed — falling back to MemorySaver",
                exc_info=True,
            )
            app.state.deep_checkpointer_degraded = True

        # Initialize surface registry
        from src.services.surface_registry import SurfaceRegistry

        app.state.surface_registry = SurfaceRegistry(redis=app.state.redis)

        # Seed global defaults (tools, agents, routes) — not per-user.
        # Per-user defaults (schedules, trust records, installations) are
        # provisioned at signup via workspace_provisioner.provision_workspace().
        # Each seed runs independently so one failure doesn't block the others.
        try:
            from src.models.database import get_session_factory
            from src.services.agent_registry import AgentRegistry
            from src.services.tool_registry import ToolRegistry

            async with get_session_factory()() as db:
                needs_commit = False

                try:
                    tool_count = await ToolRegistry(db).seed_defaults()
                    if tool_count:
                        needs_commit = True
                        logger.info("Seeded %d tool definitions", tool_count)
                except Exception:
                    logger.warning("Tool seed failed (FK or schema issue)", exc_info=True)

                try:
                    agent_count = await AgentRegistry(db).seed_defaults()
                    if agent_count:
                        needs_commit = True
                        logger.info("Seeded %d agent definitions", agent_count)
                except Exception:
                    logger.warning("Agent seed failed", exc_info=True)

                if needs_commit:
                    await db.commit()
        except Exception:
            logger.debug(
                "Registry seed skipped (DB not ready)",
                exc_info=True,
            )

        # Re-seed integration installations for all workspaces.
        # Installation configs (transport, auth_provider, remote_url) change with
        # code updates but the DB records persist from initial provisioning.
        # This ensures existing workspaces pick up config changes on restart.
        try:
            from sqlalchemy import select as sa_select

            from src.integrations.seed_installations import seed_installations
            from src.models.database import get_session_factory as _get_isf
            from src.models.users import Workspace

            async with _get_isf()() as db:
                result = await db.execute(sa_select(Workspace))
                workspaces = result.scalars().all()
                total_updated = 0
                for ws in workspaces:
                    count = await seed_installations(db, ws.workspace_id, ws.owner_user_id)
                    total_updated += count
                if total_updated:
                    await db.commit()
                import sys

                print(
                    f"[STARTUP] Installation re-seed: {total_updated} updated"
                    f" across {len(workspaces)} workspaces",
                    file=sys.stderr,
                    flush=True,
                )
        except Exception as _reseed_err:
            import sys
            import traceback

            print(
                f"[STARTUP] Installation re-seed FAILED: {_reseed_err}",
                file=sys.stderr,
                flush=True,
            )
            traceback.print_exc(file=sys.stderr)

        # Validate tool registry consistency. Fail closed: a malformed (or
        # un-validatable) registry must not serve traffic. Operators can bypass
        # the whole check with JARVIS_SKIP_REGISTRY_VALIDATION=true in emergencies.
        if settings.skip_registry_validation:
            logger.warning("Registry validation SKIPPED (JARVIS_SKIP_REGISTRY_VALIDATION=true)")
        else:
            try:
                from src.tools.validation import validate_registry

                errors = validate_registry()
            except Exception as exc:
                # The validation harness itself failed to run (import/IO error).
                # A registry we couldn't validate is not trusted — abort startup.
                logger.error("Registry validation failed to run", exc_info=True)
                raise RuntimeError("Registry validation could not run") from exc

            if errors:
                for err in errors:
                    logger.error("Registry validation: %s", err)
                raise RuntimeError(
                    f"Registry validation found {len(errors)} error(s) — fix them or set "
                    "JARVIS_SKIP_REGISTRY_VALIDATION=true to bypass."
                )
            logger.info("Registry validation passed")

        # Post-condition coverage gate (spec §4.5): every IRREVERSIBLE write
        # capability must have a registered read-back post-condition (or be
        # explicitly marked UNVERIFIABLE). Fail closed — a new write capability must
        # not serve traffic able to silently skip verification on the irreversible
        # path. Same emergency bypass as registry validation.
        if settings.skip_registry_validation:
            logger.warning("Post-condition coverage check SKIPPED (skip_registry_validation)")
        else:
            try:
                from src.services.verification.post_conditions import (
                    validate_post_condition_coverage,
                )
                from src.services.verification.predicate import write_capabilities

                pc_errors = validate_post_condition_coverage(write_capabilities())
            except Exception as exc:
                logger.error("Post-condition coverage check failed to run", exc_info=True)
                raise RuntimeError("Post-condition coverage check could not run") from exc

            if pc_errors:
                for err in pc_errors:
                    logger.error("Post-condition coverage: %s", err)
                raise RuntimeError(
                    f"Post-condition coverage found {len(pc_errors)} error(s) — register a "
                    "post-condition or mark UNVERIFIABLE, or set "
                    "JARVIS_SKIP_REGISTRY_VALIDATION=true to bypass."
                )
            logger.info("Post-condition coverage passed")

        # Identity coverage gate (spec §6 Step-3 carry-forward): every IRREVERSIBLE
        # write capability must have a deliberate idempotency-key strategy (semantic
        # IdentitySpec or explicit positional-accepted). Fail closed — a new write
        # capability must not serve traffic able to silently fall back to positional
        # keying unnoticed. Same emergency bypass as registry validation.
        if settings.skip_registry_validation:
            logger.warning("Identity coverage check SKIPPED (skip_registry_validation)")
        else:
            try:
                from src.services.idempotency.identity import validate_identity_coverage_strict
                from src.services.verification.predicate import (
                    is_irreversible_capability,
                    write_capabilities,
                )

                irreversible = {c for c in write_capabilities() if is_irreversible_capability(c)}
                id_errors = validate_identity_coverage_strict(irreversible)
            except Exception as exc:
                logger.error("Identity coverage check failed to run", exc_info=True)
                raise RuntimeError("Identity coverage check could not run") from exc

            if id_errors:
                for err in id_errors:
                    logger.error("Identity coverage: %s", err)
                raise RuntimeError(
                    f"Identity coverage found {len(id_errors)} error(s) — add an IdentitySpec "
                    "or list the capability in POSITIONAL_KEY_ACCEPTED, or set "
                    "JARVIS_SKIP_REGISTRY_VALIDATION=true to bypass."
                )
            logger.info("Identity coverage passed")

        # Ensure Qdrant collections exist
        try:
            from src.services.vector_store import VectorStore

            vector_store = VectorStore(settings)
            await vector_store.ensure_collections()
            await vector_store.ensure_indexes()
            app.state.vector_store = vector_store
        except Exception:
            logger.debug("Qdrant collection init skipped", exc_info=True)
            app.state.vector_store = None

        # Initialize GraphEngine persistent instance
        try:
            from src.services.graph_engine import GraphEngine

            if settings.neo4j_url:
                app.state.graph_engine = GraphEngine(settings)
            else:
                app.state.graph_engine = None
        except Exception:
            logger.debug("GraphEngine init skipped", exc_info=True)
            app.state.graph_engine = None

        # Create OAuthManager for MCP bridge token resolution.
        # Lightweight instance (db_factory + encryption key, no state).
        oauth_manager = None
        try:
            from src.models.database import get_session_factory as _get_sf
            from src.services.oauth_manager import OAuthManager

            oauth_manager = OAuthManager(
                db_factory=_get_sf(),
                settings=settings,
                encryption_key=settings.oauth_encryption_key,
            )
        except Exception:
            logger.warning("OAuthManager unavailable for MCP bridge", exc_info=True)

        # Initialize MCP bridge: register server configs synchronously. No eager
        # discovery — sessions and tool schemas are created lazily on first use.
        mcp_bridge_ok = False
        try:
            from src.connectors.mcp_bridge import (
                get_session_pool,
                initialize_mcp_bridge,
            )

            await initialize_mcp_bridge(
                oauth_manager=oauth_manager,
                timeout_seconds=30,
            )
            mcp_bridge_ok = get_session_pool() is not None
        except Exception:
            logger.error("MCP bridge initialization failed", exc_info=True)

        # Signal worker/bot threads — only on success. Lying about readiness
        # is worse than a 30s timeout in the consumers: a set event with a
        # broken bridge makes workers proceed and hit silent "not initialized"
        # failures on every tool call.
        if mcp_bridge_ok:
            try:
                # Shared ``src.`` module so this binds the SAME Event the worker
                # waits on. Importing ``from run`` would load run.py a second time
                # (it ran as __main__) and set a different Event — see
                # runtime_signals for the full rationale.
                from src.runtime_signals import mcp_bridge_ready

                mcp_bridge_ready.set()
            except ImportError:
                pass  # Not running via run.py (e.g., direct uvicorn or tests)
        else:
            logger.error(
                "MCP bridge not ready — mcp_bridge_ready signal suppressed; "
                "worker/bot will time out on handshake and every external "
                "MCP tool call will fail until startup is retried"
            )

        yield

        # Shutdown: close MCP bridge
        try:
            from src.connectors.mcp_bridge import shutdown_mcp_bridge

            await shutdown_mcp_bridge()
        except Exception:
            pass

        # Shutdown: release the process-wide orchestrator — await its background
        # tasks and close the shared Redis client opened by build_shared. (No
        # long-lived DB session to close: DB-bound services are per-request.)
        try:
            from src.api.routes_chat import shutdown_orchestrator

            await shutdown_orchestrator()
            logger.info("Orchestrator shut down")
        except Exception:
            logger.debug("Orchestrator shutdown failed", exc_info=True)

        # Shutdown: close the deep-runtime psycopg3 pool (if opened at startup).
        if getattr(app.state, "deep_checkpointer_pool", None):
            try:
                await app.state.deep_checkpointer_pool.close()
                logger.info("[deep_runtime] checkpointer pool closed")
            except Exception:
                logger.debug("deep_checkpointer_pool close failed", exc_info=True)

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

    # --- Error boundary: standard envelope, no raw exceptions to clients ---
    from src.api.error_handlers import register_exception_handlers

    register_exception_handlers(app)

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
            # Explicit allow-lists (no `*`): browsers only get cross-origin access
            # to the verbs and headers the frontend actually uses.
            allow_methods=["GET", "POST", "PUT", "PATCH", "DELETE", "OPTIONS"],
            allow_headers=["Authorization", "Content-Type", "Accept"],
        )

    # Health check
    @app.get("/v1/health", response_model=HealthResponse)
    async def health():
        return HealthResponse()

    # Core product routes
    app.include_router(chat_router, tags=["chat"])
    app.include_router(briefings_router, tags=["briefings"])
    app.include_router(approvals_router, tags=["approvals"])
    app.include_router(search_router, tags=["search"])
    app.include_router(meetings_router, tags=["meetings"])
    app.include_router(feedback_router, tags=["feedback"])

    # Event ingestion
    app.include_router(events_router, tags=["events"])

    # Webhook ingestion
    app.include_router(webhooks_router, tags=["webhooks"])

    # Observation health tracking
    app.include_router(observation_router, tags=["observations"])

    # System routes (heartbeat, maintenance, metrics)
    app.include_router(system_router, tags=["system"])

    # OAuth authentication callbacks
    app.include_router(auth_router, tags=["auth"])

    # JWKS (no /v1 prefix — well-known endpoints must be root-level)
    app.include_router(jwks_router, tags=["auth"])

    # A2UI surface state REST endpoints
    app.include_router(ui_router, tags=["ui"])

    # Surface detail modal tabs
    app.include_router(surface_detail_router, tags=["surface-detail"])

    # WebSocket for real-time A2UI surface streaming
    app.include_router(ws_router, tags=["websocket"])

    # System health dashboard
    app.include_router(health_router, tags=["health"])

    # User settings
    app.include_router(settings_router, tags=["settings"])

    # Trust management
    app.include_router(trust_router, tags=["trust"])

    # Prometheus metrics
    app.include_router(metrics_router, tags=["metrics"])

    # Memories
    app.include_router(memories_router, tags=["memories"])

    # Traces (observability — internal debugging)
    app.include_router(traces_router, tags=["traces"])

    # Conversations (chat session persistence)
    app.include_router(conversations_router, tags=["conversations"])

    # Artifacts
    app.include_router(artifacts_router, tags=["artifacts"])

    # Realtime SSE streaming
    app.include_router(realtime_router, tags=["realtime"])

    # Notifications
    app.include_router(notifications_router, tags=["notifications"])

    # Plans (plan tracking)
    app.include_router(plans_router, tags=["plans"])

    # History (unified run history with retry / cancel / resume)
    app.include_router(history_router, tags=["history"])

    # Knowledge graph (Neo4j)
    app.include_router(graph_router, tags=["graph"])

    # Knowledge page (graph + memories + stats)
    app.include_router(knowledge_router, tags=["knowledge"])

    # Integration platform
    app.include_router(integrations_router, tags=["integrations"])
    app.include_router(mcp_router, tags=["mcp"])

    # Runtime projections
    app.include_router(runtime_router, tags=["runtime"])

    # Insight surfaces (dismiss + execute)
    app.include_router(insights_router, tags=["insights"])
    app.include_router(workspace_settings_router, tags=["workspace-settings"])

    return app


app = create_app()
