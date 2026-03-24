"""Typed ID generation and validation for Jarvis entities.

All IDs follow the pattern: {prefix}_{ULID}
- prefix: 2-5 char type indicator (usr, evt, mem, plan, exec, apr, etc.)
- ULID: 26-char Crockford base32 (uppercase alphanumeric, no I/L/O/U)
"""

import re

from ulid import ULID

# ULID Crockford base32: 0-9 A-H J-K M-N P-T V-Z (26 chars)
_ULID_PATTERN = re.compile(r"^[0-9A-HJKMNP-TV-Z]{26}$")

# Known prefixes and their entity types
ID_PREFIXES = {
    "usr": "user",
    "ws": "workspace",
    "evt": "event",
    "ent": "entity",
    "mem": "memory",
    "plan": "plan",
    "ptask": "plan_task",
    "exec": "execution",
    "run": "task_run",
    "step": "task_step",
    "apr": "approval",
    "sched": "schedule",
    "trg": "trigger",
    "notif": "notification",
    "art": "artifact",
    "trace": "trace",
    "span": "span",
    "mc": "model_call",
    "adl": "agent_decision_log",
    "agent": "agent",
    "route": "agent_route",
    "tool": "tool",
    "goal": "goal",
    "conv": "conversation",
    "msg": "message",
    "bs": "browser_session",
    "proc": "procedure",
    "ts": "trust_score",
    "watcher": "watcher",
    "trs": "server_trust",
    "capb": "capability_binding",
    "inst": "installation",
    "revt": "runtime_event",
    "apol": "approval_policy",
    "whsub": "webhook_subscription",
    "mcat": "mcp_server_catalog",
    "oal": "org_allowlist",
    "iaud": "integration_audit",
    "pst": "perception_state",
}


def generate_id(prefix: str) -> str:
    """Generate a typed ID: {prefix}_{ULID}."""
    return f"{prefix}_{ULID()}"


def generate_user_id() -> str:
    return generate_id("usr")


def validate_typed_id(value: str, expected_prefix: str | None = None) -> bool:
    """Validate a typed ID matches {prefix}_{ULID} format.

    Args:
        value: The ID string to validate.
        expected_prefix: If given, the prefix must match exactly.

    Returns:
        True if valid, False otherwise.
    """
    if not value or "_" not in value:
        return False

    parts = value.split("_", 1)
    if len(parts) != 2:
        return False

    prefix, ulid_part = parts

    if not prefix:
        return False

    if expected_prefix and prefix != expected_prefix:
        return False

    return bool(_ULID_PATTERN.match(ulid_part))


def validate_user_id(value: str) -> bool:
    """Validate a user ID matches usr_{ULID} format."""
    return validate_typed_id(value, expected_prefix="usr")
