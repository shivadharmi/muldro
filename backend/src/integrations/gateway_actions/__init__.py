"""Single source of truth for the gateway's OpenConnector providers and actions.

Each ``GatewayAction`` is the one place an action's policy (capability, risk,
approval) and its hand-typed input schema are declared -- OpenConnector's
runtime ``get_action_guide`` exposes no machine-readable schema (see
infra/gateway/spike-findings-guide.md and spike-findings-multiprovider.md).
Each ``GatewayProvider`` binds a set of actions to the OC service that executes
them and the Jarvis installation that serves them.

Everything downstream DERIVES from this registry and restates nothing:
adapter enforcement profiles, warm-start tool schemas, catalog seeds, MCP
routing, and the platform-JWT capability mint. This is the north-star
verb -> capability + risk policy table.
"""

from __future__ import annotations

from types import MappingProxyType

from src.integrations.gateway_actions._types import GatewayAction, GatewayProvider
from src.integrations.gateway_actions.github import GITHUB
from src.integrations.gateway_actions.gmail import GMAIL
from src.integrations.gateway_actions.googlecalendar import GOOGLECALENDAR

# GatewayAction/GatewayProvider are defined in _types and re-exported here so
# consumers import the registry and its types from one place.
__all__ = [
    "ACTION_BY_ID",
    "PROVIDER_REGISTRY",
    "GatewayAction",
    "GatewayProvider",
    "capabilities_for_server",
    "provider_of_action",
    "providers_for_server",
]

# Registry order is load-bearing: providers_for_server() returns it verbatim, so
# a server's providers are connected (and their tools listed) in this order.
_PROVIDERS: tuple[GatewayProvider, ...] = (GMAIL, GOOGLECALENDAR, GITHUB)

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


def provider_of_action(action_id: str) -> str | None:
    """Return the provider owning ``action_id``, or None if unregistered."""
    return _PROVIDER_BY_ACTION.get(action_id)


def providers_for_server(server_name: str) -> tuple[str, ...]:
    """Return the OC provider ids served by a Jarvis installation, in registry order."""
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
