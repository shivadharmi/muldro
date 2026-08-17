"""Pure-function enforcement helpers for the Connection Context Adapter.

No DB access, no I/O, no network calls — every function here is a pure
transformation over in-memory data so it can be unit tested without any
fixtures or mocks. These helpers are the building blocks the adapter layer
composes to keep an OpenConnector-backed tool call within its provider's
allowed surface area (allowlisted actions, a server-forced connection
identity, and secret-free payloads).

One adapter process now serves a reviewed SET of providers (spec decision
D2), not a single hardcoded one — every action resolves its OWN policy
profile from its OWN action_id (``profile_for_action``), fail-closed on an
unregistered id. There is no default profile: a caller that forgets to
resolve one gets a ``TypeError`` at the call site, not a silent check
against the wrong provider's allowlist.
"""

import copy
import re
from collections.abc import Mapping
from dataclasses import dataclass
from types import MappingProxyType

from src.integrations.gateway_actions import (
    ACTION_BY_ID,
    PROVIDER_REGISTRY,
    GatewayAction,
    provider_of_action,
)


class ActionNotAllowed(Exception):  # noqa: N818 - name fixed by adapter interface spec
    """Raised when an action is not in any registered provider's allowlist."""


class CapabilityDenied(Exception):  # noqa: N818 - matches ActionNotAllowed/ConnectionDenied
    """Raised when the principal lacks the capability an action requires."""


@dataclass(frozen=True)
class GatewayProfile:
    """The per-provider policy surface the adapter enforces.

    One adapter serves a reviewed provider SET; every action resolves its own
    profile from its own action_id (see profile_for_action), fail-closed on an
    unknown action. Allowlist and capability map are DERIVED from ``actions`` so
    they can never drift from the registry.
    """

    provider_id: str
    actions: tuple[GatewayAction, ...]

    @property
    def action_allowlist(self) -> frozenset[str]:
        return frozenset(a.action_id for a in self.actions)

    @property
    def action_required_capability(self) -> Mapping[str, str]:
        return MappingProxyType({a.action_id: a.capability for a in self.actions})


# Derived from the single source of truth (gateway_actions.PROVIDER_REGISTRY) so the
# allowlist, capability map, warm-start schemas, and catalog seeds never drift.
#
# NOTE: this boundary check is only as tight as the minted token. Today
# ``session_pool._resolve_auth`` mints a blanket capability list for every
# gateway JWT; the mint is being made registry-derived (via
# ``gateway_actions.capabilities_for_server``, a per-server union) so the
# send-vs-read distinction bites in practice — the mint must become
# step-scoped (carrying only the capabilities the current step needs) for
# this gate to be fully load-bearing. Tracked as part of the connect-flow /
# per-step-JWT work; enforcing here is the correct boundary regardless of how
# the token is currently scoped.
_PROFILES: dict[str, GatewayProfile] = {
    provider_id: GatewayProfile(provider_id=provider_id, actions=provider.actions)
    for provider_id, provider in PROVIDER_REGISTRY.items()
}


def get_gateway_profile(provider: str) -> GatewayProfile:
    """Return the reviewed profile for ``provider``; fail-closed on unknown."""
    profile = _PROFILES.get(provider)
    if profile is None:
        raise ValueError(f"No gateway profile for provider {provider!r}")
    return profile


def profile_for_action(action_id: str) -> GatewayProfile:
    """Resolve the profile owning ``action_id``. Fail-closed on unregistered ids.

    Resolution is by MEMBERSHIP in the registry, never by splitting on the first
    dot, so a caller-supplied prefix cannot select a profile.
    """
    provider = provider_of_action(action_id)
    if provider is None or action_id not in ACTION_BY_ID:
        raise ActionNotAllowed(f"Action not allowed: {action_id}")
    return _PROFILES[provider]


# Secret key names in NORMALIZED form: lowercased with every non-alphanumeric
# character removed. strip_secrets normalizes each response key the same way,
# so snake_case, camelCase, and kebab-case variants all collapse to one form
# (access_token / accessToken / access-token -> "accesstoken"). This closes a
# camelCase blind spot: OpenConnector speaks camelCase (confirmed via the
# Task 0 spike), so a naive snake_case-only match would leak `accessToken` etc.
_SECRET_KEYS = frozenset(
    {
        "accesstoken",
        "refreshtoken",
        "idtoken",
        "sessiontoken",
        "clientsecret",
        "apikey",
        "authorization",
        "password",
        "token",
        "secret",
        "bearer",
        "credential",
        "privatekey",
    }
)


def _normalize_key(key: object) -> str:
    """Lowercase a key and strip non-alphanumerics for secret matching."""
    return re.sub(r"[^a-z0-9]", "", str(key).lower())


def ensure_action_allowed(action_id: str, profile: GatewayProfile) -> None:
    """Raise ActionNotAllowed if action_id is not in the profile's allowlist."""
    if action_id not in profile.action_allowlist:
        raise ActionNotAllowed(f"Action not allowed: {action_id}")


def ensure_capability_allowed(
    action_id: str,
    capabilities: tuple[str, ...],
    profile: GatewayProfile,
) -> None:
    """Raise CapabilityDenied unless the principal is authorized for action_id.

    ``capabilities`` is the principal's granted capability list (from the
    verified platform JWT). Fail-closed on two paths: an action with no
    required-capability mapping is denied, and an action whose mapped
    capability is not present in ``capabilities`` is denied.
    """
    required = profile.action_required_capability.get(action_id)
    if required is None or required not in capabilities:
        raise CapabilityDenied(
            f"Principal not authorized for action {action_id!r} (requires capability {required!r})"
        )


def force_connection_name(args: dict, forced_name: str) -> dict:
    """Return a copy of args with ``connectionName`` forced to forced_name.

    OpenConnector's ``execute_action`` tool uses the camelCase key
    ``connectionName`` (confirmed via the Task 0 spike). The input dict is
    never mutated — this prevents a caller-supplied ``connectionName`` (e.g.
    attacker-controlled tool args) from ever reaching the connector call.

    ``forced_name`` must be non-empty: an empty ``connectionName`` makes
    OpenConnector silently fall back to its default connection, which in the
    shared-instance model is a cross-tenant path. Reject it fail-closed.
    """
    if not forced_name or not forced_name.strip():
        raise ValueError("forced_name must be a non-empty connection name")
    copied = copy.deepcopy(args)
    copied["connectionName"] = forced_name
    return copied


def strip_secrets(obj):
    """Recursively return a copy of obj with secret-named keys removed.

    Keys are matched by NORMALIZED name (lowercased, non-alphanumerics
    stripped), so `access_token`, `accessToken`, and `access-token` all drop.
    Recurses into nested dicts and lists; other values are deep copied.

    Limitation: matching is by key NAME only, not value shape — a secret
    embedded inside a value (e.g. `?access_token=...` in a URL string) is not
    detected. OpenConnector returns connection *summaries*, not raw tokens
    (confirmed via the spike), so key-name stripping is the belt-and-suspenders
    guard; value scanning is deferred (see spec GA prerequisites).
    """
    if isinstance(obj, dict):
        return {
            key: strip_secrets(value)
            for key, value in obj.items()
            if _normalize_key(key) not in _SECRET_KEYS
        }
    if isinstance(obj, list):
        return [strip_secrets(item) for item in obj]
    return copy.deepcopy(obj)
