"""Execution locks — prevent concurrent operations on the same resource.

Supports two backends:
1. Redis distributed locks (preferred — faster, works across instances)
2. PostgreSQL advisory locks (fallback when Redis unavailable)

Advisory locks are session-scoped and automatically released when the session ends.
Redis locks use SET NX EX pattern with configurable TTL.
"""

import hashlib
import logging
from contextlib import asynccontextmanager

from sqlalchemy import text
from sqlalchemy.ext.asyncio import AsyncSession

logger = logging.getLogger(__name__)


# ── Redis distributed locks ─────────────────────────────────────


class RedisLock:
    """Redis-based distributed lock using SET NX EX pattern."""

    def __init__(self, redis):
        self._redis = redis

    async def acquire(self, key: str, ttl_seconds: int = 30) -> bool:
        """Try to acquire a lock. Returns True if acquired."""
        lock_key = f"lock:{key}"
        acquired = await self._redis.set(lock_key, "1", nx=True, ex=ttl_seconds)
        if acquired:
            logger.debug("Acquired Redis lock: %s (ttl=%ds)", key, ttl_seconds)
        return bool(acquired)

    async def release(self, key: str) -> None:
        """Release a previously acquired lock."""
        lock_key = f"lock:{key}"
        await self._redis.delete(lock_key)
        logger.debug("Released Redis lock: %s", key)


@asynccontextmanager
async def distributed_lock(redis, key: str, ttl: int = 30):
    """Redis-backed distributed lock with auto-release."""
    lock = RedisLock(redis)
    acquired = await lock.acquire(key, ttl_seconds=ttl)
    if not acquired:
        raise RuntimeError(f"Failed to acquire lock: {key}")
    try:
        yield
    finally:
        await lock.release(key)


# ── PostgreSQL advisory locks (fallback) ────────────────────────


def _resource_to_lock_id(resource_key: str) -> int:
    """Convert a string resource key to a 64-bit integer for pg_advisory_lock.

    Uses the first 8 bytes of an MD5 hash to produce a stable, deterministic
    lock ID from any string key.
    """
    digest = hashlib.md5(resource_key.encode()).digest()  # noqa: S324
    return int.from_bytes(digest[:8], byteorder="big", signed=True)


@asynccontextmanager
async def advisory_lock(db: AsyncSession, resource_key: str):
    """Acquire a PostgreSQL advisory lock for a resource.

    Usage:
        async with advisory_lock(db, f"execution:{execution_id}"):
            # only one worker can execute this block for this resource
            await operator.execute_plan(execution_id, user_id)

    The lock is released when the context manager exits.
    """
    lock_id = _resource_to_lock_id(resource_key)
    try:
        await db.execute(text(f"SELECT pg_advisory_lock({lock_id})"))
        logger.debug("Acquired lock: %s (id=%d)", resource_key, lock_id)
        yield
    finally:
        await db.execute(text(f"SELECT pg_advisory_unlock({lock_id})"))
        logger.debug("Released lock: %s (id=%d)", resource_key, lock_id)


async def try_advisory_lock(db: AsyncSession, resource_key: str) -> bool:
    """Try to acquire a PostgreSQL advisory lock without blocking.

    Returns True if the lock was acquired, False if already held.
    Caller must release with release_advisory_lock() when done.
    """
    lock_id = _resource_to_lock_id(resource_key)
    result = await db.execute(text(f"SELECT pg_try_advisory_lock({lock_id})"))
    acquired = result.scalar()
    if acquired:
        logger.debug("Acquired non-blocking lock: %s (id=%d)", resource_key, lock_id)
    else:
        logger.debug("Lock already held: %s (id=%d)", resource_key, lock_id)
    return bool(acquired)


async def release_advisory_lock(db: AsyncSession, resource_key: str) -> None:
    """Release a previously acquired advisory lock."""
    lock_id = _resource_to_lock_id(resource_key)
    await db.execute(text(f"SELECT pg_advisory_unlock({lock_id})"))
    logger.debug("Released lock: %s (id=%d)", resource_key, lock_id)
