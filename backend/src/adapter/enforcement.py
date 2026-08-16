"""Pure-function enforcement helpers for the Connection Context Adapter.

No DB access, no I/O, no network calls — every function here is a pure
transformation over in-memory data so it can be unit tested without any
fixtures or mocks. These helpers are the building blocks the adapter layer
composes to keep an OpenConnector-backed tool call within the Gmail
gateway's allowed surface area (allowlisted actions, a server-forced
connection identity, and secret-free payloads).
"""

import copy
import re
from dataclasses import dataclass

# TODO: finalize against OpenConnector's Gmail catalog before wider rollout.
GMAIL_ACTION_ALLOWLIST = frozenset(
    {
        "gmail.search",
        "gmail.get_message",
        "gmail.list_messages",
        "gmail.send",
    }
)

# Each allowlisted action's REQUIRED Jarvis capability. The allowlist proves an
# action is a known Gmail action; this map proves the *caller* was authorized
# for it. ``ensure_capability_allowed`` rejects any call whose principal was
# not granted the mapped capability, so a read-scoped token can never invoke
# ``gmail.send``. Fail-closed: an action absent from this map is denied.
#
# NOTE: this boundary check is only as tight as the minted token. Today
# ``session_pool._resolve_auth`` mints a blanket ``["email.search","email.send"]``
# for every gateway JWT, so the send-vs-read distinction does not yet bite in
# practice — the mint must become step-scoped (carrying only the capabilities
# the current step needs) for this gate to be load-bearing. Tracked as part of
# the connect-flow / per-step-JWT work; enforcing here is the correct boundary
# regardless of how the token is currently scoped.
ACTION_REQUIRED_CAPABILITY = {
    "gmail.search": "email.search",
    "gmail.get_message": "email.read",
    "gmail.list_messages": "email.list",
    "gmail.send": "email.send",
}


@dataclass(frozen=True)
class GatewayProfile:
    """The per-provider policy surface the adapter enforces for one instance.

    An adapter instance serves exactly ONE provider (matching the per-provider
    vMCP design). The profile bundles the three provider-specific values the
    boundary needs: which provider connections are resolved, which actions are
    allowlisted, and each action's required capability. These are CODE-defined
    and reviewed — the active provider is *selected* by a setting, but the
    allowlist itself is never env-injected (that would let ops widen the
    boundary out-of-band).
    """

    provider_id: str
    action_allowlist: frozenset[str]
    action_required_capability: dict[str, str]


GMAIL_PROFILE = GatewayProfile(
    provider_id="gmail",
    action_allowlist=GMAIL_ACTION_ALLOWLIST,
    action_required_capability=ACTION_REQUIRED_CAPABILITY,
)

# A no-auth provider used ONLY by the automated integration harness to drive a
# real action through the full adapter over HTTP (Gmail's OAuth can't be
# headless). Read-only; the sole allowlisted action is a public HN read.
HACKERNEWS_ACTION_ALLOWLIST = frozenset(
    {
        "hackernews.get_ask_stories",
        "hackernews.get_top_stories",
    }
)
HACKERNEWS_ACTION_REQUIRED_CAPABILITY = {
    "hackernews.get_ask_stories": "hackernews.read",
    "hackernews.get_top_stories": "hackernews.read",
}
HACKERNEWS_PROFILE = GatewayProfile(
    provider_id="hackernews",
    action_allowlist=HACKERNEWS_ACTION_ALLOWLIST,
    action_required_capability=HACKERNEWS_ACTION_REQUIRED_CAPABILITY,
)

PROVIDER_PROFILES: dict[str, GatewayProfile] = {
    GMAIL_PROFILE.provider_id: GMAIL_PROFILE,
    HACKERNEWS_PROFILE.provider_id: HACKERNEWS_PROFILE,
}


def get_gateway_profile(provider: str) -> GatewayProfile:
    """Return the reviewed profile for ``provider``; fail-closed on unknown."""
    profile = PROVIDER_PROFILES.get(provider)
    if profile is None:
        raise ValueError(f"No gateway profile for provider {provider!r}")
    return profile


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


class ActionNotAllowed(Exception):  # noqa: N818 - name fixed by adapter interface spec
    """Raised when an action is not in the Gmail gateway's allowlist."""


class CapabilityDenied(Exception):  # noqa: N818 - matches ActionNotAllowed/ConnectionDenied
    """Raised when the principal lacks the capability an action requires."""


def ensure_action_allowed(action_id: str, profile: GatewayProfile = GMAIL_PROFILE) -> None:
    """Raise ActionNotAllowed if action_id is not in the profile's allowlist."""
    if action_id not in profile.action_allowlist:
        raise ActionNotAllowed(f"Action not allowed: {action_id}")


def ensure_capability_allowed(
    action_id: str,
    capabilities: tuple[str, ...],
    profile: GatewayProfile = GMAIL_PROFILE,
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
