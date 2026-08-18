"""Shared integration connection-status resolution.

Single source of truth for "is this integration actually connected?" — used by
both the `/v1/integrations/unified` route and the daily briefing generator so
the briefing can distinguish "connected but quiet" from "not connected".

The logic joins `IntegrationControlPlane.list_installations()` (the catalog of
installed MCP servers for a workspace) with `OAuthManager` token status (whether
a usable OAuth token exists for the user). OAuth-backed integrations are only
"connected" when both the provider is configured AND a valid token is present;
local/token integrations are treated as connected when installed.

Gateway-backed installations are the exception: their credential lives inside
OpenConnector, not in `OAuthManager`, so consulting the token store would report
them permanently disconnected. For those, connectivity is read from the
`connection_map` table instead — per OC provider, so a partially connected
installation stays visible.
"""

import logging
import re
from dataclasses import dataclass, field

from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from src.integrations.gateway_actions import capabilities_for_server, providers_for_server
from src.integrations.provider_map import provider_for_server
from src.models.connection_map import DEFAULT_ACCOUNT_ALIAS, ConnectionMap

logger = logging.getLogger(__name__)

# Provider name -> settings attribute holding the OAuth client_id.
_PROVIDER_CLIENT_ID_ATTR: dict[str, str] = {
    "google": "google_oauth_client_id",
    "github": "github_oauth_client_id",
    "slack": "slack_oauth_client_id",
    "notion": "notion_oauth_client_id",
    "atlassian": "atlassian_oauth_client_id",
}

# Server auth_provider -> OAuth provider name for token lookup is resolved via
# the canonical `src.integrations.provider_map` (provider_for_server). Kept here
# only as documentation of which providers carry a client_id attr above.

# OAuthManager token-reason values that mean the credential is permanently
# unusable and the user must reconnect (vs. transient "refresh_failed").
_PERMANENT_REAUTH_REASONS: frozenset[str] = frozenset({"no_token", "no_refresh_token", "revoked"})

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
    # True when an OAuth integration is configured but its token is permanently
    # unusable (no_token / no_refresh_token / revoked) — the user must reconnect.
    # Distinct from a transient "refresh_failed" blip, which leaves this False.
    needs_reauth: bool = False
    # Every OC provider this installation serves, in registry order (empty for
    # non-gateway installations), plus each provider's own connection state so a
    # partially connected installation is reported per provider rather than
    # collapsed into one "disconnected".
    oc_providers: list[str] = field(default_factory=list)
    provider_connections: dict[str, bool] = field(default_factory=dict)


async def active_connection_providers(
    db: AsyncSession,
    workspace_id: str,
    user_id: str,
    providers: tuple[str, ...],
) -> set[str]:
    """Return which of ``providers`` this principal can actually resolve.

    Scope enforced: ``workspace_id`` + ``principal_id`` + ``provider_id`` +
    the default ``account_alias``, and ``connection_status == "active"``.

    That scope MIRRORS ``src.adapter.connection_resolver.resolve_connection`` on
    purpose: "connected" must mean "the resolver will resolve this". A narrower
    query (workspace + provider only) reported connected for rows the gateway
    then denied — another workspace member's connection, or a genuinely active
    connection stored under a non-default alias (``alias`` is a client-supplied
    body field on the connect route, so it is reachable through the public API).

    The resolver matches ``tenant_id`` where this matches ``workspace_id``; the
    platform JWT is minted with ``tenant_id = workspace_id`` (see
    ``session_pool._resolve_auth``), so the two coincide today. The alias
    constant is shared with the resolver rather than restated, so they cannot
    drift apart.

    One query per installation (not per provider): the whole provider set is
    matched with a single ``IN``. Only ``connection_status == "active"`` counts —
    a "pending"/"revoked"/"error" row is not a usable connection.

    Public because the perception tick's runnability gate
    (``scheduler/perception_tick.py``) must decide "connected" the SAME way this
    module does; a second copy of this query is exactly how the two definitions
    drifted apart before.
    """
    rows = await db.execute(
        select(ConnectionMap.provider_id).where(
            ConnectionMap.workspace_id == workspace_id,
            ConnectionMap.principal_id == user_id,
            ConnectionMap.provider_id.in_(providers),
            ConnectionMap.account_alias == DEFAULT_ACCOUNT_ALIAS,
            ConnectionMap.connection_status == "active",
        )
    )
    return set(rows.scalars().all())


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
        needs_reauth = False
        oc_providers: list[str] = []
        provider_connections: dict[str, bool] = {}
        # Display-only scopes: the installation's hand-maintained
        # `scopes_granted` list, unless the gateway branch below derives them.
        raw_scopes = inst.scopes_granted or []

        # An empty tuple means "not gateway-backed" and falls through to the
        # OAuth path below, so the `all({}) is True` vacuous-truth case can never
        # be reached: `connected = all(...)` only runs when `providers` is
        # non-empty, and then `provider_connections` has one entry per provider.
        providers = providers_for_server(inst.server_name)

        if providers:
            # Gateway-backed: the credential lives in OpenConnector, not
            # OAuthManager, so connectivity is the per-provider connection_map
            # state. Reported per provider so a partially connected installation
            # (Gmail linked, Calendar declined) stays visible rather than
            # collapsing to "disconnected". `configured` stays True: the gateway
            # owns the OAuth client, not a Jarvis-side client_id setting.
            active = await active_connection_providers(db, workspace_id, user_id, providers)
            provider_connections = {p: (p in active) for p in providers}
            connected = all(provider_connections.values())
            oc_providers = list(providers)
            # A gateway installation carries no `scopes_granted` (the list was
            # hand-maintained in Jarvis vocabulary); the registry already knows
            # exactly which capabilities its providers expose, so the badges
            # come from there instead of rendering empty.
            raw_scopes = list(capabilities_for_server(inst.server_name))
        elif category == "oauth":
            oauth_name = provider_for_server(auth_provider)
            client_id_attr = _PROVIDER_CLIENT_ID_ATTR.get(oauth_name, "")
            configured = bool(getattr(settings, client_id_attr, "")) if client_id_attr else False
            connected = False
            if configured and oauth_mgr:
                try:
                    result = await oauth_mgr.get_valid_token_with_reason(user_id, oauth_name)
                    connected = result.reason == "ok" and result.token is not None
                    needs_reauth = result.reason in _PERMANENT_REAUTH_REASONS
                except Exception:
                    connected = False

        # Determine provider name for the frontend.
        provider_name: str | None = None
        if auth_provider and auth_provider not in ("token", "none"):
            provider_name = provider_for_server(auth_provider)

        # Every gateway installation declares the same auth_provider
        # ("platform_jwt"), so deriving the brand slug from it collapses them
        # all into "platform" — a collision between google-workspace and
        # github. Derive from the server_name for those, which stays distinct.
        slug = derive_slug(None if providers else provider_name, inst.server_name)

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
                slug=slug,
                access_scopes=coarsen_scopes(raw_scopes),
                needs_reauth=needs_reauth,
                oc_providers=oc_providers,
                provider_connections=provider_connections,
            )
        )

    return results
