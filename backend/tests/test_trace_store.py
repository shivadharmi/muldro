"""Tests for TraceStore service."""

from datetime import datetime, timezone

import pytest

from src.services.trace_store import TraceStore
from tests.conftest import TEST_USER_ID

# Use current time so traces aren't filtered out by the 24h time_range_hours default
_NOW = datetime.now(timezone.utc).isoformat()


@pytest.fixture
def store():
    return TraceStore()  # in-memory fallback


@pytest.mark.asyncio
async def test_store_and_get(store):
    trace = {
        "trace_id": "trace_001",
        "trigger": "user_message",
        "started_at": _NOW,
        "ended_at": "2026-03-16T10:00:02+00:00",
        "duration_ms": 2000,
        "spans": [],
    }
    result = await store.store_trace(trace, user_id=TEST_USER_ID)
    assert result == "trace_001"

    retrieved = await store.get_trace("trace_001")
    assert retrieved is not None
    assert retrieved["trigger"] == "user_message"


@pytest.mark.asyncio
async def test_get_nonexistent(store):
    result = await store.get_trace("trace_nonexistent")
    assert result is None


@pytest.mark.asyncio
async def test_search_by_trigger(store):
    for i in range(5):
        trigger = "briefing" if i % 2 == 0 else "user_message"
        await store.store_trace(
            {
                "trace_id": f"trace_{i}",
                "trigger": trigger,
                "started_at": _NOW,
                "spans": [],
            },
            user_id=TEST_USER_ID,
        )

    results = await store.search_traces(trigger="briefing")
    assert len(results) == 3
    assert all(r["trigger"] == "briefing" for r in results)


@pytest.mark.asyncio
async def test_search_by_agent_name(store):
    await store.store_trace(
        {
            "trace_id": "trace_agent",
            "trigger": "test",
            "started_at": _NOW,
            "spans": [{"agent_name": "planner", "duration_ms": 100}],
        },
        user_id=TEST_USER_ID,
    )
    await store.store_trace(
        {
            "trace_id": "trace_other",
            "trigger": "test",
            "started_at": _NOW,
            "spans": [{"agent_name": "observer", "duration_ms": 50}],
        },
        user_id=TEST_USER_ID,
    )

    results = await store.search_traces(agent_name="planner")
    assert len(results) == 1
    assert results[0]["trace_id"] == "trace_agent"


@pytest.mark.asyncio
async def test_search_limit(store):
    for i in range(10):
        await store.store_trace(
            {
                "trace_id": f"trace_{i}",
                "trigger": "test",
                "started_at": _NOW,
                "spans": [],
            },
            user_id=TEST_USER_ID,
        )

    results = await store.search_traces(limit=3)
    assert len(results) == 3


@pytest.mark.asyncio
async def test_agent_performance(store):
    await store.store_trace(
        {
            "trace_id": "trace_perf",
            "trigger": "test",
            "started_at": _NOW,
            "spans": [
                {
                    "agent_name": "planner",
                    "duration_ms": 200,
                    "input_tokens": 100,
                    "output_tokens": 50,
                },
                {
                    "agent_name": "planner",
                    "duration_ms": 300,
                    "input_tokens": 150,
                    "output_tokens": 75,
                },
                {
                    "agent_name": "observer",
                    "duration_ms": 50,
                    "input_tokens": 30,
                    "output_tokens": 10,
                    "error": "timeout",
                },
            ],
        },
        user_id=TEST_USER_ID,
    )

    perf = await store.get_agent_performance()
    assert "planner" in perf
    assert perf["planner"]["call_count"] == 2
    assert perf["planner"]["avg_duration_ms"] == 250
    assert perf["planner"]["total_input_tokens"] == 250
    assert perf["observer"]["error_count"] == 1


@pytest.mark.asyncio
async def test_ring_buffer_max_size():
    store = TraceStore()
    for i in range(600):
        await store.store_trace(
            {
                "trace_id": f"trace_{i}",
                "trigger": "test",
                "started_at": _NOW,
                "spans": [],
            },
            user_id=TEST_USER_ID,
        )
    # Ring buffer maxlen=500, so oldest should be evicted
    assert await store.get_trace("trace_0") is None
    assert await store.get_trace("trace_599") is not None
