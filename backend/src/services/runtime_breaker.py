"""Redis keyspace for the Step 10B/10D runtime-cutover gate.

Owns the key names + the read/write helpers so the effective-runtime gate
(``runtime_gate.py``) and the Phase 5 watcher (not yet built) share ONE
keyspace — nobody else should hand-format ``jarvis:runtime:...`` strings.

Borrows the CLOSED/OPEN/cooldown SHAPE of
``src.orchestrator.api_circuit_breaker.AnthropicCircuitBreaker`` but is
net-new, not a subclass or reuse of that object: this breaker is
surface-keyed (``chat`` | ``perception`` | ``autonomous``, not per-model) and
Redis-backed (cross-process — the gate, the watcher, and any manual escape
hatch all run in different processes), whereas ``AnthropicCircuitBreaker`` is
in-memory and per-process. Same shape, different substrate.
"""

from __future__ import annotations

from typing import Any

VALID_SURFACES = ("chat", "perception", "autonomous")


def _key(kind: str, surface: str) -> str:
    return f"jarvis:runtime:{kind}:{surface}"


def override_key(surface: str) -> str:
    """Manual escape-hatch key — highest-priority tier in ``effective_runtime``."""
    return _key("override", surface)


def breaker_key(surface: str) -> str:
    """Auto-tripped breaker key — set by the Phase 5 watcher on a live regression."""
    return _key("breaker", surface)


def enabled_key(surface: str) -> str:
    """Rollout-enable key — flips a surface to ``"deep"`` ahead of a static-settings flip."""
    return _key("enabled", surface)


async def read_key(redis: Any, kind: str, surface: str) -> str | None:
    """GET one of the three tiered keys, decoding bytes to str.

    Returns ``None`` both when the key is missing AND when the Redis call
    itself raises — callers (``effective_runtime``) treat "no key" and "Redis
    error" identically, so a Redis outage always falls through to the next
    tier instead of propagating an exception into the gate.
    """
    try:
        value = await redis.get(_key(kind, surface))
    except Exception:
        return None
    if value is None:
        return None
    return value.decode() if isinstance(value, bytes) else value


async def trip(redis: Any, surface: str) -> None:
    """Trip the breaker to the proven-safe direction ("legacy").

    Phase 5 adds cooldown/opened_at bookkeeping on top of this minimal SET.
    """
    await redis.set(breaker_key(surface), "legacy")


async def clear(redis: Any, surface: str) -> None:
    """Clear a tripped breaker."""
    await redis.delete(breaker_key(surface))


async def breaker_state(redis: Any, surface: str) -> str | None:
    """Current breaker value for ``surface`` (``"legacy"`` if tripped, else ``None``)."""
    return await read_key(redis, "breaker", surface)


async def set_manual_override(redis: Any, surface: str, target: str = "legacy") -> None:
    """Set the manual escape-hatch override (Step 10B Phase 5 Task 5b).

    The override key is the HIGHEST-priority tier in ``effective_runtime`` — it wins
    over even a tripped breaker or an enable key. ``surface="all"`` fans out to every
    surface in ``VALID_SURFACES``, forcing the whole system to ``target`` in one call
    (the "everything's on fire" case).
    """
    if surface == "all":
        for s in VALID_SURFACES:
            await redis.set(override_key(s), target)
        return
    await redis.set(override_key(surface), target)


async def clear_manual_override(redis: Any, surface: str) -> None:
    """Clear the manual override for ``surface`` (or every surface, for ``"all"``),
    restoring resolution to the breaker/enabled/static tiers."""
    if surface == "all":
        for s in VALID_SURFACES:
            await redis.delete(override_key(s))
        return
    await redis.delete(override_key(surface))
