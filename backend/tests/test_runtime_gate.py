"""Step 10B Phase 4: per-surface effective-runtime gate.

Real Redis, self-contained (module-level ``_redis_reachable`` skipif,
matching ``tests/test_write_lock.py`` / ``tests/test_write_lock_cross_path.py``).
Every test UUID-suffixes its surface so keys never collide with a shared
``:6379`` instance running other tests concurrently, and cleans up its own
keys in a ``finally`` block.

Priority under test: manual override > auto breaker > rollout enable key >
static ``settings.runtime``. The single most safety-critical case is
``test_redis_error_falls_back_to_static_not_deep`` — a raising Redis GET must
NEVER resolve to ``"deep"``.
"""

import uuid

import pytest
import redis.asyncio as redis_async

from src.config.settings import get_settings
from src.services import runtime_breaker
from src.services.runtime_gate import effective_runtime
from tests.conftest import make_mock_settings


def _redis_reachable() -> bool:
    try:
        import redis

        redis.from_url(get_settings().redis_url).ping()
        return True
    except Exception:
        return False


pytestmark = pytest.mark.skipif(not _redis_reachable(), reason="requires live Redis")


class _RaisingRedis:
    """Fake Redis client whose GET always raises — simulates a live outage."""

    async def get(self, key):
        raise ConnectionError("simulated redis outage")


async def _cleanup(r, surface: str) -> None:
    await r.delete(
        runtime_breaker.override_key(surface),
        runtime_breaker.breaker_key(surface),
        runtime_breaker.enabled_key(surface),
    )


async def test_no_keys_resolves_to_static_legacy():
    r = redis_async.from_url(get_settings().redis_url, decode_responses=True)
    surface = f"chat_{uuid.uuid4().hex}"
    try:
        result = await effective_runtime(
            surface, redis=r, settings=make_mock_settings(runtime="legacy")
        )
        assert result == "legacy"
    finally:
        await _cleanup(r, surface)
        await r.aclose()


async def test_enable_key_flips_surface_to_deep():
    r = redis_async.from_url(get_settings().redis_url, decode_responses=True)
    surface = f"chat_{uuid.uuid4().hex}"
    try:
        await r.set(runtime_breaker.enabled_key(surface), "deep")
        result = await effective_runtime(
            surface, redis=r, settings=make_mock_settings(runtime="legacy")
        )
        assert result == "deep"
    finally:
        await _cleanup(r, surface)
        await r.aclose()


async def test_manual_override_forces_legacy_over_enable():
    r = redis_async.from_url(get_settings().redis_url, decode_responses=True)
    surface = f"chat_{uuid.uuid4().hex}"
    try:
        await r.set(runtime_breaker.enabled_key(surface), "deep")
        await r.set(runtime_breaker.override_key(surface), "legacy")
        result = await effective_runtime(
            surface, redis=r, settings=make_mock_settings(runtime="legacy")
        )
        assert result == "legacy"
    finally:
        await _cleanup(r, surface)
        await r.aclose()


async def test_breaker_forces_legacy_over_enable():
    r = redis_async.from_url(get_settings().redis_url, decode_responses=True)
    surface = f"chat_{uuid.uuid4().hex}"
    try:
        await r.set(runtime_breaker.enabled_key(surface), "deep")
        await r.set(runtime_breaker.breaker_key(surface), "legacy")
        result = await effective_runtime(
            surface, redis=r, settings=make_mock_settings(runtime="legacy")
        )
        assert result == "legacy"
    finally:
        await _cleanup(r, surface)
        await r.aclose()


async def test_redis_unavailable_falls_back_to_static():
    surface = f"chat_{uuid.uuid4().hex}"
    result = await effective_runtime(
        surface, redis=None, settings=make_mock_settings(runtime="legacy")
    )
    assert result == "legacy"


async def test_redis_error_falls_back_to_static_not_deep():
    """The MOST safety-critical case: a raising Redis GET must fall through to
    static ``settings.runtime``, never resolve to ``"deep"``."""
    surface = f"chat_{uuid.uuid4().hex}"
    result = await effective_runtime(
        surface, redis=_RaisingRedis(), settings=make_mock_settings(runtime="legacy")
    )
    assert result == "legacy"
    assert result != "deep"


async def test_resolved_once_per_turn_is_stable():
    r = redis_async.from_url(get_settings().redis_url, decode_responses=True)
    surface = f"chat_{uuid.uuid4().hex}"
    try:
        await r.set(runtime_breaker.enabled_key(surface), "deep")
        cache: dict[str, str] = {}

        first = await effective_runtime(
            surface, redis=r, settings=make_mock_settings(runtime="legacy"), cache=cache
        )
        assert first == "deep"

        # Flip the keys mid-"turn": remove the enable key, add an override.
        await r.delete(runtime_breaker.enabled_key(surface))
        await r.set(runtime_breaker.override_key(surface), "legacy")

        second = await effective_runtime(
            surface, redis=r, settings=make_mock_settings(runtime="legacy"), cache=cache
        )
        # The memo holds — Redis is never re-read for this surface in this cache.
        assert second == "deep"
    finally:
        await _cleanup(r, surface)
        await r.aclose()


async def test_trip_sets_breaker_to_legacy():
    r = redis_async.from_url(get_settings().redis_url, decode_responses=True)
    surface = f"chat_{uuid.uuid4().hex}"
    try:
        assert await runtime_breaker.breaker_state(r, surface) is None
        await runtime_breaker.trip(r, surface)
        assert await runtime_breaker.breaker_state(r, surface) == "legacy"
    finally:
        await _cleanup(r, surface)
        await r.aclose()


async def test_clear_removes_breaker():
    r = redis_async.from_url(get_settings().redis_url, decode_responses=True)
    surface = f"chat_{uuid.uuid4().hex}"
    try:
        await runtime_breaker.trip(r, surface)
        assert await runtime_breaker.breaker_state(r, surface) == "legacy"
        await runtime_breaker.clear(r, surface)
        assert await runtime_breaker.breaker_state(r, surface) is None
    finally:
        await _cleanup(r, surface)
        await r.aclose()
