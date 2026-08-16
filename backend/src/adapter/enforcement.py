"""Pure-function enforcement helpers for the Connection Context Adapter.

No DB access, no I/O, no network calls — every function here is a pure
transformation over in-memory data so it can be unit tested without any
fixtures or mocks. These helpers are the building blocks the adapter layer
composes to keep an OpenConnector-backed tool call within the Gmail
gateway's allowed surface area (allowlisted actions, a server-forced
connection identity, and secret-free payloads).
"""

import copy

# TODO: finalize against OpenConnector's Gmail catalog before wider rollout.
GMAIL_ACTION_ALLOWLIST = frozenset(
    {
        "gmail.search",
        "gmail.get_message",
        "gmail.list_messages",
        "gmail.send",
    }
)

_SECRET_KEYS = frozenset(
    {
        "access_token",
        "refresh_token",
        "client_secret",
        "api_key",
        "authorization",
        "password",
        "token",
        "id_token",
    }
)


class ActionNotAllowed(Exception):  # noqa: N818 - name fixed by adapter interface spec
    """Raised when an action is not in the Gmail gateway's allowlist."""


def ensure_action_allowed(action_id: str) -> None:
    """Raise ActionNotAllowed if action_id is not in the allowlist."""
    if action_id not in GMAIL_ACTION_ALLOWLIST:
        raise ActionNotAllowed(f"Action not allowed: {action_id}")


def force_connection_name(args: dict, forced_name: str) -> dict:
    """Return a copy of args with connection_name forced to forced_name.

    The input dict is never mutated — this prevents a caller-supplied
    connection_name (e.g. attacker-controlled tool args) from ever reaching
    the underlying connector call.
    """
    copied = copy.deepcopy(args)
    copied["connection_name"] = forced_name
    return copied


def strip_secrets(obj):
    """Recursively return a copy of obj with secret keys removed.

    Dict keys whose lowercase form matches a known secret key name
    (access_token, refresh_token, client_secret, api_key, authorization,
    password, token, id_token) are dropped. Recurses into nested dicts and
    lists; all other values are returned as deep copies.
    """
    if isinstance(obj, dict):
        return {
            key: strip_secrets(value)
            for key, value in obj.items()
            if str(key).lower() not in _SECRET_KEYS
        }
    if isinstance(obj, list):
        return [strip_secrets(item) for item in obj]
    return copy.deepcopy(obj)
