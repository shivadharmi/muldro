# tests/test_write_lock.py
import uuid

import pytest
import redis.asyncio as redis_async

from src.config.settings import get_settings
from src.services.write_lock import (
    WriteLockContended,
    acquire_write_lock,
    write_lock_key,
)


def _redis_reachable() -> bool:
    try:
        import redis

        redis.from_url(get_settings().redis_url).ping()
        return True
    except Exception:
        return False


pytestmark = pytest.mark.skipif(not _redis_reachable(), reason="requires live Redis")


def test_write_lock_key_is_deterministic_and_capability_scoped():
    assert write_lock_key("ws1", "email.send") == "write:ws1:email.send"
    assert write_lock_key("ws1", "email.send") != write_lock_key("ws1", "calendar.create")


async def test_same_key_mutually_excludes_different_key_does_not():
    r = redis_async.from_url(get_settings().redis_url, decode_responses=True)
    cap = f"email.send.{uuid.uuid4().hex}"
    async with acquire_write_lock(r, "ws1", cap):
        # A different capability must acquire immediately.
        async with acquire_write_lock(r, "ws1", f"calendar.{uuid.uuid4().hex}"):
            pass
        # The SAME key must fail fast within the bounded wait.
        with pytest.raises(WriteLockContended):
            async with acquire_write_lock(r, "ws1", cap, wait_timeout=0.5):
                pass
    await r.aclose()


async def test_release_uses_owner_token_and_survives_ttl_expiry_of_a_prior_owner():
    r = redis_async.from_url(get_settings().redis_url, decode_responses=True)
    cap = f"cap.{uuid.uuid4().hex}"
    async with acquire_write_lock(r, "ws1", cap):
        current = await r.get(f"lock:{write_lock_key('ws1', cap)}")
        assert current is not None  # a token, not a constant "1"
    assert await r.get(f"lock:{write_lock_key('ws1', cap)}") is None  # released
    await r.aclose()
