"""Tests for per-endpoint rate limiting on sensitive/expensive endpoints.

The global RateLimitMiddleware (120 rpm/IP) covers reads. These tests cover
the tighter per-endpoint caps wired onto:
  - /v1/auth/verify           (magic-link token brute-force protection)
  - /v1/approvals/{id}/approve|reject|edit   (trigger execution)
  - /v1/history/{run_id}/retry, /v1/runs/{run_id}/cancel|resume  (re-execution)
"""

import pytest
from fastapi import Depends, FastAPI
from fastapi.testclient import TestClient

from src.api.app import app
from src.middleware.security import RateLimiter, per_endpoint_rate_limit


@pytest.fixture(autouse=True)
def _reset_limiter():
    RateLimiter.reset()
    yield
    RateLimiter.reset()


def test_per_endpoint_dependency_returns_429_over_limit():
    """The dependency blocks with 429 once the per-endpoint limit is exceeded."""
    mini = FastAPI()

    @mini.post("/guarded", dependencies=[Depends(per_endpoint_rate_limit(2))])
    async def guarded():
        return {"ok": True}

    client = TestClient(mini)

    assert client.post("/guarded").status_code == 200
    assert client.post("/guarded").status_code == 200
    resp = client.post("/guarded")
    assert resp.status_code == 429
    assert "rate limit" in resp.json()["detail"].lower()


def test_per_endpoint_dependency_independent_per_path():
    """Each path has its own counter — exhausting one does not block another."""
    mini = FastAPI()

    @mini.post("/a", dependencies=[Depends(per_endpoint_rate_limit(1))])
    async def a():
        return {"ok": "a"}

    @mini.post("/b", dependencies=[Depends(per_endpoint_rate_limit(1))])
    async def b():
        return {"ok": "b"}

    client = TestClient(mini)
    assert client.post("/a").status_code == 200
    assert client.post("/a").status_code == 429
    # Different path, fresh counter.
    assert client.post("/b").status_code == 200


def _has_rate_limit_dependency(route) -> bool:
    """Recursively check a route's dependant tree for the rate-limit dependency."""
    dependant = getattr(route, "dependant", None)
    if dependant is None:
        return False
    stack = list(dependant.dependencies)
    while stack:
        dep = stack.pop()
        call = getattr(dep, "call", None)
        qualname = getattr(call, "__qualname__", "")
        if "per_endpoint_rate_limit" in qualname:
            return True
        stack.extend(dep.dependencies)
    return False


def _find_route(path: str, method: str):
    for route in app.routes:
        if getattr(route, "path", None) == path and method in getattr(route, "methods", set()):
            return route
    return None


PROTECTED_ENDPOINTS = [
    ("/v1/auth/verify", "POST"),
    ("/v1/approvals/{approval_id}/approve", "POST"),
    ("/v1/approvals/{approval_id}/reject", "POST"),
    ("/v1/approvals/{approval_id}/edit", "POST"),
    ("/v1/history/{run_id}/retry", "POST"),
    ("/v1/runs/{run_id}/cancel", "POST"),
    ("/v1/runs/{run_id}/resume", "POST"),
]


@pytest.mark.parametrize("path,method", PROTECTED_ENDPOINTS)
def test_sensitive_endpoint_has_rate_limit(path, method):
    """Each sensitive/expensive endpoint carries a per-endpoint rate limit."""
    route = _find_route(path, method)
    assert route is not None, f"route {method} {path} not found"
    assert _has_rate_limit_dependency(route), f"{method} {path} missing per-endpoint rate limit"
