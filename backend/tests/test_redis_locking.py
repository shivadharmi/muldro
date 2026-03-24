"""Tests for Redis distributed locks."""

import pytest

from src.services.locking import RedisLock, distributed_lock

try:
    import fakeredis.aioredis as fakeredis_aio
except ImportError:
    fakeredis_aio = None


@pytest.fixture
def redis():
    if fakeredis_aio is None:
        pytest.skip("fakeredis not installed")
    return fakeredis_aio.FakeRedis(decode_responses=True)


@pytest.fixture
def lock(redis):
    return RedisLock(redis)


@pytest.mark.asyncio
async def test_acquire_and_release(lock):
    """Lock can be acquired and released."""
    assert await lock.acquire("resource:1", ttl_seconds=30)
    await lock.release("resource:1")


@pytest.mark.asyncio
async def test_lock_prevents_double_acquire(lock):
    """Second acquire on same key should fail."""
    assert await lock.acquire("resource:2", ttl_seconds=30)
    assert not await lock.acquire("resource:2", ttl_seconds=30)
    await lock.release("resource:2")


@pytest.mark.asyncio
async def test_release_allows_reacquire(lock):
    """After release, lock can be reacquired."""
    assert await lock.acquire("resource:3", ttl_seconds=30)
    await lock.release("resource:3")
    assert await lock.acquire("resource:3", ttl_seconds=30)
    await lock.release("resource:3")


@pytest.mark.asyncio
async def test_different_keys_independent(lock):
    """Locks on different keys are independent."""
    assert await lock.acquire("resource:a", ttl_seconds=30)
    assert await lock.acquire("resource:b", ttl_seconds=30)
    await lock.release("resource:a")
    await lock.release("resource:b")


@pytest.mark.asyncio
async def test_distributed_lock_context_manager(redis):
    """distributed_lock context manager acquires and releases."""
    async with distributed_lock(redis, "ctx:1", ttl=30):
        # Lock should be held
        lock = RedisLock(redis)
        assert not await lock.acquire("ctx:1", ttl_seconds=30)

    # Lock should be released after context exits
    lock = RedisLock(redis)
    assert await lock.acquire("ctx:1", ttl_seconds=30)
    await lock.release("ctx:1")


@pytest.mark.asyncio
async def test_distributed_lock_raises_on_contention(redis):
    """distributed_lock should raise if lock already held."""
    lock = RedisLock(redis)
    await lock.acquire("ctx:2", ttl_seconds=30)

    with pytest.raises(RuntimeError, match="Failed to acquire lock"):
        async with distributed_lock(redis, "ctx:2", ttl=30):
            pass  # Should not reach here

    await lock.release("ctx:2")
