"""Per-surface effective-runtime gate (Step 10B Phase 4).

``settings.runtime`` is a plain pydantic field read once at process start —
it cannot hot-change. 10D needs to flip surfaces (``chat`` / ``perception`` /
``autonomous``) live, without a redeploy, and the Phase 5 watcher needs to be
able to trip a regressing surface back to ``"legacy"`` fast.
``effective_runtime`` is the single read path call sites use instead of
reading ``settings.runtime`` directly.

Priority (first match wins):
    1. manual override key   — human escape hatch, always wins
    2. auto-tripped breaker  — Phase 5 watcher's safety trip
    3. rollout enable key    — turns a surface on ahead of a static flip
    4. static ``settings.runtime`` — the process-start default, fail-safe floor

Fail-safe contract: the ONLY way this returns ``"deep"`` is a *successful*
read of the enable key that itself says ``"deep"``, with no override and no
tripped breaker. Any Redis error, at any tier, or ``redis is None``, resolves
to the static ``settings.runtime`` — never to an accidental ``"deep"``. In
production ``settings.runtime`` defaults to ``"legacy"``, so an outage always
degrades to the already-proven-safe runtime.
"""

from __future__ import annotations

from typing import Any

from src.services import runtime_breaker


async def effective_runtime(
    surface: str,
    *,
    redis: Any,
    settings: Any,
    cache: dict[str, str] | None = None,
) -> str:
    """Resolve the runtime a call on ``surface`` should execute on.

    ``cache`` is an optional resolve-once memo, keyed by surface. Pass the
    SAME dict across every read within one turn/tick and this returns the
    first-resolved value for that surface without touching Redis again — a
    key flipping mid-turn must never change an already-resolved decision.
    """
    if cache is not None and surface in cache:
        return cache[surface]

    resolved = await _resolve(surface, redis=redis, settings=settings)

    if cache is not None:
        cache[surface] = resolved
    return resolved


async def _resolve(surface: str, *, redis: Any, settings: Any) -> str:
    """Walk the four tiers, falling through on a missing key or a Redis error.

    ``runtime_breaker.read_key`` already swallows Redis exceptions and
    returns ``None`` for them, so a raising GET is indistinguishable here
    from a missing key — both fall through to the next tier.
    """
    if redis is None:
        return settings.runtime

    override = await runtime_breaker.read_key(redis, "override", surface)
    if override is not None:
        return override

    breaker = await runtime_breaker.read_key(redis, "breaker", surface)
    if breaker is not None:
        return breaker

    enabled = await runtime_breaker.read_key(redis, "enabled", surface)
    if enabled is not None:
        return enabled

    return settings.runtime
