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

# TODO: finalize against OpenConnector's Gmail catalog before wider rollout.
GMAIL_ACTION_ALLOWLIST = frozenset(
    {
        "gmail.search",
        "gmail.get_message",
        "gmail.list_messages",
        "gmail.send",
    }
)

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


def ensure_action_allowed(action_id: str) -> None:
    """Raise ActionNotAllowed if action_id is not in the allowlist."""
    if action_id not in GMAIL_ACTION_ALLOWLIST:
        raise ActionNotAllowed(f"Action not allowed: {action_id}")


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
