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
spans multiple sources or servers.

Scope note: this map answers for **natively authenticated** providers only. The
gateway-backed brands (Google, GitHub) are answered by the registry in
``src.integrations.gateway_actions`` instead, which is why the two fan-out maps
below are asymmetric — see each one's comment.
"""

from __future__ import annotations

from dataclasses import dataclass


@dataclass(frozen=True)
class NativePerception:
    """A provider's OWN OAuth token, and what that token buys.

    ``purpose`` is a short human phrase the integrations UI renders next to the
    credential. It lives here, beside the sources, so a provider cannot acquire
    a second credential without also saying what it is for -- and so no reader
    downstream has to name a brand to find out.
    """

    sources: tuple[str, ...]
    purpose: str


# OAuth provider -> the perception sources it backs. Providers absent from this
# map back a single source whose name equals the provider (identity).
#
# ``github`` is listed even though it is identity-shaped, because being listed is
# what makes the pause/resume path SEE it. The GitHub notifications poll runs on
# a native OAuth token again (``GitHubConnector`` against /notifications), so a
# revoked token must be able to pause the source and a reconnect must resume it.
# GitHub's gateway credential is a separate thing serving the github.* MCP
# actions; it never reaches this map.
#
# The one entry this map used to carry was ``google -> [gmail, calendar]``,
# retired when those sources moved behind the OpenConnector gateway. Both
# readers are native-only paths that gmail and calendar can no longer reach:
#
# * ``provider_for_source`` — called by ``connector_poller`` and
#   ``perception_tick`` AFTER ``gateway_provider_for_source`` has already
#   short-circuited every gateway-backed source, so it only ever sees a native
#   one; and by ``routes_auth_oauth_integration``, which serves the native
#   OAuth callback only.
# * ``sources_for_provider`` — called by ``ReauthService`` to pause/resume a
#   provider's sources. Its upstreams (``apply_needs_reauth`` /
#   ``clear_reauth``) are reachable only for natively-authenticated providers:
#   ``McpAuthRequiredError`` is raised solely on the stdio-token and
#   OAuthManager branches of ``session_pool._resolve_auth``, and a
#   ``platform_jwt`` installation (google-workspace, github) returns before
#   either. That branch ORDER is pinned executably by
#   ``tests/integrations/test_session_pool_auth.py::
#   test_platform_jwt_branch_returns_a_bearer_without_raising_reauth`` — if it
#   goes red, that ordering has changed and the github MCP server would start
#   raising reauth against this entry; fix the order, not this map.
#
# Add an entry the moment a native OAuth provider backs a perception source
# whose name differs from it, or backs more than one — without it those sources
# are invisible to the pause/resume path.
_PROVIDER_NATIVE_PERCEPTION: dict[str, NativePerception] = {
    "github": NativePerception(sources=("github",), purpose="notifications"),
}

# Derived, so the sources are declared exactly once above.
_PROVIDER_SOURCES: dict[str, list[str]] = {
    provider: list(native.sources) for provider, native in _PROVIDER_NATIVE_PERCEPTION.items()
}

# OAuth provider -> the MCP server names it powers. Providers absent from this
# map run a single server whose name equals the provider (identity). ``google``
# and ``github`` were retired from here when their MCP servers moved behind the
# OpenConnector gateway — there is no longer a native OAuth provider owning
# those servers, so the registry in ``src.integrations.gateway_actions`` answers
# "which server serves this?" instead.
_PROVIDER_SERVERS: dict[str, list[str]] = {
    "slack": ["slack"],
    "notion": ["notion"],
    "atlassian": ["atlassian"],
}

# Substring fragments (matched against a normalized server name) -> provider.
# Order matters: the first matching fragment wins. Mirrors the legacy
# ``_infer_provider`` behaviour.
#
# The ``google`` fragment outlives ``_PROVIDER_SOURCES``'s ``google`` entry on
# purpose: it still labels the google-workspace server for the deep runtime's
# per-session ``unavailable_server`` cache, which keys on providers and never
# touches perception sources. The one place the two would meet is
# ``dag_runner._defer_for_reauth`` (``provider_for_server`` ->
# ``apply_needs_reauth`` -> ``sources_for_provider``); that pairing is
# unreachable today because google-workspace authenticates with a platform JWT
# and so cannot raise ``McpAuthRequiredError``. If a gateway installation ever
# does surface one, restore the ``google`` entry above — otherwise gmail and
# calendar would silently not be paused.
_SERVER_NAME_FRAGMENTS: tuple[tuple[tuple[str, ...], str], ...] = (
    (("google", "gmail", "calendar"), "google"),
    (("github",), "github"),
    (("slack",), "slack"),
    (("notion",), "notion"),
    (("jira", "atlassian", "confluence"), "atlassian"),
)


def provider_for_source(source: str) -> str:
    """Map a perception source to its OAuth provider.

    A source listed under a fan-out provider in ``_PROVIDER_SOURCES`` maps to
    that provider; every other source maps to a provider of the same name.
    """
    for provider, sources in _PROVIDER_SOURCES.items():
        if source in sources:
            return provider
    return source


def native_perception_for_provider(provider: str) -> NativePerception | None:
    """Return what ``provider`` polls on its OWN OAuth token, or None.

    Unlike ``sources_for_provider`` this has NO identity fallback, and the
    difference is the whole point: the fallback answers "yes, one source" for
    every provider ever named, including the fully gateway-backed ones, so a
    caller asking "does this provider hold a second, native credential?" would
    get a yes for all of them. Only an explicit declaration above counts.
    """
    return _PROVIDER_NATIVE_PERCEPTION.get(provider)


def sources_for_provider(provider: str) -> list[str]:
    """Return the perception sources backed by ``provider``.

    A provider listed in ``_PROVIDER_SOURCES`` fans out to its sources; every
    other provider backs a single source of the same name.
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

    Falls back to the (unmodified) server name when no fragment matches, so a
    no-auth server maps to itself. Every seeded server needs auth today; this
    fallback exists for admin-registered ones.
    """
    name_lower = server_name.lower().replace("-", "_")
    for fragments, provider in _SERVER_NAME_FRAGMENTS:
        if any(fragment in name_lower for fragment in fragments):
            return provider
    return server_name
