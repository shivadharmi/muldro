"""OAuth integration provisioning helpers used by the OAuth callback:
IntegrationInstallation upsert, schedule enablement, initial-observation
background task, and error redirect.

Extracted from routes_auth_oauth.py (decomposition, 2026-06-20)."""

import logging
from urllib.parse import urlencode

from fastapi.responses import RedirectResponse

from src.config.settings import Settings

logger = logging.getLogger(__name__)


async def _trigger_initial_observation(user_id: str, sources: list[str], workspace_id: str) -> None:
    """Run initial perception cycle and MCP schema discovery for newly connected sources."""
    try:
        from src.config.settings import get_settings
        from src.connectors.base import CONNECTOR_REGISTRY
        from src.models.database import get_session_factory
        from src.orchestrator.jarvis import JarvisOrchestrator
        from src.runtime import build as build_runtime
        from src.tools import intelligence_server

        settings = get_settings()
        db_factory = get_session_factory()

        # Build a short-lived ServiceContainer for this background task
        svc_db = db_factory()
        try:
            svc = build_runtime(settings, svc_db)
            intelligence_server.configure(db_factory, settings, svc)
            orchestrator = JarvisOrchestrator(
                settings=settings,
                db_factory=db_factory,
                services=svc,
            )
            for source in sources:
                # MCP-only integrations (e.g., Atlassian) don't have a native
                # CONNECTOR_REGISTRY entry — their data flows entirely through
                # external MCP servers. Running a perception cycle against such
                # sources just emits "No connector registered" warnings and a
                # misleading perception_poll_failed log. Skip perception for
                # them; MCP schema discovery below still runs.
                if source not in CONNECTOR_REGISTRY:
                    logger.info(
                        "Skipping perception cycle for MCP-only source %s/%s",
                        user_id,
                        source,
                    )
                else:
                    try:
                        await orchestrator.run_perception_cycle(
                            source, user_id=user_id, workspace_id=workspace_id
                        )
                        logger.info("Initial observation completed for %s/%s", user_id, source)
                    except Exception:
                        logger.warning(
                            "Initial observation failed for %s/%s",
                            user_id,
                            source,
                            exc_info=True,
                        )

                # Eagerly create MCP session to discover tool schemas.
                # Stdio servers are lazy (per-user), so schemas aren't
                # available until the first session. Creating it here
                # populates the DB so tools appear in agent tool lists
                # immediately after OAuth connection.
                try:
                    from src.integrations.mcp_pool import get_workspace_pool

                    pool = get_workspace_pool()
                    if pool:
                        # Ensure config is registered (may have been activated
                        # after startup via this OAuth callback)
                        if not pool.session_pool.has_server_config(source, workspace_id):
                            await pool.reload_server(workspace_id, source)

                        session = await pool.session_pool.get_or_create_session(
                            source, user_id=user_id, workspace_id=workspace_id
                        )
                        logger.info(
                            "MCP schema discovery for %s: %d tools",
                            source,
                            len(session.tools),
                        )
                except Exception:
                    logger.debug("MCP schema discovery skipped for %s", source, exc_info=True)
        finally:
            await svc_db.close()
    except Exception:
        logger.warning("Initial observation dispatch failed", exc_info=True)


async def _ensure_integration(
    db_factory,
    user_id: str,
    provider: str,
    account_email: str | None = None,
    workspace_id: str = "",
    extra_config: dict | None = None,
) -> None:
    """Create or reactivate an IntegrationInstallation after OAuth."""
    from sqlalchemy import select as sa_select

    from src.models.ids import generate_id
    from src.models.integration_installation import IntegrationInstallation

    # Build config dict from account_email and any extra provider-specific data
    config: dict = {}
    if account_email:
        config["account_email"] = account_email
    if extra_config:
        config.update(extra_config)

    try:
        async with db_factory() as db:
            result = await db.execute(
                sa_select(IntegrationInstallation).where(
                    IntegrationInstallation.user_id == user_id,
                    IntegrationInstallation.server_name == provider,
                    IntegrationInstallation.workspace_id == workspace_id,
                )
            )
            existing = result.scalar_one_or_none()
            if existing:
                existing.status = "active"
                existing.enabled = True
                # Merge new config into existing (preserves other fields)
                merged = dict(existing.config or {})
                merged.update(config)
                existing.config = merged
            else:
                db.add(
                    IntegrationInstallation(
                        install_id=generate_id("inst"),
                        user_id=user_id,
                        workspace_id=workspace_id,
                        server_name=provider,
                        display_name=provider.replace("_", " ").title(),
                        transport="native",
                        auth_provider="oauth",
                        status="active",
                        health_status="unknown",
                        config=config,
                        enabled=True,
                    )
                )
            await db.commit()

        # Enable schedules tied to this integration (observation + globals on first)
        await _enable_integration_schedules(db_factory, provider, workspace_id=workspace_id)
    except Exception:
        logger.warning("Failed to ensure integration %s for %s", provider, user_id, exc_info=True)


async def _enable_integration_schedules(
    db_factory,
    provider: str,
    workspace_id: str = "",
) -> None:
    """Enable seeded schedules when an integration is authorized."""
    try:
        from src.services.schedule_seeder import enable_schedules_for_connector

        async with db_factory() as db:
            enabled = await enable_schedules_for_connector(
                db,
                provider,
                workspace_id=workspace_id,
            )
            if enabled:
                await db.commit()
    except Exception:
        logger.debug("Failed to enable schedules for %s", provider, exc_info=True)


def _error_redirect(settings: Settings, message: str) -> RedirectResponse:
    """Redirect to frontend with an error message."""
    frontend_url = settings.frontend_url.rstrip("/")
    params = urlencode({"error": message})
    return RedirectResponse(url=f"{frontend_url}/integrations?{params}")
