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


# ── admin route handlers (called directly, bypassing FastAPI DI + require_admin) ──
# These exercise the handler BODY (surface/target validation, redis wiring). The
# router-level require_admin gate is exercised over HTTP in the integration tests below.


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

        resp = await set_runtime_override(
            RuntimeOverrideRequest(surface=surface, target="legacy"), request
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

    with pytest.raises(HTTPException) as exc_info:
        await set_runtime_override(
            RuntimeOverrideRequest(surface="not_a_valid_surface", target="legacy"), request
        )
    assert exc_info.value.status_code == 400


async def test_route_post_override_rejects_target_deep():
    """FIX A: the escape hatch forces the SAFE direction only. target="deep" (the
    privilege-escalation vector the security review flagged) must be rejected 400 —
    flipping a surface to "deep" is the separate ENABLE-key rollout path, not this."""
    from fastapi import HTTPException

    from src.api.routes_admin_runtime import RuntimeOverrideRequest, set_runtime_override

    request = MagicMock()

    with pytest.raises(HTTPException) as exc_info:
        await set_runtime_override(RuntimeOverrideRequest(surface="chat", target="deep"), request)
    assert exc_info.value.status_code == 400


async def test_route_post_override_rejects_invalid_target():
    from fastapi import HTTPException

    from src.api.routes_admin_runtime import RuntimeOverrideRequest, set_runtime_override

    request = MagicMock()

    with pytest.raises(HTTPException) as exc_info:
        await set_runtime_override(
            RuntimeOverrideRequest(surface="chat", target="not_a_runtime"), request
        )
    assert exc_info.value.status_code == 400


async def test_route_post_override_no_redis_returns_503():
    from fastapi import HTTPException

    from src.api.routes_admin_runtime import RuntimeOverrideRequest, set_runtime_override

    request = MagicMock()
    request.app.state.redis = None

    with pytest.raises(HTTPException) as exc_info:
        await set_runtime_override(RuntimeOverrideRequest(surface="chat", target="legacy"), request)
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

        resp = await clear_runtime_override(surface, request)

        assert resp.status == "cleared"
        assert await runtime_breaker.read_key(r, "override", surface) is None
    finally:
        await _cleanup(r, [surface])
        await r.aclose()


# ── require_admin gate — HTTP integration tests (over the app test client) ────────
# The security review explicitly wants an integration test that hits the endpoint over
# HTTP and asserts 403 for a non-admin. These are SYNC tests (plain def): TestClient runs
# the app in its own portal thread, so they must not run under the async-test hook.

_ADMIN_TOKEN = "s3cr3t-operator-token"


def _client_with_admin_token(token: str):
    """A TestClient with server-side ``admin_api_token`` overridden to ``token`` via the
    get_settings dependency (require_admin reads settings through Depends(get_settings))."""
    from fastapi.testclient import TestClient

    from src.api.app import app
    from src.config.settings import get_settings as settings_dep

    app.dependency_overrides[settings_dep] = lambda: make_mock_settings(admin_api_token=token)
    return TestClient(app), app, settings_dep


def _restore(app, settings_dep) -> None:
    app.dependency_overrides.pop(settings_dep, None)


def test_http_no_admin_token_is_403():
    client, app, settings_dep = _client_with_admin_token(_ADMIN_TOKEN)
    try:
        resp = client.post(
            "/v1/admin/runtime/override", json={"surface": "chat", "target": "legacy"}
        )
        assert resp.status_code == 403, resp.text
    finally:
        _restore(app, settings_dep)


def test_http_wrong_admin_token_is_403():
    client, app, settings_dep = _client_with_admin_token(_ADMIN_TOKEN)
    try:
        resp = client.post(
            "/v1/admin/runtime/override",
            json={"surface": "chat", "target": "legacy"},
            headers={"X-Admin-Token": "wrong-token"},
        )
        assert resp.status_code == 403, resp.text
    finally:
        _restore(app, settings_dep)


def test_http_unset_server_token_is_403_failclosed():
    """Fail-closed: with no admin token configured server-side, EVEN a caller supplying
    a token is rejected — the route is disabled entirely by default."""
    client, app, settings_dep = _client_with_admin_token("")  # server token unset
    try:
        resp = client.post(
            "/v1/admin/runtime/override",
            json={"surface": "chat", "target": "legacy"},
            headers={"X-Admin-Token": "anything"},
        )
        assert resp.status_code == 403, resp.text
    finally:
        _restore(app, settings_dep)


def test_http_valid_token_legacy_is_200_and_sets_key():
    import redis as sync_redis
    import redis.asyncio as _redis_async

    client, app, settings_dep = _client_with_admin_token(_ADMIN_TOKEN)
    surface = "chat"
    prior_redis = getattr(app.state, "redis", None)
    # TestClient does not run lifespan (no `with`), so wire app.state.redis manually.
    app.state.redis = _redis_async.from_url(get_settings().redis_url, decode_responses=True)
    checker = sync_redis.from_url(get_settings().redis_url, decode_responses=True)
    try:
        checker.delete(runtime_breaker.override_key(surface))

        resp = client.post(
            "/v1/admin/runtime/override",
            json={"surface": surface, "target": "legacy"},
            headers={"X-Admin-Token": _ADMIN_TOKEN},
        )

        assert resp.status_code == 200, resp.text
        assert resp.json()["status"] == "set"
        assert checker.get(runtime_breaker.override_key(surface)) == "legacy"
    finally:
        checker.delete(runtime_breaker.override_key(surface))
        checker.close()
        app.state.redis = prior_redis
        _restore(app, settings_dep)


def test_http_valid_token_deep_is_400_restricted():
    """FIX A over HTTP: a valid operator cannot use the escape hatch to force "deep"."""
    client, app, settings_dep = _client_with_admin_token(_ADMIN_TOKEN)
    prior_redis = getattr(app.state, "redis", None)
    app.state.redis = MagicMock()  # target validation rejects before redis is touched
    try:
        resp = client.post(
            "/v1/admin/runtime/override",
            json={"surface": "chat", "target": "deep"},
            headers={"X-Admin-Token": _ADMIN_TOKEN},
        )
        assert resp.status_code == 400, resp.text
    finally:
        app.state.redis = prior_redis
        _restore(app, settings_dep)
