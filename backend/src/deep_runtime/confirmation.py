"""Presence, and what it means for a turn's authority (single-lead cutover).

Two facts travel with a chat turn and NEITHER is derived from the other:

* ``permission_mode`` — ``auto`` | ``ask`` | ``bypass``. *Which* writes need a human.
* ``presence``        — ``present`` | ``absent``. *Whether* a human is reachable on this turn.

Before this module those two were conflated into one transport boolean (``can_pause``), which
meant a fact about the response channel was silently deciding how writes got gated. Naming them
separately is the point: ``presence`` may only ever DOWNGRADE authority, and there is no path by
which it grants something ``permission_mode`` did not — see ``tests/test_confirmation.py``, which
asserts that exhaustively rather than by example.

``bypass`` means "do not interrupt me". That promise is only meaningful when there is a *me* on
the turn, so an absent turn downgrades it to ``auto``. ``bypass`` is transitional in any case:
fence it to a present user and build nothing new on it.

This module is DELIBERATELY dependency-light — no DB, no settings, no ``src.orchestrator`` import.
It is the shared vocabulary the chat processor and (in a later task) both write gates speak.
"""

from __future__ import annotations

from typing import Literal

Presence = Literal["present", "absent"]

PRESENT: Presence = "present"
ABSENT: Presence = "absent"

# The permission modes the chat path understands. Anything else fails CLOSED to ``ask``.
_KNOWN_MODES = ("bypass", "ask", "auto")
_FAIL_CLOSED_MODE = "ask"


def resolve_effective_permission_mode(
    permission_mode: str | None,
    presence: str,
    *,
    bypass_entitled: bool,
) -> str:
    """Resolve the EFFECTIVE permission mode, applying FAIL-SAFE downgrades only.

    SECURITY — this decides whether and how a write is gated. Every branch here either keeps
    the requested mode or moves it to a STRICTER one; none escalates.

    * an unknown or blank mode → ``ask`` (confirm every write);
    * ``bypass`` without a present user → ``auto``;
    * ``bypass`` without the workspace entitlement → ``auto``.

    Pure and synchronous on purpose: the entitlement needs a DB read, so the caller resolves it
    and passes it in. That keeps the whole policy one exhaustively testable table instead of a
    branch buried in an async method.
    """
    if permission_mode not in _KNOWN_MODES:
        return _FAIL_CLOSED_MODE
    if permission_mode != "bypass":
        return permission_mode
    if presence != PRESENT:
        return "auto"
    if not bypass_entitled:
        return "auto"
    return "bypass"
