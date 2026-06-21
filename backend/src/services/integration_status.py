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
import re
from dataclasses import dataclass, field

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

# Capability verb fragments that imply a write-level action.
_WRITE_MARKERS: tuple[str, ...] = (
    "send",
    "create",
    "update",
    "delete",
    "merge",
    "write",
    "post",
    "edit",
    "remove",
    "add",
    "set",
    "label",
    "archive",
    "move",
    "comment",
)

# Capability verb fragments that imply a read-level action.
_READ_MARKERS: tuple[str, ...] = (
    "read",
    "list",
    "search",
    "get",
    "fetch",
    "view",
)


def derive_slug(provider: str | None, server_name: str) -> str:
    """Return a stable lowercase key for brand-logo asset lookup.

    Prefers the OAuth provider when present (it is already normalized to a
    short brand key), otherwise falls back to the server_name, lowercased and
    stripped of common suffixes/separators so e.g. "google-workspace" → "google".
    """
    base = (provider or server_name or "").strip().lower()
    # Normalize separators to a single token boundary, then take the brand root.
    base = base.replace("_", "-").replace(" ", "-")
    # Drop a trailing descriptor (e.g. "google-workspace" → "google").
    root = base.split("-", 1)[0] if base else ""
    return root or base


# Split a scope string on token/verb boundaries: dots, slashes, colons,
# dashes, underscores, and whitespace. Used so markers match whole tokens
# (e.g. "send" in "email.send") instead of arbitrary substrings (so "add"
# does NOT match inside "address").
_SCOPE_TOKEN_SEP = re.compile(r"[^a-z0-9]+")


def _scope_tokens(scope: str) -> list[str]:
    """Split a raw scope string into lowercase alphanumeric tokens."""
    return [tok for tok in _SCOPE_TOKEN_SEP.split(scope.lower()) if tok]


def coarsen_scopes(scopes: list[str]) -> list[str]:
    """Coarsen capability strings into the design's ["read", "write"] subset.

    A capability contributes "write" if any of its whole tokens matches a write
    marker, and "read" if any token matches a read marker. Matching is
    token/verb-boundary aware (scopes are split on ``. / : - _`` and whitespace),
    so a marker only matches a complete token — e.g. "add" will not false-match
    inside "address". Order is deterministic: read before write. Capabilities
    whose tokens match neither marker set are ignored.
    """
    write_markers = set(_WRITE_MARKERS)
    read_markers = set(_READ_MARKERS)
    has_read = False
    has_write = False
    for scope in scopes:
        tokens = _scope_tokens(scope)
        if any(tok in write_markers for tok in tokens):
            has_write = True
        if any(tok in read_markers for tok in tokens):
            has_read = True
    result: list[str] = []
    if has_read:
        result.append("read")
    if has_write:
        result.append("write")
    return result


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
    slug: str = ""
    access_scopes: list[str] = field(default_factory=list)


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

        raw_scopes = inst.scopes_granted or []
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
                scopes=raw_scopes,
                slug=derive_slug(provider_name, inst.server_name),
                access_scopes=coarsen_scopes(raw_scopes),
            )
        )

    return results
