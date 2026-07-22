# tests/test_write_lock_cross_path.py  — LOAD-BEARING guard for Fork B.
import uuid

import pytest
import redis.asyncio as redis_async

from src.config.settings import get_settings
from src.services.write_lock import WriteLockContended, acquire_write_lock


def _redis_reachable() -> bool:
    try:
        import redis

        redis.from_url(get_settings().redis_url).ping()
        return True
    except Exception:
        return False


pytestmark = pytest.mark.skipif(not _redis_reachable(), reason="requires live Redis")


async def test_deep_and_autonomous_paths_mutually_exclude_same_capability():
    """The deep middleware and the autonomous wrapper BOTH call acquire_write_lock with the
    same write_lock_key — so holding one blocks the other. This is the whole point of Fork B."""
    r = redis_async.from_url(get_settings().redis_url, decode_responses=True)
    cap = f"email.send.{uuid.uuid4().hex}"
    try:
        async with acquire_write_lock(r, "wsX", cap):  # "deep path" holds it
            with pytest.raises(WriteLockContended):  # "autonomous path" blocked
                async with acquire_write_lock(r, "wsX", cap, wait_timeout=0.3):
                    pass
        # NEGATIVE control: after release, the same key acquires cleanly.
        async with acquire_write_lock(r, "wsX", cap, wait_timeout=0.3):
            pass
    finally:
        await r.aclose()


async def test_different_capability_never_blocks():
    r = redis_async.from_url(get_settings().redis_url, decode_responses=True)
    cap_a = f"email.send.{uuid.uuid4().hex}"
    cap_b = f"calendar.create.{uuid.uuid4().hex}"
    try:
        async with acquire_write_lock(r, "wsX", cap_a):  # hold capability A
            # A DIFFERENT capability in the same workspace must acquire immediately.
            async with acquire_write_lock(r, "wsX", cap_b, wait_timeout=0.3):
                pass  # no WriteLockContended raised -> different caps don't collide
    finally:
        await r.aclose()
