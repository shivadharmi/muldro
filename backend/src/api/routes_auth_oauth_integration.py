"""OAuth integration provisioning helpers used by the OAuth callback:
IntegrationInstallation upsert, schedule enablement, initial-observation
background task, and error redirect.

Extracted from routes_auth_oauth.py (decomposition, 2026-06-20)."""

import logging
from urllib.parse import urlencode

from fastapi.responses import RedirectResponse

from src.config.settings import Settings

logger = logging.getLogger(__name__)


def _mcp_servers_for_sources(sources: list[str]) -> list[str]:
    """Order-preserving, deduped MCP server names backing ``sources``.

    A perception source is not necessarily an MCP server name, and several
    sources can share one server. Eager schema discovery must key off server
    names, so translate each source through its OAuth provider via
    ``provider_map`` (the single source of truth for the source -> provider ->
    server relationship) and dedupe the result.

    Only the natively-authenticated providers reach here: the gateway-backed
    installations (google-workspace, github) are connected through
    ``routes_integrations``, not the OAuth callback.
    """
    from src.integrations.provider_map import provider_for_source, servers_for_provider

    servers: list[str] = []
    for source in sources:
        for server in servers_for_provider(provider_for_source(source)):
            if server not in servers:
                servers.append(server)
    return servers


async def _trigger_initial_observation(user_id: str, sources: list[str], workspace_id: str) -> None:
    """Run initial perception cycle and MCP schema discovery for newly connected sources."""
    try:
        from src.config.settings import get_settings
        from src.connectors.base import CONNECTOR_REGISTRY
        from src.models.database import get_session_factory
        from src.orchestrator.jarvis import JarvisOrchestrator
        from src.runtime import build as build_runtime
        from src.tools import configure_tool_servers

        settings = get_settings()
        db_factory = get_session_factory()

        # Build a short-lived ServiceContainer for this background task
        svc_db = db_factory()
        try:
            svc = build_runtime(settings, svc_db)
            # Pass None: internal tools resolve the thread-local (per-loop) session
            # factory rather than a shared global bound to another loop (Step 11 Phase 3).
            configure_tool_servers(None, settings, svc)
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

            # Eagerly create an MCP session per server to discover tool schemas.
            # Stdio servers are lazy (per-user), so schemas aren't available until
            # the first session; creating it here populates the DB so tools appear
            # in agent tool lists immediately after OAuth connection. Keyed on MCP
            # server names (not source names): gmail + calendar share the single
            # google-workspace server, so discovery runs once for it — reloading
            # "gmail"/"calendar" as servers would just warn "no active installation".
            for server_name in _mcp_servers_for_sources(sources):
                try:
                    from src.integrations.mcp_pool import get_workspace_pool

                    pool = get_workspace_pool()
                    if pool:
                        # Ensure config is registered (may have been activated
                        # after startup via this OAuth callback)
                        if not pool.session_pool.has_server_config(server_name, workspace_id):
                            await pool.reload_server(workspace_id, server_name)

                        session = await pool.session_pool.get_or_create_session(
                            server_name, user_id=user_id, workspace_id=workspace_id
                        )
                        logger.info(
                            "MCP schema discovery for %s: %d tools",
                            server_name,
                            len(session.tools),
                        )
                except Exception:
                    logger.debug("MCP schema discovery skipped for %s", server_name, exc_info=True)
        finally:
            await svc_db.close()
    except Exception:
        logger.warning("Initial observation dispatch failed", exc_info=True)


async def _register_webhooks_for_sources(
    db_factory,
    user_id: str,
    sources: list[str],
    workspace_id: str,
) -> None:
    """Best-effort push-webhook registration for newly connected sources.

    No-op unless ``settings.webhooks_configured`` (master switch + public
    callback base URL). A registration failure NEVER fails the OAuth connect —
    the source simply stays in poll mode. Idempotency is handled inside
    ``WebhookManager.register`` (reuses an existing active channel).
    """
    try:
        from src.config.settings import get_settings

        settings = get_settings()
        if not getattr(settings, "webhooks_configured", False):
            return  # poll-only deployment — nothing to do

        from src.integrations.sync.webhook_manager import WebhookManager
        from src.services.oauth_manager import OAuthManager

        oauth_mgr = OAuthManager(
            db_factory, encryption_key=settings.oauth_encryption_key, settings=settings
        )
        # Map perception sources → (provider, resource_type, resource_id).
        resource_map = {
            "gmail": ("mailbox", "me"),
            "calendar": ("calendar", "primary"),
        }
        async with db_factory() as db:
            mgr = WebhookManager(
                db,
                workspace_id,
                settings.webhook_callback_base_url,
                settings=settings,
                oauth_manager=oauth_mgr,
            )
            registered = False
            for source in sources:
                if source not in resource_map:
                    continue
                resource_type, resource_id = resource_map[source]
                try:
                    sub = await mgr.register(
                        user_id=user_id,
                        provider=source,
                        resource_type=resource_type,
                        resource_id=resource_id,
                    )
                    registered = True
                    logger.info(
                        "webhook_registered_on_connect",
                        extra={
                            "provider": source,
                            "subscription_id": sub.subscription_id,
                            "status": sub.status,
                        },
                    )
                except Exception:
                    logger.warning(
                        "Webhook registration failed for %s/%s; staying poll-only",
                        user_id,
                        source,
                        exc_info=True,
                    )
            if registered:
                await db.commit()
    except Exception:
        # Connect must never fail because of webhook setup.
        logger.warning("Webhook registration dispatch failed", exc_info=True)


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
