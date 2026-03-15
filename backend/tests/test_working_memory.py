"""Tests for WorkingMemoryService."""

from datetime import datetime, timedelta, timezone
from unittest.mock import AsyncMock, MagicMock

import pytest

from src.services.working_memory import WorkingMemoryService


@pytest.fixture
def mock_db():
    """Mock AsyncSession."""
    return AsyncMock()


@pytest.fixture
def service(mock_db):
    return WorkingMemoryService(db=mock_db)


def make_mock_entry(**overrides):
    """Factory for mock WorkingMemoryEntry."""
    entry = MagicMock()
    defaults = {
        "entry_id": "wm_001",
        "user_id": "user_001",
        "session_id": None,
        "entry_type": "variable",
        "key": "test_key",
        "value": {"data": "test"},
        "ttl_seconds": 3600,
        "expires_at": datetime.now(timezone.utc) + timedelta(hours=1),
    }
    for key, value in {**defaults, **overrides}.items():
        setattr(entry, key, value)
    return entry


@pytest.mark.asyncio
async def test_set_creates_new_entry(service, mock_db):
    """Test set creates a new entry when key doesn't exist."""
    result = MagicMock()
    result.scalar_one_or_none.return_value = None
    mock_db.execute = AsyncMock(return_value=result)

    entry_id = await service.set(
        user_id="user_001",
        key="new_key",
        value={"data": "new"},
        ttl_seconds=1800,
    )

    assert entry_id.startswith("wm_")
    mock_db.add.assert_called_once()
    mock_db.flush.assert_called_once()


@pytest.mark.asyncio
async def test_set_updates_existing_entry(service, mock_db):
    """Test set updates existing entry (upsert behavior)."""
    existing = make_mock_entry()
    result = MagicMock()
    result.scalar_one_or_none.return_value = existing
    mock_db.execute = AsyncMock(return_value=result)

    entry_id = await service.set(
        user_id="user_001",
        key="test_key",
        value={"data": "updated"},
        ttl_seconds=1800,
    )

    assert entry_id == "wm_001"
    assert existing.value == {"data": "updated"}
    assert existing.ttl_seconds == 1800
    mock_db.add.assert_not_called()  # Should not add, only update
    mock_db.flush.assert_called_once()


@pytest.mark.asyncio
async def test_get_returns_value(service, mock_db):
    """Test get retrieves non-expired value."""
    entry = make_mock_entry(
        value={"data": "test"},
        expires_at=datetime.now(timezone.utc) + timedelta(hours=1),
    )
    result = MagicMock()
    result.scalar_one_or_none.return_value = entry
    mock_db.execute = AsyncMock(return_value=result)

    value = await service.get(user_id="user_001", key="test_key")

    assert value == {"data": "test"}


@pytest.mark.asyncio
async def test_get_returns_none_for_missing(service, mock_db):
    """Test get returns None when key doesn't exist."""
    result = MagicMock()
    result.scalar_one_or_none.return_value = None
    mock_db.execute = AsyncMock(return_value=result)

    value = await service.get(user_id="user_001", key="nonexistent")

    assert value is None


@pytest.mark.asyncio
async def test_get_returns_none_for_expired(service, mock_db):
    """Test get returns None for expired entries and deletes them."""
    expired_entry = make_mock_entry(
        expires_at=datetime.now(timezone.utc) - timedelta(hours=1),
    )
    result = MagicMock()
    result.scalar_one_or_none.return_value = expired_entry
    mock_db.execute = AsyncMock(return_value=result)

    value = await service.get(user_id="user_001", key="expired_key")

    assert value is None
    mock_db.delete.assert_called_once_with(expired_entry)
    mock_db.flush.assert_called_once()


@pytest.mark.asyncio
async def test_get_all_returns_non_expired_entries(service, mock_db):
    """Test get_all returns only non-expired entries."""
    now = datetime.now(timezone.utc)
    entries = [
        make_mock_entry(key="key1", value={"v": 1}, expires_at=now + timedelta(hours=1)),
        make_mock_entry(key="key2", value={"v": 2}, expires_at=now - timedelta(hours=1)),
        make_mock_entry(key="key3", value={"v": 3}, expires_at=now + timedelta(hours=2)),
    ]
    result = MagicMock()
    result.scalars.return_value.all.return_value = entries
    mock_db.execute = AsyncMock(return_value=result)

    data = await service.get_all(user_id="user_001")

    assert len(data) == 2
    assert "key1" in data
    assert "key3" in data
    assert "key2" not in data
    assert data["key1"] == {"v": 1}
    assert data["key3"] == {"v": 3}


@pytest.mark.asyncio
async def test_get_task_focus(service, mock_db):
    """Test get_task_focus returns active task focus entries."""
    now = datetime.now(timezone.utc)
    entries = [
        make_mock_entry(
            entry_type="task_focus",
            key="task_focus:task_001",
            value={"task": "test"},
            expires_at=now + timedelta(hours=1),
        ),
        make_mock_entry(
            entry_type="task_focus",
            key="task_focus:task_002",
            value={"task": "expired"},
            expires_at=now - timedelta(hours=1),
        ),
    ]
    result = MagicMock()
    result.scalars.return_value.all.return_value = entries
    mock_db.execute = AsyncMock(return_value=result)

    focus = await service.get_task_focus(user_id="user_001")

    assert len(focus) == 1
    assert focus[0]["key"] == "task_focus:task_001"
    assert focus[0]["value"] == {"task": "test"}


@pytest.mark.asyncio
async def test_set_task_focus(service, mock_db):
    """Test set_task_focus creates task_focus entry with long TTL."""
    result = MagicMock()
    result.scalar_one_or_none.return_value = None
    mock_db.execute = AsyncMock(return_value=result)

    entry_id = await service.set_task_focus(
        user_id="user_001",
        task_id="task_001",
        context={"goal": "test"},
    )

    assert entry_id.startswith("wm_")
    mock_db.add.assert_called_once()

    # Verify the entry created has task_focus type and 24h TTL
    added_entry = mock_db.add.call_args[0][0]
    assert added_entry.entry_type == "task_focus"
    assert added_entry.key == "task_focus:task_001"
    assert added_entry.ttl_seconds == 86400


@pytest.mark.asyncio
async def test_delete_removes_entry(service, mock_db):
    """Test delete removes specific entry."""
    await service.delete(user_id="user_001", key="test_key")

    mock_db.execute.assert_called_once()
    mock_db.flush.assert_called_once()


@pytest.mark.asyncio
async def test_clear_session(service, mock_db):
    """Test clear_session removes all entries for session."""
    result = MagicMock()
    result.rowcount = 5
    mock_db.execute = AsyncMock(return_value=result)

    count = await service.clear_session(session_id="sess_001")

    assert count == 5
    mock_db.flush.assert_called_once()


@pytest.mark.asyncio
async def test_cleanup_expired(service, mock_db):
    """Test cleanup_expired removes all expired entries."""
    result = MagicMock()
    result.rowcount = 10
    mock_db.execute = AsyncMock(return_value=result)

    count = await service.cleanup_expired()

    assert count == 10
    mock_db.flush.assert_called_once()


@pytest.mark.asyncio
async def test_cleanup_expired_no_entries(service, mock_db):
    """Test cleanup_expired returns 0 when no expired entries."""
    result = MagicMock()
    result.rowcount = 0
    mock_db.execute = AsyncMock(return_value=result)

    count = await service.cleanup_expired()

    assert count == 0
