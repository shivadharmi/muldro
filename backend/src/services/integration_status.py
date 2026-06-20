"""Shared integration connection-status resolution.

Single source of truth for "is this integration actually connected?" — used by
both the `/v1/integrations/unified` route and the daily briefing generator so
the briefing can distinguish "connected but quiet" from "not connected".

The logic joins `IntegrationControlPlane.list_installations()` (the catalog of
installed MCP servers for a workspace) with `OAuthManager` token status (whether
a usable OAuth token exists for the user). OAuth-backed integrations are only
"connected" when both the provider is configured AND a valid token is present;
local/token integrations are treated as connected when installed.
"""

import logging
from dataclasses import dataclass

from sqlalchemy.ext.asyncio import AsyncSession

logger = logging.getLogger(__name__)

# Provider name -> settings attribute holding the OAuth client_id.
_PROVIDER_CLIENT_ID_ATTR: dict[str, str] = {
    "google": "google_oauth_client_id",
    "github": "github_oauth_client_id",
    "slack": "slack_oauth_client_id",
    "notion": "notion_oauth_client_id",
    "atlassian": "atlassian_oauth_client_id",
}

# Server auth_provider -> OAuth provider name for token lookup.
_SERVER_TO_OAUTH_PROVIDER: dict[str, str] = {
    "google": "google",
    "github": "github",
    "slack": "slack",
    "notion": "notion",
    "atlassian": "atlassian",
}


@dataclass(frozen=True)
class IntegrationStatus:
    """Resolved connection status for a single installed integration."""

    server_name: str
    display_name: str
    provider: str | None
    category: str  # "oauth", "token", "local"
    configured: bool
    connected: bool
    health_status: str
    enabled: bool
    install_id: str | None
    scopes: list[str]


async def get_integration_statuses(
    db: AsyncSession,
    user_id: str,
    workspace_id: str,
) -> list[IntegrationStatus]:
    """Resolve connection status for every installed integration.

    Joins installation records with OAuth token state. Mirrors the logic the
    `/v1/integrations/unified` endpoint exposes to the frontend, so the briefing
    and the UI agree on what "connected" means.
    """
    from src.config.settings import Settings, get_settings
    from src.integrations.control_plane import IntegrationControlPlane
    from src.models.database import get_session_factory
    from src.services.oauth_manager import OAuthManager

    settings: Settings = get_settings()
    cp = IntegrationControlPlane(db, workspace_id)
    installations = await cp.list_installations()

    # Build OAuth manager for token checks (only when encryption key present).
    oauth_mgr: OAuthManager | None = None
    if settings.oauth_encryption_key:
        db_factory = get_session_factory()
        oauth_mgr = OAuthManager(
            db_factory,
            encryption_key=settings.oauth_encryption_key,
            settings=settings,
        )

    results: list[IntegrationStatus] = []
    for inst in installations:
        auth_provider = inst.auth_provider

        # Determine category.
        if auth_provider is None:
            category = "local"
        elif auth_provider == "token":
            category = "token"
        else:
            category = "oauth"

        # Determine configured + connected.
        configured = True
        connected = True

        if category == "oauth":
            oauth_name = _SERVER_TO_OAUTH_PROVIDER.get(auth_provider, auth_provider)
            client_id_attr = _PROVIDER_CLIENT_ID_ATTR.get(oauth_name, "")
            configured = bool(getattr(settings, client_id_attr, "")) if client_id_attr else False
            connected = False
            if configured and oauth_mgr:
                try:
                    token = await oauth_mgr.get_valid_token(user_id, oauth_name)
                    connected = token is not None
                except Exception:
                    connected = False

        # Determine provider name for the frontend.
        provider_name: str | None = None
        if auth_provider and auth_provider not in ("token", "none"):
            provider_name = _SERVER_TO_OAUTH_PROVIDER.get(auth_provider, auth_provider)

        results.append(
            IntegrationStatus(
                server_name=inst.server_name,
                display_name=inst.display_name,
                provider=provider_name,
                category=category,
                configured=configured,
                connected=connected,
                health_status=inst.health_status,
                enabled=inst.enabled,
                install_id=inst.install_id,
                scopes=inst.scopes_granted or [],
            )
        )

    return results
