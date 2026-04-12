"""Tests for SchedulerLoop._tick_consolidation (Task 6 / Plan 6A)."""

import logging
from contextlib import asynccontextmanager
from unittest.mock import AsyncMock, MagicMock, patch

import pytest

from tests.conftest import TEST_USER_ID, make_mock_settings

# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------


def make_scheduler():
    """Return a SchedulerLoop with a mock settings object."""
    from src.services.scheduler import SchedulerLoop

    settings = make_mock_settings()
    return SchedulerLoop(settings=settings)


def make_db_factory(user_ids: list[str]):
    """Return a DB session factory stub that yields user_ids from Memory query."""
    mock_db = AsyncMock()

    # Build the result rows for the distinct(user_id) query
    rows = [(uid,) for uid in user_ids]
    mock_result = MagicMock()
    mock_result.all.return_value = rows
    mock_db.execute = AsyncMock(return_value=mock_result)
    mock_db.commit = AsyncMock()

    @asynccontextmanager
    async def _factory():
        yield mock_db

    return _factory, mock_db


# ---------------------------------------------------------------------------
# Tests
# ---------------------------------------------------------------------------


@pytest.mark.asyncio
async def test_consolidates_for_all_active_users():
    """_tick_consolidation calls consolidate_memories for every active user."""
    uid1 = TEST_USER_ID
    uid2 = "usr_01JTEST00000000000000000001"

    factory, mock_db = make_db_factory([uid1, uid2])
    scheduler = make_scheduler()

    ms_instance = AsyncMock()
    ms_instance.consolidate_memories = AsyncMock(return_value=3)

    with patch(
        "src.services.memory_service.MemoryService",
        return_value=ms_instance,
    ):
        await scheduler._tick_consolidation(factory)

    # consolidate_memories was called once per user
    assert ms_instance.consolidate_memories.call_count == 2
    called_user_ids = {call.args[0] for call in ms_instance.consolidate_memories.call_args_list}
    assert called_user_ids == {uid1, uid2}

    # DB was committed
    mock_db.commit.assert_awaited_once()


@pytest.mark.asyncio
async def test_handles_no_active_users():
    """_tick_consolidation completes without error when no active users exist."""
    factory, mock_db = make_db_factory([])
    scheduler = make_scheduler()

    ms_instance = AsyncMock()
    ms_instance.consolidate_memories = AsyncMock(return_value=0)

    with patch(
        "src.services.memory_service.MemoryService",
        return_value=ms_instance,
    ):
        # Should not raise
        await scheduler._tick_consolidation(factory)

    # No consolidation calls made for any user
    ms_instance.consolidate_memories.assert_not_awaited()
    # DB commit still called (empty loop, commit reached)
    mock_db.commit.assert_awaited_once()


@pytest.mark.asyncio
async def test_logs_warning_on_failure(caplog):
    """_tick_consolidation catches exceptions and logs a warning without re-raising."""
    scheduler = make_scheduler()

    @asynccontextmanager
    async def _bad_factory():
        raise RuntimeError("DB connection refused")
        yield  # pragma: no cover — needed for asynccontextmanager protocol

    with caplog.at_level(logging.WARNING, logger="src.services.scheduler"):
        # Must not propagate the exception
        await scheduler._tick_consolidation(_bad_factory)

    assert any("Memory consolidation tick failed" in record.message for record in caplog.records)
