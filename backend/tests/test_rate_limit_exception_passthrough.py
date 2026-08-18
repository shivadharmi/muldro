"""RateLimitMiddleware must not swallow downstream exceptions, nor re-run the app.

Regression guard for a defect that made EVERY unhandled endpoint exception hang
the request forever whenever Redis was reachable (i.e. dev and prod alike).

``await self.app(...)`` sat INSIDE the ``try`` whose ``except Exception`` exists
only to catch a failing Redis limiter. So an endpoint error was caught there,
logged as "Redis rate limiter failed", and the middleware fell through to the
in-memory branch — invoking the whole downstream app a SECOND time on the same
request. The second pass awaited ``request.body()``, but the receive stream had
already been drained by the first pass, so it blocked forever. The client saw no
bytes at all; the server's ``unhandled_error`` handler never ran, so the real
traceback was replaced by a misleading Redis debug line.

The two tests below pin the two halves that must stay true together:
a downstream error propagates (and runs the app once), while a genuinely failing
Redis limiter still falls back to the in-memory limiter and serves the request.
"""

from __future__ import annotations

import pytest

from src.middleware.security import RateLimiter, RateLimitMiddleware


@pytest.fixture(autouse=True)
def _reset_limiter():
    RateLimiter.reset()
    yield
    RateLimiter.reset()


class _FakeRedis:
    """Minimal stand-in for the redis client RedisRateLimiter drives."""

    def __init__(self, *, fail: bool = False) -> None:
        self._fail = fail

    async def incr(self, key: str) -> int:
        if self._fail:
            raise ConnectionError("redis is down")
        return 1  # first hit in the bucket -> always under the limit

    async def expire(self, key: str, ttl: int) -> None:
        if self._fail:
            raise ConnectionError("redis is down")


class _AppState:
    def __init__(self, redis) -> None:
        self.redis = redis


class _FakeApp:
    """Stands in for the ASGI app under the middleware."""

    def __init__(self, redis) -> None:
        self.state = _AppState(redis)


def _scope(app: _FakeApp) -> dict:
    return {
        "type": "http",
        "path": "/v1/connections/begin",
        "method": "POST",
        "headers": [],
        "client": ("127.0.0.1", 51234),
        "app": app,
    }


async def _noop_receive() -> dict:
    return {"type": "http.request", "body": b"", "more_body": False}


async def _noop_send(message: dict) -> None:
    return None


async def test_downstream_exception_propagates_and_app_runs_once():
    """An endpoint error must reach the error handlers, not be eaten by the limiter.

    Two assertions, because the defect had two symptoms and fixing only one
    would leave the other: the exception must escape the middleware (so
    TracingMiddleware logs it and the 500 handler renders it), AND the
    downstream app must be entered exactly once (a second entry is what
    deadlocked on the already-consumed request body).
    """
    calls = 0

    async def failing_app(scope, receive, send):
        nonlocal calls
        calls += 1
        raise RuntimeError("boom from the endpoint")

    holder = _FakeApp(_FakeRedis())
    mw = RateLimitMiddleware(failing_app, requests_per_minute=120)

    with pytest.raises(RuntimeError, match="boom from the endpoint"):
        await mw(_scope(holder), _noop_receive, _noop_send)

    assert calls == 1, f"downstream app was invoked {calls} times; must be exactly 1"


async def test_failing_redis_still_falls_back_to_in_memory():
    """The fallback the try/except was actually written for must keep working.

    If the Redis limiter itself raises, the middleware still has to serve the
    request via the in-memory limiter — exactly once.
    """
    calls = 0
    sent: list[dict] = []

    async def ok_app(scope, receive, send):
        nonlocal calls
        calls += 1
        await send({"type": "http.response.start", "status": 200, "headers": []})
        await send({"type": "http.response.body", "body": b"ok"})

    async def capture_send(message: dict) -> None:
        sent.append(message)

    holder = _FakeApp(_FakeRedis(fail=True))
    mw = RateLimitMiddleware(ok_app, requests_per_minute=120)

    await mw(_scope(holder), _noop_receive, capture_send)

    assert calls == 1, f"downstream app was invoked {calls} times; must be exactly 1"
    assert sent[0]["status"] == 200
