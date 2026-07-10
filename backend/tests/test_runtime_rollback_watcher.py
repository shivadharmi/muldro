"""Step 10B Phase 5 Task 5a: the one-directional auto-rollback watcher tick.

Real Redis, self-contained (module-level ``_redis_reachable`` skipif, matching
``tests/test_runtime_gate.py``). Unlike ``test_runtime_gate.py``, the surfaces under
test here are the FIXED strings the watcher iterates
(``runtime_breaker.VALID_SURFACES`` = "chat" | "perception" | "autonomous") — no other
test file writes to those bare keys (only UUID-suffixed ones), so collision-safety
comes from thorough ``finally``-block cleanup rather than UUID-suffixing.

Covers: cold-start never false-trips, a breach trips the mapped surface's breaker to
"legacy", a just-tripped surface is skipped on the next tick (anti-churn, no explicit
cooldown timer needed), a surface already resolving "legacy" is a pure no-op, and the
watcher is strictly one-directional (never writes an enable=deep key, never clears an
existing breaker).
"""

import pytest
import redis.asyncio as redis_async

from src.config.settings import get_settings
from src.services import runtime_breaker
from src.services.metrics_service import MetricsService
from tests.conftest import make_mock_settings


def _redis_reachable() -> bool:
    try:
        import redis

        redis.from_url(get_settings().redis_url).ping()
        return True
    except Exception:
        return False


pytestmark = pytest.mark.skipif(not _redis_reachable(), reason="requires live Redis")


class _FakeServices:
    def __init__(self, redis):
        self.extras = {"redis": redis}


class _FakeOrchestrator:
    def __init__(self, redis):
        self._services = _FakeServices(redis)


def _make_tick(redis, **overrides):
    from src.services.scheduler.runtime_rollback_tick import RuntimeRollbackTickMixin

    tick = RuntimeRollbackTickMixin()
    tick._settings = make_mock_settings(
        rollback_double_fire_threshold=3,
        rollback_verification_false_negative_threshold=3,
        rollback_double_prompt_threshold=3,
        rollback_ungated_perception_write_threshold=1,
        rollback_shadow_divergence_threshold=3,
        **overrides,
    )
    tick._orchestrator = _FakeOrchestrator(redis)
    return tick


async def _cleanup(r, surfaces: list[str]) -> None:
    keys = []
    for s in surfaces:
        keys += [
            runtime_breaker.override_key(s),
            runtime_breaker.breaker_key(s),
            runtime_breaker.enabled_key(s),
        ]
    await r.delete(*keys)


async def test_cold_start_establishes_baseline_without_tripping():
    """A fresh watcher's FIRST observation of an already-elevated counter must not
    trip — it only establishes the baseline (no false trip on process start)."""
    r = redis_async.from_url(get_settings().redis_url, decode_responses=True)
    try:
        await _cleanup(r, ["autonomous"])
        await r.set(runtime_breaker.enabled_key("autonomous"), "deep")

        # Pre-existing double_fire activity from BEFORE this watcher ever ran.
        MetricsService.record_double_fire(surface="autonomous", kind="already_done")
        MetricsService.record_double_fire(surface="autonomous", kind="already_done")

        tick = _make_tick(r)
        await tick._tick_runtime_rollback(None)

        assert await runtime_breaker.breaker_state(r, "autonomous") is None
    finally:
        await _cleanup(r, ["autonomous"])
        await r.aclose()


async def test_breach_trips_autonomous_to_legacy_then_anti_churn():
    """After baseline is established, a delta >= threshold trips the breaker to
    "legacy". A subsequent tick must not churn: the surface now resolves "legacy" so
    it is skipped entirely (no re-trip, no touch to the enable key)."""
    r = redis_async.from_url(get_settings().redis_url, decode_responses=True)
    try:
        await _cleanup(r, ["autonomous"])
        await r.set(runtime_breaker.enabled_key("autonomous"), "deep")

        tick = _make_tick(r)

        # Tick #1: cold start — establishes baseline, must NOT trip.
        await tick._tick_runtime_rollback(None)
        assert await runtime_breaker.breaker_state(r, "autonomous") is None

        # Breach: threshold is 3, fire 4 double-fires since baseline.
        for _ in range(4):
            MetricsService.record_double_fire(surface="autonomous", kind="in_flight_conflict")

        # Tick #2: delta (4) >= threshold (3) -> trips.
        await tick._tick_runtime_rollback(None)
        assert await runtime_breaker.breaker_state(r, "autonomous") == "legacy"

        # Tick #3: surface now resolves "legacy" -> skipped, no churn.
        await tick._tick_runtime_rollback(None)
        assert await runtime_breaker.breaker_state(r, "autonomous") == "legacy"
        # One-directional: the enable key is untouched by the watcher.
        assert await runtime_breaker.read_key(r, "enabled", "autonomous") == "deep"
    finally:
        await _cleanup(r, ["autonomous"])
        await r.aclose()


async def test_surface_already_legacy_is_pure_noop():
    """A surface with no enable key (resolves "legacy" via the static default) is
    never evaluated: the tick must not write ANY key for it."""
    r = redis_async.from_url(get_settings().redis_url, decode_responses=True)
    try:
        await _cleanup(r, ["chat"])

        tick = _make_tick(r)
        await tick._tick_runtime_rollback(None)

        assert await runtime_breaker.breaker_state(r, "chat") is None
        assert await runtime_breaker.read_key(r, "enabled", "chat") is None
        assert await runtime_breaker.read_key(r, "override", "chat") is None
    finally:
        await _cleanup(r, ["chat"])
        await r.aclose()


async def test_one_directional_never_clears_an_existing_breaker():
    """The watcher may ONLY call ``trip`` (-> legacy). A pre-existing tripped breaker
    on a surface (simulating a prior trip, human or watcher) must survive a tick
    untouched — the watcher never calls ``clear`` and never sets an enable=deep key."""
    r = redis_async.from_url(get_settings().redis_url, decode_responses=True)
    try:
        await _cleanup(r, ["perception"])
        await runtime_breaker.trip(r, "perception")

        tick = _make_tick(r)
        await tick._tick_runtime_rollback(None)

        assert await runtime_breaker.breaker_state(r, "perception") == "legacy"
        assert await runtime_breaker.read_key(r, "enabled", "perception") is None
    finally:
        await _cleanup(r, ["perception"])
        await r.aclose()


async def test_no_redis_is_noop():
    """No reachable Redis client (e.g. a process built without one) -> the tick
    returns immediately without raising."""
    tick = _make_tick(None)
    tick._orchestrator = None  # no orchestrator reachable at all
    await tick._tick_runtime_rollback(None)  # must not raise
