"""Tests for dead-letter queue service."""

from unittest.mock import AsyncMock, MagicMock

import pytest

from src.services.dead_letter import DeadLetterService


@pytest.fixture
def mock_db():
    db = MagicMock()
    db.add = MagicMock()
    db.flush = AsyncMock()
    db.execute = AsyncMock()
    return db


@pytest.mark.asyncio
async def test_enqueue(mock_db):
    """Should add a failed operation to the dead-letter queue."""
    dlq = DeadLetterService(mock_db)

    entry_id = await dlq.enqueue(
        user_id="usr_default",
        operation_type="event_processing",
        error_type="ValueError",
        error_message="Something went wrong",
        source_id="evt_001",
        payload={"event_id": "evt_001"},
    )

    assert entry_id.startswith("dlq_")
    mock_db.add.assert_called_once()
    mock_db.flush.assert_awaited_once()


@pytest.mark.asyncio
async def test_enqueue_truncates_long_message(mock_db):
    """Should truncate error messages longer than 2000 chars."""
    dlq = DeadLetterService(mock_db)

    long_msg = "x" * 5000
    await dlq.enqueue(
        user_id="usr_default",
        operation_type="embedding",
        error_type="RuntimeError",
        error_message=long_msg,
    )

    call_args = mock_db.add.call_args
    entry = call_args[0][0]
    assert len(entry.error_message) == 2000


@pytest.mark.asyncio
async def test_mark_retrying_increments_attempt(mock_db):
    """Should increment attempt count and set status to retrying."""
    entry = MagicMock()
    entry.attempt_count = 1
    entry.max_attempts = 3
    entry.status = "pending"

    result_mock = MagicMock()
    result_mock.scalar_one_or_none.return_value = entry
    mock_db.execute.return_value = result_mock

    dlq = DeadLetterService(mock_db)
    result = await dlq.mark_retrying("dlq_001")

    assert result is True
    assert entry.attempt_count == 2
    assert entry.status == "retrying"


@pytest.mark.asyncio
async def test_mark_retrying_exhausted(mock_db):
    """Should mark as exhausted when max attempts reached."""
    entry = MagicMock()
    entry.attempt_count = 3
    entry.max_attempts = 3
    entry.status = "retrying"

    result_mock = MagicMock()
    result_mock.scalar_one_or_none.return_value = entry
    mock_db.execute.return_value = result_mock

    dlq = DeadLetterService(mock_db)
    result = await dlq.mark_retrying("dlq_001")

    assert result is False
    assert entry.status == "exhausted"


@pytest.mark.asyncio
async def test_mark_resolved(mock_db):
    """Should update entry status to resolved."""
    dlq = DeadLetterService(mock_db)
    await dlq.mark_resolved("dlq_001")

    mock_db.execute.assert_awaited_once()
    mock_db.flush.assert_awaited_once()


@pytest.mark.asyncio
async def test_get_stats_empty(mock_db):
    """Should return zero stats when queue is empty."""
    result_mock = MagicMock()
    result_mock.all.return_value = []
    mock_db.execute.return_value = result_mock

    dlq = DeadLetterService(mock_db)
    stats = await dlq.get_stats("usr_default")

    assert stats["total"] == 0
    assert stats["by_status"] == {}
    assert stats["by_operation"] == {}


@pytest.mark.asyncio
async def test_get_stats_with_entries(mock_db):
    """Should count entries by status and operation type."""
    result_mock = MagicMock()
    result_mock.all.return_value = [
        ("pending", "event_processing"),
        ("pending", "event_processing"),
        ("exhausted", "embedding"),
    ]
    mock_db.execute.return_value = result_mock

    dlq = DeadLetterService(mock_db)
    stats = await dlq.get_stats("usr_default")

    assert stats["total"] == 3
    assert stats["by_status"]["pending"] == 2
    assert stats["by_status"]["exhausted"] == 1
    assert stats["by_operation"]["event_processing"] == 2
