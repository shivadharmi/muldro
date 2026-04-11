"""Tests for memory expiration scheduler tick."""

from datetime import datetime, timedelta, timezone
from unittest.mock import AsyncMock, MagicMock

import pytest

from tests.conftest import make_mock_settings


@pytest.mark.asyncio
async def test_tick_memory_expiration_marks_expired():
    """Expired memories should be marked 'expired' and deleted from Qdrant."""
    from src.services.scheduler import SchedulerLoop

    settings = make_mock_settings()
    scheduler = SchedulerLoop(settings=settings)

    fake_mem = MagicMock()
    fake_mem.memory_id = "mem_expired1"
    fake_mem.status = "active"
    fake_mem.ttl_days = 7
    fake_mem.created_at = datetime.now(timezone.utc) - timedelta(days=10)

    mock_result = MagicMock()
    mock_result.scalars.return_value = [fake_mem]

    mock_db = AsyncMock()
    mock_db.execute = AsyncMock(return_value=mock_result)
    mock_db.commit = AsyncMock()

    mock_vector_store = AsyncMock()

    mock_factory = MagicMock()
    mock_factory.return_value.__aenter__ = AsyncMock(return_value=mock_db)
    mock_factory.return_value.__aexit__ = AsyncMock(return_value=False)

    await scheduler._tick_memory_expiration(mock_factory, mock_vector_store)

    assert fake_mem.status == "expired"
    mock_vector_store.delete.assert_called_once_with("memories", "mem_expired1")
    mock_db.commit.assert_called_once()


@pytest.mark.asyncio
async def test_tick_memory_expiration_no_expired():
    """When no memories are expired, no Qdrant deletes should happen."""
    from src.services.scheduler import SchedulerLoop

    settings = make_mock_settings()
    scheduler = SchedulerLoop(settings=settings)

    mock_result = MagicMock()
    mock_result.scalars.return_value = []

    mock_db = AsyncMock()
    mock_db.execute = AsyncMock(return_value=mock_result)
    mock_db.commit = AsyncMock()

    mock_vector_store = AsyncMock()

    mock_factory = MagicMock()
    mock_factory.return_value.__aenter__ = AsyncMock(return_value=mock_db)
    mock_factory.return_value.__aexit__ = AsyncMock(return_value=False)

    await scheduler._tick_memory_expiration(mock_factory, mock_vector_store)

    mock_vector_store.delete.assert_not_called()


@pytest.mark.asyncio
async def test_tick_memory_expiration_qdrant_failure_graceful():
    """Qdrant delete failure should not prevent other memories from being expired."""
    from src.services.scheduler import SchedulerLoop

    settings = make_mock_settings()
    scheduler = SchedulerLoop(settings=settings)

    fake_mem1 = MagicMock()
    fake_mem1.memory_id = "mem_1"
    fake_mem1.status = "active"
    fake_mem2 = MagicMock()
    fake_mem2.memory_id = "mem_2"
    fake_mem2.status = "active"

    mock_result = MagicMock()
    mock_result.scalars.return_value = [fake_mem1, fake_mem2]

    mock_db = AsyncMock()
    mock_db.execute = AsyncMock(return_value=mock_result)
    mock_db.commit = AsyncMock()

    mock_vector_store = AsyncMock()
    mock_vector_store.delete = AsyncMock(side_effect=[Exception("Qdrant down"), None])

    mock_factory = MagicMock()
    mock_factory.return_value.__aenter__ = AsyncMock(return_value=mock_db)
    mock_factory.return_value.__aexit__ = AsyncMock(return_value=False)

    await scheduler._tick_memory_expiration(mock_factory, mock_vector_store)

    # Both should be marked expired even though first Qdrant delete failed
    assert fake_mem1.status == "expired"
    assert fake_mem2.status == "expired"
    mock_db.commit.assert_called_once()
