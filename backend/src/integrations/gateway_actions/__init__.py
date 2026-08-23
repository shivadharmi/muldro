"""Single source of truth for the gateway's OpenConnector providers and actions.

Each ``GatewayAction`` is the one place an action's policy (capability, risk,
approval) and its hand-typed input schema are declared -- OpenConnector's
runtime ``get_action_guide`` exposes no machine-readable schema (see
infra/gateway/spike-findings-guide.md and spike-findings-multiprovider.md).
Each ``GatewayProvider`` binds a set of actions to the OC service that executes
them and the Muldro installation that serves them.

Everything downstream DERIVES from this registry and restates nothing:
adapter enforcement profiles, warm-start tool schemas, catalog seeds, MCP
routing, and the platform-JWT capability mint. This is the north-star
verb -> capability + risk policy table.
"""

from __future__ import annotations

from types import MappingProxyType

from src.integrations.gateway_actions._types import GatewayAction, GatewayProvider
from src.integrations.gateway_actions.atlassian import CONFLUENCE, JIRA
from src.integrations.gateway_actions.github import GITHUB
from src.integrations.gateway_actions.gmail import GMAIL
from src.integrations.gateway_actions.googlecalendar import GOOGLECALENDAR
from src.integrations.gateway_actions.notion import NOTION

# GatewayAction/GatewayProvider are defined in _types and re-exported here so
# consumers import the registry and its types from one place.
__all__ = [
    "ACTION_BY_ID",
    "PROVIDER_REGISTRY",
    "GatewayAction",
    "GatewayProvider",
    "capabilities_for_server",
    "gateway_provider_for_source",
    "perception_sources_for_provider",
    "provider_labels_for_server",
    "provider_of_action",
    "providers_for_server",
]

# Registry order is load-bearing: providers_for_server() returns it verbatim, so
# a server's providers are connected (and their tools listed) in this order.
_PROVIDERS: tuple[GatewayProvider, ...] = (GMAIL, GOOGLECALENDAR, GITHUB, NOTION, JIRA, CONFLUENCE)

PROVIDER_REGISTRY: MappingProxyType[str, GatewayProvider] = MappingProxyType(
    {p.provider_id: p for p in _PROVIDERS}
)

# Flat action lookup. Provider resolution goes through MEMBERSHIP in this map --
# never by splitting an action_id on its first dot -- so an unregistered id such
# as "gmail.attacker_action" fails on a dict miss instead of being parsed into a
# plausible provider from a caller-supplied prefix.
ACTION_BY_ID: MappingProxyType[str, GatewayAction] = MappingProxyType(
    {a.action_id: a for p in _PROVIDERS for a in p.actions}
)

_PROVIDER_BY_ACTION: MappingProxyType[str, str] = MappingProxyType(
    {a.action_id: p.provider_id for p in _PROVIDERS for a in p.actions}
)


def _build_perception_source_index() -> MappingProxyType[str, str]:
    """Invert every provider's ``perception_sources`` into source -> provider.

    Built with an explicit loop rather than a comprehension so a source claimed
    by two providers raises at import instead of silently collapsing to whichever
    provider happens to come last in registry order -- "which credential backs
    this source" has exactly one answer or the registry is wrong.
    """
    index: dict[str, str] = {}
    for provider in _PROVIDERS:
        for source in provider.perception_sources:
            owner = index.get(source)
            if owner is not None:
                raise ValueError(
                    f"perception source {source!r} is claimed by both "
                    f"{owner!r} and {provider.provider_id!r}"
                )
            index[source] = provider.provider_id
    return MappingProxyType(index)


_PROVIDER_BY_PERCEPTION_SOURCE: MappingProxyType[str, str] = _build_perception_source_index()


def provider_of_action(action_id: str) -> str | None:
    """Return the provider owning ``action_id``, or None if unregistered."""
    return _PROVIDER_BY_ACTION.get(action_id)


def gateway_provider_for_source(source: str) -> str | None:
    """Return the OC provider whose credential backs a perception source.

    ``None`` means the source is NOT gateway-backed and its runnability is still
    an OAuthManager question. Resolution is by MEMBERSHIP in the registry-derived
    index -- never by munging the source name -- because the two vocabularies do
    not line up (source "calendar" -> provider "googlecalendar") and a
    name-derived guess would silently invent providers.
    """
    return _PROVIDER_BY_PERCEPTION_SOURCE.get(source)


def perception_sources_for_provider(provider_id: str) -> tuple[str, ...]:
    """Return the perception sources an OC provider backs (empty if none/unknown)."""
    provider = PROVIDER_REGISTRY.get(provider_id)
    return provider.perception_sources if provider else ()


def providers_for_server(server_name: str) -> tuple[str, ...]:
    """Return the OC provider ids served by a Muldro installation, in registry order."""
    return tuple(p.provider_id for p in _PROVIDERS if p.server_name == server_name)


def capabilities_for_server(server_name: str) -> tuple[str, ...]:
    """Union of capabilities across a server's providers, sorted and deduplicated.

    This is what ``session_pool._resolve_auth`` mints into the platform JWT, so a
    GitHub session's token carries no email capability and vice versa.

    Derived from ``providers_for_server`` rather than re-filtering ``_PROVIDERS``,
    so the server -> provider binding is decided in exactly one place.
    """
    caps = {
        a.capability
        for provider_id in providers_for_server(server_name)
        for a in PROVIDER_REGISTRY[provider_id].actions
    }
    return tuple(sorted(caps))


def provider_labels_for_server(server_name: str) -> dict[str, str]:
    """Map each of a server's OC provider ids to its registry ``display_name``.

    Composed from ``providers_for_server`` so the server -> provider binding is
    decided in exactly one place. Consumers (e.g. the unified integrations API)
    use this instead of hand-maintaining a provider -> label table that silently
    degrades to a raw slug when a provider is added.
    """
    return {
        provider_id: PROVIDER_REGISTRY[provider_id].display_name
        for provider_id in providers_for_server(server_name)
    }
