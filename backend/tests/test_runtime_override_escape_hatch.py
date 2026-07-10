"""Step 10B Phase 5 Task 5b: the manual runtime kill-switch (escape hatch).

Real Redis, self-contained (module-level ``_redis_reachable`` skipif, matching
``tests/test_runtime_gate.py``). Fixed-surface tests (the ``surface="all"`` cases)
clean up ``runtime_breaker.VALID_SURFACES`` keys in ``finally``; single-surface tests
UUID-suffix like ``test_runtime_gate.py`` for extra collision-safety.

Covers: the override wins over an enabled=deep key (highest-priority tier, mirroring
``test_manual_override_forces_legacy_over_enable`` in test_runtime_gate.py but exercised
through the write helper instead of a raw ``redis.set``), ``surface="all"`` fans out to
every surface, clearing restores resolution to the lower tiers, and the admin route
(called directly, bypassing FastAPI DI — mirrors ``tests/test_routes_realtime.py``).
"""

import uuid
from unittest.mock import MagicMock

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


async def _cleanup(r, surfaces: list[str]) -> None:
    keys = []
    for s in surfaces:
        keys += [
            runtime_breaker.override_key(s),
            runtime_breaker.breaker_key(s),
            runtime_breaker.enabled_key(s),
        ]
    await r.delete(*keys)


# ── runtime_breaker.set_manual_override / clear_manual_override ──────────────────


async def test_override_forces_legacy_over_enabled_deep():
    r = redis_async.from_url(get_settings().redis_url, decode_responses=True)
    surface = f"chat_{uuid.uuid4().hex}"
    try:
        await r.set(runtime_breaker.enabled_key(surface), "deep")
        await runtime_breaker.set_manual_override(r, surface, target="legacy")

        result = await effective_runtime(
            surface, redis=r, settings=make_mock_settings(runtime="legacy")
        )
        assert result == "legacy"
    finally:
        await _cleanup(r, [surface])
        await r.aclose()


async def test_override_all_forces_every_surface_legacy():
    r = redis_async.from_url(get_settings().redis_url, decode_responses=True)
    try:
        await _cleanup(r, list(runtime_breaker.VALID_SURFACES))
        for s in runtime_breaker.VALID_SURFACES:
            await r.set(runtime_breaker.enabled_key(s), "deep")

        await runtime_breaker.set_manual_override(r, "all", target="legacy")

        for s in runtime_breaker.VALID_SURFACES:
            result = await effective_runtime(
                s, redis=r, settings=make_mock_settings(runtime="legacy")
            )
            assert result == "legacy", f"surface {s!r} was not forced legacy"
    finally:
        await _cleanup(r, list(runtime_breaker.VALID_SURFACES))
        await r.aclose()


async def test_clear_override_restores_lower_tiers():
    r = redis_async.from_url(get_settings().redis_url, decode_responses=True)
    surface = f"chat_{uuid.uuid4().hex}"
    try:
        await r.set(runtime_breaker.enabled_key(surface), "deep")
        await runtime_breaker.set_manual_override(r, surface, target="legacy")
        assert (
            await effective_runtime(surface, redis=r, settings=make_mock_settings(runtime="legacy"))
            == "legacy"
        )

        await runtime_breaker.clear_manual_override(r, surface)

        result = await effective_runtime(
            surface, redis=r, settings=make_mock_settings(runtime="legacy")
        )
        assert result == "deep"  # falls through to the still-set enable key
    finally:
        await _cleanup(r, [surface])
        await r.aclose()


async def test_clear_override_all():
    r = redis_async.from_url(get_settings().redis_url, decode_responses=True)
    try:
        await _cleanup(r, list(runtime_breaker.VALID_SURFACES))
        await runtime_breaker.set_manual_override(r, "all", target="legacy")
        for s in runtime_breaker.VALID_SURFACES:
            assert await runtime_breaker.read_key(r, "override", s) == "legacy"

        await runtime_breaker.clear_manual_override(r, "all")

        for s in runtime_breaker.VALID_SURFACES:
            assert await runtime_breaker.read_key(r, "override", s) is None
    finally:
        await _cleanup(r, list(runtime_breaker.VALID_SURFACES))
        await r.aclose()


# ── admin route (called directly, bypassing FastAPI DI) ──────────────────────────


async def test_route_post_override_sets_key():
    from src.api.routes_admin_runtime import RuntimeOverrideRequest, set_runtime_override

    r = redis_async.from_url(get_settings().redis_url, decode_responses=True)
    # The route validates against the FIXED VALID_SURFACES + "all" — a UUID-suffixed
    # surface would be rejected as invalid, so this uses a real (fixed) surface and
    # relies on `finally` cleanup for collision-safety (mirrors
    # test_runtime_rollback_watcher.py's fixed-surface convention).
    surface = "chat"
    try:
        request = MagicMock()
        request.app.state.redis = r
        user = MagicMock()
        user.user_id = "usr_admin_test"

        resp = await set_runtime_override(
            RuntimeOverrideRequest(surface=surface, target="legacy"), request, user
        )

        assert resp.status == "set"
        assert await runtime_breaker.read_key(r, "override", surface) == "legacy"
    finally:
        await _cleanup(r, [surface])
        await r.aclose()


async def test_route_post_override_rejects_invalid_surface():
    from fastapi import HTTPException

    from src.api.routes_admin_runtime import RuntimeOverrideRequest, set_runtime_override

    request = MagicMock()
    user = MagicMock()
    user.user_id = "usr_admin_test"

    with pytest.raises(HTTPException) as exc_info:
        await set_runtime_override(
            RuntimeOverrideRequest(surface="not_a_valid_surface", target="legacy"), request, user
        )
    assert exc_info.value.status_code == 400


async def test_route_post_override_rejects_invalid_target():
    from fastapi import HTTPException

    from src.api.routes_admin_runtime import RuntimeOverrideRequest, set_runtime_override

    request = MagicMock()
    user = MagicMock()
    user.user_id = "usr_admin_test"

    with pytest.raises(HTTPException) as exc_info:
        await set_runtime_override(
            RuntimeOverrideRequest(surface="chat", target="not_a_runtime"), request, user
        )
    assert exc_info.value.status_code == 400


async def test_route_post_override_no_redis_returns_503():
    from fastapi import HTTPException

    from src.api.routes_admin_runtime import RuntimeOverrideRequest, set_runtime_override

    request = MagicMock()
    request.app.state.redis = None
    user = MagicMock()
    user.user_id = "usr_admin_test"

    with pytest.raises(HTTPException) as exc_info:
        await set_runtime_override(
            RuntimeOverrideRequest(surface="chat", target="legacy"), request, user
        )
    assert exc_info.value.status_code == 503


async def test_route_delete_override_clears_key():
    from src.api.routes_admin_runtime import clear_runtime_override

    r = redis_async.from_url(get_settings().redis_url, decode_responses=True)
    # Fixed surface — see comment in test_route_post_override_sets_key.
    surface = "perception"
    try:
        await runtime_breaker.set_manual_override(r, surface, target="legacy")
        request = MagicMock()
        request.app.state.redis = r
        user = MagicMock()
        user.user_id = "usr_admin_test"

        resp = await clear_runtime_override(surface, request, user)

        assert resp.status == "cleared"
        assert await runtime_breaker.read_key(r, "override", surface) is None
    finally:
        await _cleanup(r, [surface])
        await r.aclose()
