"""FastMCP auth provider factory — maps provider names to FastMCP auth instances.

Built-in providers: Google, GitHub, Discord (native FastMCP support).
Custom OAuthProxy: Slack, Linear, Notion, Jira, LinkedIn, Twitter.
BearerAuth: static token for simple MCP servers.
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
    provider_type: str  # "builtin", "oauth_proxy", "bearer"
    default_scopes: list[str]
    authorize_url: str = ""
    token_url: str = ""


# Registry of all supported providers with their metadata.
SUPPORTED_PROVIDERS: dict[str, ProviderMeta] = {
    "google": ProviderMeta(
        name="google",
        display_name="Google Workspace",
        provider_type="builtin",
        default_scopes=[
            "https://mail.google.com/",
            "https://www.googleapis.com/auth/calendar",
            "https://www.googleapis.com/auth/drive",
            "https://www.googleapis.com/auth/documents",
            "https://www.googleapis.com/auth/spreadsheets",
        ],
    ),
    "github": ProviderMeta(
        name="github",
        display_name="GitHub",
        provider_type="builtin",
        default_scopes=["repo", "read:org", "read:user"],
    ),
    "discord": ProviderMeta(
        name="discord",
        display_name="Discord",
        provider_type="builtin",
        default_scopes=["identify", "guilds"],
    ),
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
    "linear": ProviderMeta(
        name="linear",
        display_name="Linear",
        provider_type="oauth_proxy",
        default_scopes=["read", "write"],
        authorize_url="https://linear.app/oauth/authorize",
        token_url="https://api.linear.app/oauth/token",
    ),
    "notion": ProviderMeta(
        name="notion",
        display_name="Notion",
        provider_type="oauth_proxy",
        default_scopes=[],
        authorize_url="https://api.notion.com/v1/oauth/authorize",
        token_url="https://api.notion.com/v1/oauth/token",
    ),
    "jira": ProviderMeta(
        name="jira",
        display_name="Jira (Atlassian)",
        provider_type="oauth_proxy",
        default_scopes=["read:jira-work", "write:jira-work", "read:jira-user"],
        authorize_url="https://auth.atlassian.com/authorize",
        token_url="https://auth.atlassian.com/oauth/token",
    ),
    "linkedin": ProviderMeta(
        name="linkedin",
        display_name="LinkedIn",
        provider_type="oauth_proxy",
        default_scopes=["openid", "profile", "w_member_social"],
        authorize_url="https://www.linkedin.com/oauth/v2/authorization",
        token_url="https://www.linkedin.com/oauth/v2/accessToken",
    ),
    "twitter": ProviderMeta(
        name="twitter",
        display_name="Twitter / X",
        provider_type="oauth_proxy",
        default_scopes=["tweet.read", "tweet.write", "users.read"],
        authorize_url="https://twitter.com/i/oauth2/authorize",
        token_url="https://api.twitter.com/2/oauth2/token",
    ),
}


def get_server_auth_provider(provider_name: str, settings: Settings) -> Any | None:
    """Create a FastMCP server-side auth provider for protecting MCP endpoints.

    These providers handle OAuth flows when external clients connect to
    Jarvis-hosted MCP servers. For client-side auth (Jarvis connecting
    to external MCP servers), use ``get_client_auth()``.

    Returns None if the provider credentials are not configured.
    """
    meta = SUPPORTED_PROVIDERS.get(provider_name)
    if not meta:
        logger.warning("Unknown auth provider: %s", provider_name)
        return None

    if meta.provider_type == "builtin":
        return _build_builtin_provider(provider_name, settings)
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


def _build_builtin_provider(provider_name: str, settings: Settings) -> Any | None:
    """Build a native FastMCP auth provider (Google, GitHub, Discord)."""
    base_url = _get_base_url(settings)

    if provider_name == "google":
        client_id = settings.google_oauth_client_id
        client_secret = settings.google_oauth_client_secret
        if not client_id or not client_secret:
            return None

        from fastmcp.server.auth.providers.google import GoogleProvider

        return GoogleProvider(
            client_id=client_id,
            client_secret=client_secret,
            base_url=base_url,
            required_scopes=SUPPORTED_PROVIDERS["google"].default_scopes,
        )

    if provider_name == "github":
        client_id = settings.github_oauth_client_id
        client_secret = settings.github_oauth_client_secret
        if not client_id or not client_secret:
            return None

        from fastmcp.server.auth.providers.github import GitHubProvider

        return GitHubProvider(
            client_id=client_id,
            client_secret=client_secret,
            base_url=base_url,
            required_scopes=SUPPORTED_PROVIDERS["github"].default_scopes,
        )

    if provider_name == "discord":
        client_id = getattr(settings, "discord_oauth_client_id", "")
        client_secret = getattr(settings, "discord_oauth_client_secret", "")
        if not client_id or not client_secret:
            return None

        from fastmcp.server.auth.providers.discord import DiscordProvider

        return DiscordProvider(
            client_id=client_id,
            client_secret=client_secret,
            base_url=base_url,
            required_scopes=SUPPORTED_PROVIDERS["discord"].default_scopes,
        )

    return None


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
        client_id = ""
        if meta.provider_type == "builtin":
            client_id = getattr(settings, f"{name}_oauth_client_id", "")
        else:
            client_id = getattr(settings, f"{name}_oauth_client_id", "")

        statuses.append({
            "provider": name,
            "display_name": meta.display_name,
            "type": meta.provider_type,
            "configured": bool(client_id),
            "scopes": meta.default_scopes,
        })
    return statuses
