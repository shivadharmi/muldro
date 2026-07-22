"""Canonical source <-> OAuth-provider <-> MCP-server mapping.

Single source of truth for the three-way relationship between:

- **perception sources** (``gmail``, ``calendar``, ``slack``, ...) — what the
  scheduler polls;
- **OAuth providers** (``google``, ``github``, ``slack``, ...) — the credential
  namespace ``OAuthManager`` stores tokens under;
- **MCP server names** (``google-workspace``, ``github``, ...) — the running
  connector processes.

Historically this logic was duplicated across
``integration_status._SERVER_TO_OAUTH_PROVIDER``,
``session_pool._infer_provider`` and ``perception_tick._provider_for_source``.
Those now delegate here so there is exactly one place to change when a provider
spans multiple sources or servers (e.g. Google → gmail + calendar →
google-workspace).
"""

from __future__ import annotations

# OAuth provider -> the perception sources it backs. Providers absent from this
# map back a single source whose name equals the provider (identity).
_PROVIDER_SOURCES: dict[str, list[str]] = {
    "google": ["gmail", "calendar"],
}

# OAuth provider -> the MCP server names it powers. Providers absent from this
# map run a single server whose name equals the provider (identity).
_PROVIDER_SERVERS: dict[str, list[str]] = {
    "google": ["google-workspace"],
    "github": ["github"],
    "slack": ["slack"],
    "notion": ["notion"],
    "atlassian": ["atlassian"],
}

# Substring fragments (matched against a normalized server name) -> provider.
# Order matters: the first matching fragment wins. Mirrors the legacy
# ``_infer_provider`` behaviour.
_SERVER_NAME_FRAGMENTS: tuple[tuple[tuple[str, ...], str], ...] = (
    (("google", "gmail", "calendar"), "google"),
    (("github",), "github"),
    (("slack",), "slack"),
    (("notion",), "notion"),
    (("jira", "atlassian", "confluence"), "atlassian"),
)


def provider_for_source(source: str) -> str:
    """Map a perception source to its OAuth provider.

    ``gmail`` and ``calendar`` share the ``google`` provider; every other
    source maps to a provider of the same name.
    """
    for provider, sources in _PROVIDER_SOURCES.items():
        if source in sources:
            return provider
    return source


def sources_for_provider(provider: str) -> list[str]:
    """Return the perception sources backed by ``provider``.

    ``google`` fans out to ``["gmail", "calendar"]``; every other provider
    backs a single source of the same name.
    """
    return list(_PROVIDER_SOURCES.get(provider, [provider]))


def servers_for_provider(provider: str) -> list[str]:
    """Return the MCP server names powered by ``provider``.

    ``google`` runs ``["google-workspace"]``; every other provider runs a
    single server of the same name.
    """
    return list(_PROVIDER_SERVERS.get(provider, [provider]))


def provider_for_server(server_name: str) -> str:
    """Infer the OAuth provider from an MCP server name.

    Falls back to the (unmodified) server name when no fragment matches, so
    no-auth servers (e.g. playwright) map to themselves.
    """
    name_lower = server_name.lower().replace("-", "_")
    for fragments, provider in _SERVER_NAME_FRAGMENTS:
        if any(fragment in name_lower for fragment in fragments):
            return provider
    return server_name
