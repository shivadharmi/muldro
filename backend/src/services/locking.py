"""Execution locks — prevent concurrent operations on the same resource.

Uses PostgreSQL advisory locks for distributed-safe locking without
additional infrastructure. Advisory locks are session-scoped and
automatically released when the session ends.
"""

import hashlib
import logging
from contextlib import asynccontextmanager

from sqlalchemy import text
from sqlalchemy.ext.asyncio import AsyncSession

logger = logging.getLogger(__name__)


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
