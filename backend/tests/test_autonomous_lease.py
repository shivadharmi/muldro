# tests/test_autonomous_lease.py
"""Step 10C P3: single-flight Redis lease for durable autonomous runs.

Real Redis, self-contained (module-level ``_redis_reachable`` skipif, matching
``tests/test_write_lock.py`` / ``tests/test_runtime_gate.py``). Every test
UUID-suffixes its run_id so the lease key (``lease:run:run_<uuid>``) never
collides with a shared ``:6379`` instance running other projects/tests, and
cleans up its own key in a ``finally`` block.

Groups:
  1. single-flight   — exactly one caller acquires; the other backs off; after
                       release a fresh acquire succeeds again.
  2. TTL auto-expiry — a crashed holder's lease expires so the next acquire wins
                       (no deadlock).
  3. owner-token CAS — a stale holder's release never deletes a NEW owner's key.
  4. integration     — deep-gated GraphExecutor: two concurrent execute/resume on
                       the SAME run_id → exactly one drives the body, the other
                       backs off (returns the run without re-driving). Legacy gate
                       (default) → no lease, body always runs (byte-neutral).
"""

import asyncio
import uuid
from unittest.mock import AsyncMock, MagicMock, patch

import pytest
import redis.asyncio as redis_async

from src.config.settings import get_settings
from src.services.autonomous_lease import (
    RUN_LEASE_TTL_SECONDS,
    acquire_run_lease,
    run_lease_key,
)


def _redis_reachable() -> bool:
    try:
        import redis

        redis.from_url(get_settings().redis_url).ping()
        return True
    except Exception:
        return False


pytestmark = pytest.mark.skipif(not _redis_reachable(), reason="requires live Redis")


# ── 0. key + constant shape ──────────────────────────────────────────


def test_run_lease_key_is_deterministic_and_run_scoped():
    assert run_lease_key("run_abc") == "lease:run:run_abc"
    assert run_lease_key("run_abc") != run_lease_key("run_def")


def test_ttl_exceeds_background_execution_timeout():
    # The 600s background execution timeout must be covered by the lease TTL so a
    # crashed holder's lease auto-expires only AFTER the run itself would have died.
    assert RUN_LEASE_TTL_SECONDS > 600


# ── 1. single-flight ─────────────────────────────────────────────────


async def test_single_flight_one_acquires_other_backs_off():
    r = redis_async.from_url(get_settings().redis_url, decode_responses=True)
    run_id = f"run_{uuid.uuid4().hex}"
    key = run_lease_key(run_id)
    try:
        async with acquire_run_lease(r, run_id) as first:
            assert first is True  # THIS caller drives
            # A second acquire while the first is still held must back off.
            async with acquire_run_lease(r, run_id) as second:
                assert second is False  # another worker holds it → do NOT drive
            # The failed acquire must NOT have released the first owner's lease.
            assert await r.get(key) is not None
        # After the first releases (context exit), a third acquire wins again.
        async with acquire_run_lease(r, run_id) as third:
            assert third is True
    finally:
        await r.delete(key)
        await r.aclose()


# ── 2. TTL auto-expiry (crashed holder never deadlocks) ──────────────


async def test_ttl_auto_expiry_lets_next_acquire_succeed():
    r = redis_async.from_url(get_settings().redis_url, decode_responses=True)
    run_id = f"run_{uuid.uuid4().hex}"
    key = run_lease_key(run_id)
    try:
        # Simulate a CRASHED holder: raw SET NX PX with a short TTL, never released.
        crashed_token = uuid.uuid4().hex
        assert await r.set(key, crashed_token, nx=True, px=1000) is True
        # While the crashed holder's lease is still live, a fresh acquire backs off.
        async with acquire_run_lease(r, run_id) as blocked:
            assert blocked is False
        await asyncio.sleep(1.3)  # crashed holder's lease TTL-expires
        # After expiry the next tick re-acquires — no deadlock.
        async with acquire_run_lease(r, run_id) as reacquired:
            assert reacquired is True
    finally:
        await r.delete(key)
        await r.aclose()


# ── 3. owner-token CAS release ───────────────────────────────────────


async def test_owner_token_cas_release_does_not_delete_new_owners_key():
    r = redis_async.from_url(get_settings().redis_url, decode_responses=True)
    run_id = f"run_{uuid.uuid4().hex}"
    key = run_lease_key(run_id)
    try:
        # (1) The stored value is a 32-char hex owner token, NOT a constant.
        async with acquire_run_lease(r, run_id, ttl_s=1) as a:
            assert a is True
            token_a = await r.get(key)
            assert token_a is not None
            assert len(token_a) == 32
            int(token_a, 16)  # raises ValueError if not hex — proves it's a uuid4 token

            await asyncio.sleep(1.2)  # owner A's lease TTL-expires
            token_b = uuid.uuid4().hex
            assert await r.set(key, token_b, nx=True, px=120_000) is True  # B grabs free key
            # Exiting A's context now runs A's CAS release with token_a; it must NOT
            # delete token_b. An unconditional DEL would fail the assertion below.
        assert await r.get(key) == token_b  # B's lease survived A's stale release
    finally:
        await r.delete(key)
        await r.aclose()


# ── 4. integration (deep-gated GraphExecutor) ────────────────────────


def _make_executor(redis=None):
    with patch("src.services.graph_executor.get_anthropic_client") as mock_client:
        mock_client.return_value = MagicMock()
        from src.services.graph_executor import GraphExecutor
        from tests.conftest import make_mock_settings

        return GraphExecutor(make_mock_settings(), AsyncMock(), redis=redis)


def _stub_run(run_id):
    run = MagicMock()
    run.run_id = run_id
    return run


async def test_deep_gate_execute_run_single_flight_only_one_drives():
    r = redis_async.from_url(get_settings().redis_url, decode_responses=True)
    run_id = f"run_{uuid.uuid4().hex}"
    key = run_lease_key(run_id)
    try:
        executor = _make_executor(redis=r)
        run = _stub_run(run_id)

        # Back-off path re-fetches the run via self._db.execute(select(TaskRun)...).
        mock_result = MagicMock()
        mock_result.scalar_one_or_none.return_value = run
        executor._db.execute = AsyncMock(return_value=mock_result)

        # Spy on the extracted body; hold the lease across an await so the second
        # concurrent caller genuinely observes it held and backs off.
        async def slow_body(rid, **kwargs):
            await asyncio.sleep(0.3)
            return run

        body_spy = AsyncMock(side_effect=slow_body)
        executor._execute_run_body = body_spy

        results = await asyncio.gather(
            executor.execute_run(run_id),
            executor.execute_run(run_id),
        )

        assert body_spy.call_count == 1  # exactly one worker drove the run
        assert all(res is run for res in results)  # both return a valid TaskRun
    finally:
        await r.delete(key)
        await r.aclose()


async def test_deep_gate_resume_run_single_flight_only_one_drives():
    r = redis_async.from_url(get_settings().redis_url, decode_responses=True)
    run_id = f"run_{uuid.uuid4().hex}"
    key = run_lease_key(run_id)
    try:
        executor = _make_executor(redis=r)
        run = _stub_run(run_id)

        mock_result = MagicMock()
        mock_result.scalar_one_or_none.return_value = run
        executor._db.execute = AsyncMock(return_value=mock_result)

        async def slow_body(rid, **kwargs):
            await asyncio.sleep(0.3)
            return run

        body_spy = AsyncMock(side_effect=slow_body)
        executor._resume_run_body = body_spy

        results = await asyncio.gather(
            executor.resume_run(run_id),
            executor.resume_run(run_id),
        )

        assert body_spy.call_count == 1
        assert all(res is run for res in results)
    finally:
        await r.delete(key)
        await r.aclose()
