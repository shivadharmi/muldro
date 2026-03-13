"""Security middleware — rate limiting, request size limits.

Provides basic protection against abuse for the v1 single-user system.
Uses Redis for rate limiting when available, falls back to in-memory.
"""

import logging
import time
from collections import defaultdict
from typing import ClassVar

from starlette.middleware.base import BaseHTTPMiddleware
from starlette.requests import Request
from starlette.responses import JSONResponse, Response

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


class RateLimitMiddleware(BaseHTTPMiddleware):
    """Apply per-IP rate limiting. Uses Redis when available, in-memory fallback."""

    def __init__(self, app, requests_per_minute: int = 120):
        super().__init__(app)
        self._limiter = RateLimiter(requests_per_minute=requests_per_minute)
        self._rpm = requests_per_minute
        self._redis_limiter: RedisRateLimiter | None = None

    async def dispatch(self, request: Request, call_next) -> Response:
        client_ip = request.client.host if request.client else "unknown"

        # Try Redis limiter first
        redis = getattr(request.app.state, "redis", None)
        if redis is not None:
            if self._redis_limiter is None:
                self._redis_limiter = RedisRateLimiter(redis, self._rpm)
            try:
                if not await self._redis_limiter.is_allowed(client_ip):
                    logger.warning("Rate limit exceeded: %s %s", client_ip, request.url.path)
                    return JSONResponse(
                        status_code=429,
                        content={"detail": "Rate limit exceeded. Try again later."},
                    )
                return await call_next(request)
            except Exception:
                logger.debug("Redis rate limiter failed, falling back to in-memory")

        # Fallback to in-memory limiter
        if not self._limiter.is_allowed(client_ip):
            logger.warning("Rate limit exceeded: %s %s", client_ip, request.url.path)
            return JSONResponse(
                status_code=429,
                content={"detail": "Rate limit exceeded. Try again later."},
            )

        return await call_next(request)


class RequestSizeLimitMiddleware(BaseHTTPMiddleware):
    """Reject requests with bodies exceeding the size limit."""

    def __init__(self, app, max_bytes: int = MAX_REQUEST_BODY_BYTES):
        super().__init__(app)
        self._max_bytes = max_bytes

    async def dispatch(self, request: Request, call_next) -> Response:
        content_length = request.headers.get("content-length")
        if content_length and int(content_length) > self._max_bytes:
            logger.warning(
                "Request too large: %s bytes from %s",
                content_length,
                request.url.path,
            )
            return JSONResponse(
                status_code=413,
                content={"detail": "Request body too large"},
            )

        return await call_next(request)
