"""Redis-backed caching for frequently accessed data.

Cache targets:
- Briefings: brief:{user_id}:{date} (TTL: 1 hour)
- Entity lookups: entity:{user_id}:{query} (TTL: 5 min)
- User preferences: prefs:{user_id} (TTL: 10 min)
- Event dedup window: dedup:{idempotency_key} (TTL: 24h)
"""

import json
import logging

logger = logging.getLogger(__name__)


class RedisCache:
    """Redis-backed caching for frequently accessed data."""

    def __init__(self, redis):
        self._redis = redis

    async def get(self, key: str) -> str | None:
        """Get a string value from cache."""
        return await self._redis.get(key)

    async def set(self, key: str, value: str, ttl_seconds: int = 300) -> None:
        """Set a string value in cache with TTL."""
        await self._redis.set(key, value, ex=ttl_seconds)

    async def delete(self, key: str) -> None:
        """Delete a key from cache."""
        await self._redis.delete(key)

    async def get_json(self, key: str) -> dict | None:
        """Get a JSON value from cache."""
        raw = await self._redis.get(key)
        if raw is None:
            return None
        return json.loads(raw)

    async def set_json(self, key: str, value: dict, ttl_seconds: int = 300) -> None:
        """Set a JSON value in cache with TTL."""
        await self._redis.set(key, json.dumps(value), ex=ttl_seconds)

    async def exists(self, key: str) -> bool:
        """Check if a key exists in cache."""
        return bool(await self._redis.exists(key))

    async def incr_with_ttl(self, key: str, ttl_seconds: int = 60) -> int:
        """Increment a counter and set TTL if new. Returns new count."""
        count = await self._redis.incr(key)
        if count == 1:
            await self._redis.expire(key, ttl_seconds)
        return count
