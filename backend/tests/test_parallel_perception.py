"""Tests for parallel perception source processing."""

import asyncio
import time

import pytest


@pytest.mark.asyncio
async def test_concurrent_sources_faster_than_sequential():
    """Parallel execution of 3 sources should be faster than sequential."""
    call_log = []

    async def mock_cycle(source):
        call_log.append(("start", source, time.monotonic()))
        await asyncio.sleep(0.05)
        call_log.append(("end", source, time.monotonic()))
        return {"status": "completed", "events": 1}

    sources = ["gmail", "calendar", "slack"]

    start = time.monotonic()
    sem = asyncio.Semaphore(5)

    async def bounded(s):
        async with sem:
            return await mock_cycle(s)

    await asyncio.gather(*(bounded(s) for s in sources))
    elapsed = time.monotonic() - start

    assert elapsed < 0.12  # Parallel ~50ms, not sequential ~150ms
    assert len(call_log) == 6


@pytest.mark.asyncio
async def test_semaphore_limits_concurrency():
    """Semaphore should limit concurrent perception cycles."""
    active = 0
    max_active = 0

    async def mock_cycle(source):
        nonlocal active, max_active
        active += 1
        max_active = max(max_active, active)
        await asyncio.sleep(0.02)
        active -= 1
        return {"status": "completed", "events": 0}

    sources = [f"source_{i}" for i in range(10)]
    sem = asyncio.Semaphore(3)

    async def bounded(s):
        async with sem:
            return await mock_cycle(s)

    await asyncio.gather(*(bounded(s) for s in sources))

    assert max_active <= 3
    assert active == 0
