"""Security middleware — rate limiting, request size limits.

Provides basic protection against abuse for the v1 single-user system.
Uses Redis for rate limiting when available, falls back to in-memory.

Uses pure ASGI middleware (not BaseHTTPMiddleware) to avoid buffering
streaming responses like SSE.
"""

import logging
import time
from collections import defaultdict
from typing import ClassVar

from starlette.types import ASGIApp, Receive, Scope, Send

logger = logging.getLogger(__name__)

# Maximum request body size (1MB)
MAX_REQUEST_BODY_BYTES = 1_048_576


class RateLimiter:
    """Simple in-memory sliding window rate limiter.

    Used as fallback when Redis is not available.
    """

    _windows: ClassVar[dict[str, list[float]]] = defaultdict(list)

    def __init__(self, requests_per_minute: int = 120):
        self.rpm = requests_per_minute
        self.window_seconds = 60.0

    def is_allowed(self, key: str) -> bool:
        """Check if the request is within rate limits."""
        now = time.monotonic()
        window = self._windows[key]

        # Prune expired entries
        cutoff = now - self.window_seconds
        self._windows[key] = [t for t in window if t > cutoff]

        if len(self._windows[key]) >= self.rpm:
            return False

        self._windows[key].append(now)
        return True

    @classmethod
    def reset(cls) -> None:
        cls._windows.clear()


class RedisRateLimiter:
    """Redis-backed sliding window rate limiter.

    Survives server restarts, works across multiple instances.
    Key: ratelimit:{ip}:{minute_bucket}
    """

    def __init__(self, redis, requests_per_minute: int = 120):
        self._redis = redis
        self.rpm = requests_per_minute

    async def is_allowed(self, key: str) -> bool:
        """Check if the request is within rate limits using Redis INCR."""
        minute_bucket = int(time.time()) // 60
        redis_key = f"ratelimit:{key}:{minute_bucket}"

        count = await self._redis.incr(redis_key)
        if count == 1:
            await self._redis.expire(redis_key, 120)  # 2 min TTL for safety

        return count <= self.rpm


class RateLimitMiddleware:
    """Pure ASGI middleware: per-IP rate limiting.

    Uses Redis when available, in-memory fallback.
    Does not buffer responses — streaming (SSE) passes through cleanly.
    """

    def __init__(self, app: ASGIApp, requests_per_minute: int = 120) -> None:
        self.app = app
        self._limiter = RateLimiter(requests_per_minute=requests_per_minute)
        self._rpm = requests_per_minute
        self._redis_limiter: RedisRateLimiter | None = None

    async def __call__(self, scope: Scope, receive: Receive, send: Send) -> None:
        if scope["type"] != "http":
            await self.app(scope, receive, send)
            return

        # Extract client IP
        client = scope.get("client")
        client_ip = client[0] if client else "unknown"

        # Try Redis limiter first
        app_state = scope.get("app")
        redis = getattr(app_state.state, "redis", None) if app_state else None

        if redis is not None:
            if self._redis_limiter is None:
                self._redis_limiter = RedisRateLimiter(redis, self._rpm)
            try:
                if not await self._redis_limiter.is_allowed(client_ip):
                    path = scope.get("path", "")
                    logger.warning("Rate limit exceeded: %s %s", client_ip, path)
                    await _send_json_response(
                        send,
                        429,
                        {"detail": "Rate limit exceeded. Try again later."},
                    )
                    return
                await self.app(scope, receive, send)
                return
            except Exception:
                logger.debug("Redis rate limiter failed, falling back to in-memory")

        # Fallback to in-memory limiter
        if not self._limiter.is_allowed(client_ip):
            path = scope.get("path", "")
            logger.warning("Rate limit exceeded: %s %s", client_ip, path)
            await _send_json_response(
                send,
                429,
                {"detail": "Rate limit exceeded. Try again later."},
            )
            return

        await self.app(scope, receive, send)


class RequestSizeLimitMiddleware:
    """Pure ASGI middleware: reject requests with bodies exceeding the size limit.

    Does not buffer responses — streaming (SSE) passes through cleanly.
    """

    def __init__(self, app: ASGIApp, max_bytes: int = MAX_REQUEST_BODY_BYTES) -> None:
        self.app = app
        self._max_bytes = max_bytes

    async def __call__(self, scope: Scope, receive: Receive, send: Send) -> None:
        if scope["type"] != "http":
            await self.app(scope, receive, send)
            return

        headers = dict(scope.get("headers", []))
        content_length = headers.get(b"content-length", b"").decode("utf-8")

        if content_length and int(content_length) > self._max_bytes:
            path = scope.get("path", "")
            logger.warning("Request too large: %s bytes from %s", content_length, path)
            await _send_json_response(send, 413, {"detail": "Request body too large"})
            return

        await self.app(scope, receive, send)


def per_endpoint_rate_limit(max_rpm: int = 10):
    """FastAPI dependency factory for per-endpoint rate limiting.

    Uses Redis when available, falls back to in-memory. Returns a Depends
    callable that raises HTTPException(429) when the limit is exceeded.

    Usage: @router.post("/path", dependencies=[Depends(per_endpoint_rate_limit(5))])
    """
    from starlette.requests import Request

    _in_memory = RateLimiter(requests_per_minute=max_rpm)

    async def _check(request: Request):
        from fastapi import HTTPException

        client = request.client
        client_ip = client.host if client else "unknown"
        route = request.scope.get("route")
        path = route.path if route else request.url.path
        key = f"ep:{path}:{client_ip}"

        msg = "Rate limit exceeded. Try again later."
        redis = getattr(request.app.state, "redis", None)
        if redis is not None:
            try:
                rl = RedisRateLimiter(redis, requests_per_minute=max_rpm)
                if not await rl.is_allowed(key):
                    raise HTTPException(status_code=429, detail=msg)
                return
            except HTTPException:
                raise
            except Exception:
                pass  # fall back to in-memory

        if not _in_memory.is_allowed(key):
            raise HTTPException(status_code=429, detail=msg)

    return _check


async def _send_json_response(send: Send, status: int, body: dict) -> None:
    """Send a JSON error response via raw ASGI send."""
    import json

    payload = json.dumps(body).encode("utf-8")
    await send(
        {
            "type": "http.response.start",
            "status": status,
            "headers": [
                (b"content-type", b"application/json"),
                (b"content-length", str(len(payload)).encode()),
            ],
        }
    )
    await send(
        {
            "type": "http.response.body",
            "body": payload,
        }
    )
