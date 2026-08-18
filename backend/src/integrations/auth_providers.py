"""FastMCP auth provider factory — maps provider names to FastMCP auth instances.

Custom OAuthProxy: Slack, Notion, Atlassian (Jira + Confluence).
BearerAuth: static token for simple MCP servers.

Google and GitHub are deliberately absent: both are served by the OpenConnector
gateway (see ``src.integrations.gateway_actions``), which owns their OAuth
clients and credentials. Registering them here would advertise a native connect
path that nothing reads.
"""

from __future__ import annotations

import logging
from dataclasses import dataclass
from typing import TYPE_CHECKING, Any

from fastmcp.client.auth import BearerAuth

if TYPE_CHECKING:
    from src.config.settings import Settings

logger = logging.getLogger(__name__)


@dataclass(frozen=True)
class ProviderMeta:
    """Metadata for a supported OAuth provider."""

    name: str
    display_name: str
    provider_type: str  # "oauth_proxy", "bearer"
    default_scopes: list[str]
    authorize_url: str = ""
    token_url: str = ""


# Registry of all supported providers with their metadata.
SUPPORTED_PROVIDERS: dict[str, ProviderMeta] = {
    "slack": ProviderMeta(
        name="slack",
        display_name="Slack",
        provider_type="oauth_proxy",
        default_scopes=[
            "channels:read",
            "channels:history",
            "chat:write",
            "users:read",
        ],
        authorize_url="https://slack.com/oauth/v2/authorize",
        token_url="https://slack.com/api/oauth.v2.access",
    ),
    "notion": ProviderMeta(
        name="notion",
        display_name="Notion",
        provider_type="oauth_proxy",
        default_scopes=[],
        authorize_url="https://api.notion.com/v1/oauth/authorize",
        token_url="https://api.notion.com/v1/oauth/token",
    ),
    "atlassian": ProviderMeta(
        name="atlassian",
        display_name="Atlassian (Jira + Confluence)",
        provider_type="oauth_proxy",
        default_scopes=[
            # offline_access + read:me are mandatory for Atlassian's Remote
            # MCP (mcp.atlassian.com); without them, every tool call fails
            # with the opaque "having trouble" wrapper.
            "offline_access",
            "read:me",
            "read:jira-work",
            "write:jira-work",
            "read:jira-user",
            "manage:jira-project",
            "read:confluence-content.all",
            "read:confluence-content.summary",
            "write:confluence-content",
            "read:confluence-space.summary",
            "read:confluence-props",
            "write:confluence-props",
            "read:confluence-user",
            "read:confluence-groups",
            "search:confluence",
        ],
        authorize_url="https://auth.atlassian.com/authorize",
        token_url="https://auth.atlassian.com/oauth/token",
    ),
}


def get_server_auth_provider(provider_name: str, settings: Settings) -> Any | None:
    """Create a FastMCP server-side auth provider for protecting MCP endpoints.

    These providers handle OAuth flows when external clients connect to
    Muldro-hosted MCP servers. For client-side auth (Muldro connecting
    to external MCP servers), use ``get_client_auth()``.

    Returns None if the provider credentials are not configured.
    """
    meta = SUPPORTED_PROVIDERS.get(provider_name)
    if not meta:
        logger.warning("Unknown auth provider: %s", provider_name)
        return None

    if meta.provider_type == "oauth_proxy":
        return _build_oauth_proxy(provider_name, meta, settings)

    return None


def get_client_auth(
    provider_name: str,
    access_token: str | None = None,
) -> Any | None:
    """Create a FastMCP client-side auth for connecting TO external MCP servers.

    If an access_token is provided, returns BearerAuth.
    Otherwise returns "oauth" string to trigger browser-based flow.
    """
    if access_token:
        return BearerAuth(token=access_token)
    return "oauth"


def _build_oauth_proxy(
    provider_name: str,
    meta: ProviderMeta,
    settings: Settings,
) -> Any | None:
    """Build a custom OAuthProxy for providers without native FastMCP support."""
    client_id = getattr(settings, f"{provider_name}_oauth_client_id", "")
    client_secret = getattr(settings, f"{provider_name}_oauth_client_secret", "")

    if not client_id or not client_secret:
        return None

    base_url = _get_base_url(settings)

    from fastmcp.server.auth import OAuthProxy
    from fastmcp.server.auth.providers.jwt import JWTVerifier

    # OAuthProxy requires a token_verifier; use a permissive JWTVerifier
    # that accepts tokens issued by the upstream provider.
    verifier = JWTVerifier(
        issuer=meta.authorize_url.split("/oauth")[0] if meta.authorize_url else None,
        base_url=base_url,
    )

    return OAuthProxy(
        upstream_authorization_endpoint=meta.authorize_url,
        upstream_token_endpoint=meta.token_url,
        upstream_client_id=client_id,
        upstream_client_secret=client_secret,
        token_verifier=verifier,
        base_url=base_url,
        valid_scopes=meta.default_scopes or None,
    )


def _get_base_url(settings: Settings) -> str:
    """Resolve the public base URL for OAuth redirects."""
    # Check for explicit base_url setting first
    base_url = getattr(settings, "base_url", "")
    if base_url:
        return base_url
    # Fallback: derive from redirect URIs
    redirect = getattr(settings, "google_oauth_redirect_uri", "")
    if redirect:
        # "http://localhost:8000/v1/auth/google/callback" → "http://localhost:8000"
        parts = redirect.split("/v1/")
        if parts:
            return parts[0]
    return "http://localhost:8000"


def get_provider_status(settings: Settings) -> list[dict]:
    """Return status of all supported providers (configured/not configured)."""
    statuses = []
    for name, meta in SUPPORTED_PROVIDERS.items():
        client_id = getattr(settings, f"{name}_oauth_client_id", "")

        statuses.append(
            {
                "provider": name,
                "display_name": meta.display_name,
                "type": meta.provider_type,
                "configured": bool(client_id),
                "scopes": meta.default_scopes,
            }
        )
    return statuses
