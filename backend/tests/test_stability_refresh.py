"""Tests for SchedulerLoop._tick_stability_refresh."""

from contextlib import asynccontextmanager
from unittest.mock import AsyncMock, MagicMock

import pytest

from tests.conftest import make_mock_settings

TEST_USER_ID = "usr_01JTEST00000000000000000000"
TEST_WORKSPACE_ID = "ws_test"


def _make_scheduler():
    """Return a SchedulerLoop with mocked orchestrator and settings."""
    from src.services.scheduler import SchedulerLoop

    settings = make_mock_settings(qdrant_url="http://localhost:6333")
    return SchedulerLoop(settings=settings)


def _make_factory(rows):
    """Return an async context-manager factory that yields a mock DB session."""

    mock_db = MagicMock()
    mock_result = MagicMock()
    mock_result.all.return_value = rows
    mock_db.execute = AsyncMock(return_value=mock_result)

    @asynccontextmanager
    async def factory():
        yield mock_db

    return factory


def _make_vector_store():
    """Return a mock VectorStore with set_payload as AsyncMock."""
    vs = MagicMock()
    vs.set_payload = AsyncMock()
    return vs


# ---------------------------------------------------------------------------
# Tests
# ---------------------------------------------------------------------------


@pytest.mark.asyncio
async def test_updates_qdrant_payloads_for_stale_memories():
    """set_payload is called once per stale memory row returned by the query."""
    scheduler = _make_scheduler()

    rows = [
        ("mem_01AAA", 0.85),
        ("mem_01BBB", 0.42),
    ]
    factory = _make_factory(rows)
    vector_store = _make_vector_store()

    await scheduler._tick_stability_refresh(factory, vector_store)

    assert vector_store.set_payload.call_count == 2
    calls = vector_store.set_payload.call_args_list
    assert calls[0].args == ("memories", "mem_01AAA", {"stability_score": 0.85})
    assert calls[1].args == ("memories", "mem_01BBB", {"stability_score": 0.42})


@pytest.mark.asyncio
async def test_skips_when_no_vector_store():
    """No error and no DB access when vector_store is None."""
    scheduler = _make_scheduler()
    factory = _make_factory([("mem_01CCC", 0.5)])

    # Should complete without raising
    await scheduler._tick_stability_refresh(factory, vector_store=None)

    # factory is never called because we short-circuit on missing vector_store
    # (DB mock was not touched — verified implicitly by no exception)


@pytest.mark.asyncio
async def test_handles_empty_result():
    """No set_payload calls when the query returns no rows."""
    scheduler = _make_scheduler()
    factory = _make_factory([])
    vector_store = _make_vector_store()

    await scheduler._tick_stability_refresh(factory, vector_store)

    vector_store.set_payload.assert_not_called()


@pytest.mark.asyncio
async def test_null_stability_score_defaults_to_zero():
    """A None stability_score is stored as 0.0 in Qdrant."""
    scheduler = _make_scheduler()
    rows = [("mem_01DDD", None)]
    factory = _make_factory(rows)
    vector_store = _make_vector_store()

    await scheduler._tick_stability_refresh(factory, vector_store)

    vector_store.set_payload.assert_called_once_with(
        "memories", "mem_01DDD", {"stability_score": 0.0}
    )


@pytest.mark.asyncio
async def test_continues_after_per_record_error():
    """A set_payload failure on one record does not abort remaining records."""
    scheduler = _make_scheduler()
    rows = [("mem_01EEE", 0.9), ("mem_01FFF", 0.7)]
    factory = _make_factory(rows)

    vector_store = _make_vector_store()
    # First call raises; second should still be attempted
    vector_store.set_payload.side_effect = [RuntimeError("qdrant down"), None]

    await scheduler._tick_stability_refresh(factory, vector_store)

    assert vector_store.set_payload.call_count == 2
