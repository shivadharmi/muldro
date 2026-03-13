"""Tests for RedisCache using fakeredis."""

import pytest

from src.services.cache import RedisCache

try:
    import fakeredis.aioredis as fakeredis_aio
except ImportError:
    fakeredis_aio = None


@pytest.fixture
async def redis():
    if fakeredis_aio is None:
        pytest.skip("fakeredis not installed")
    r = fakeredis_aio.FakeRedis(decode_responses=True)
    yield r
    await r.aclose()


@pytest.fixture
def cache(redis):
    return RedisCache(redis)


@pytest.mark.asyncio
async def test_get_set_string(cache):
    """Basic string get/set."""
    await cache.set("key1", "value1", ttl_seconds=60)
    result = await cache.get("key1")
    assert result == "value1"


@pytest.mark.asyncio
async def test_get_missing_key(cache):
    """Getting a missing key returns None."""
    result = await cache.get("nonexistent")
    assert result is None


@pytest.mark.asyncio
async def test_delete(cache):
    """Delete removes a key."""
    await cache.set("key2", "value2")
    await cache.delete("key2")
    result = await cache.get("key2")
    assert result is None


@pytest.mark.asyncio
async def test_get_set_json(cache):
    """JSON get/set round-trips correctly."""
    data = {"headline": "3 priorities", "count": 5}
    await cache.set_json("brief:usr_default:2026-03-14", data, ttl_seconds=3600)
    result = await cache.get_json("brief:usr_default:2026-03-14")
    assert result == data


@pytest.mark.asyncio
async def test_get_json_missing(cache):
    """Getting missing JSON key returns None."""
    result = await cache.get_json("nonexistent")
    assert result is None


@pytest.mark.asyncio
async def test_exists(cache):
    """exists returns True for set keys, False for missing."""
    assert not await cache.exists("missing")
    await cache.set("present", "yes")
    assert await cache.exists("present")


@pytest.mark.asyncio
async def test_incr_with_ttl(cache):
    """incr_with_ttl increments and returns count."""
    count1 = await cache.incr_with_ttl("counter:test", ttl_seconds=60)
    assert count1 == 1
    count2 = await cache.incr_with_ttl("counter:test", ttl_seconds=60)
    assert count2 == 2
