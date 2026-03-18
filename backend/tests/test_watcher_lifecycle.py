"""Tests for Phase 4A: Watcher lifecycle CRUD via WatcherService."""

from datetime import datetime, timedelta, timezone
from unittest.mock import AsyncMock, MagicMock

from src.services.watcher_service import WatcherService


def _make_watcher_service(db=None):
    db = db or AsyncMock()
    db.add = MagicMock()
    db.flush = AsyncMock()
    return WatcherService(db=db)


def _make_trigger(trigger_id="trg_001", name="Test Watcher", status="active", enabled=True):
    t = MagicMock()
    t.trigger_id = trigger_id
    t.name = name
    t.conditions = {"source": "gmail", "event_type": "email_received"}
    t.action_type = "notify"
    t.status = status
    t.enabled = enabled
    return t


class TestWatcherCreate:
    async def test_create_watcher_returns_trigger_id(self):
        ws = _make_watcher_service()
        result = await ws.create_watcher(
            user_id="usr_1",
            name="Track investor emails",
            conditions={"source": "gmail", "keywords": ["investor"]},
        )
        assert "trigger_id" in result
        assert result["trigger_id"].startswith("trg_")
        assert result["status"] == "active"

    async def test_create_watcher_adds_to_db(self):
        db = AsyncMock()
        db.add = MagicMock()
        db.flush = AsyncMock()
        ws = WatcherService(db=db)

        await ws.create_watcher(
            user_id="usr_1",
            name="Calendar watcher",
            conditions={"source": "calendar"},
            action_type="plan",
        )
        db.add.assert_called_once()
        db.flush.assert_called_once()

    async def test_create_watcher_custom_action_type(self):
        ws = _make_watcher_service()
        result = await ws.create_watcher(
            user_id="usr_1",
            name="Escalation watcher",
            conditions={"priority": "critical"},
            action_type="escalate",
        )
        assert result["status"] == "active"


class TestWatcherGet:
    async def test_get_existing_watcher(self):
        trigger = _make_trigger()
        db = AsyncMock()
        mock_result = MagicMock()
        mock_result.scalar_one_or_none.return_value = trigger
        db.execute = AsyncMock(return_value=mock_result)

        ws = WatcherService(db=db)
        result = await ws.get_watcher("trg_001")

        assert result is not None
        assert result["trigger_id"] == "trg_001"
        assert result["name"] == "Test Watcher"
        assert result["status"] == "active"

    async def test_get_nonexistent_watcher(self):
        db = AsyncMock()
        mock_result = MagicMock()
        mock_result.scalar_one_or_none.return_value = None
        db.execute = AsyncMock(return_value=mock_result)

        ws = WatcherService(db=db)
        result = await ws.get_watcher("trg_nonexistent")
        assert result is None


class TestWatcherDisable:
    async def test_disable_sets_status(self):
        trigger = _make_trigger()
        db = AsyncMock()
        mock_result = MagicMock()
        mock_result.scalar_one_or_none.return_value = trigger
        db.execute = AsyncMock(return_value=mock_result)
        db.flush = AsyncMock()

        ws = WatcherService(db=db)
        await ws.disable_watcher("trg_001")

        assert trigger.enabled is False
        assert trigger.status == "disabled"

    async def test_disable_nonexistent_is_noop(self):
        db = AsyncMock()
        mock_result = MagicMock()
        mock_result.scalar_one_or_none.return_value = None
        db.execute = AsyncMock(return_value=mock_result)

        ws = WatcherService(db=db)
        # Should not raise
        await ws.disable_watcher("trg_nonexistent")


class TestWatcherSnooze:
    async def test_snooze_sets_status_and_conditions(self):
        trigger = _make_trigger()
        db = AsyncMock()
        mock_result = MagicMock()
        mock_result.scalar_one_or_none.return_value = trigger
        db.execute = AsyncMock(return_value=mock_result)
        db.flush = AsyncMock()

        until = datetime.now(timezone.utc) + timedelta(hours=2)
        ws = WatcherService(db=db)
        await ws.snooze_watcher("trg_001", until=until)

        assert trigger.enabled is False
        assert trigger.status == "snoozed"
        assert "snooze_until" in trigger.conditions

    async def test_snooze_nonexistent_is_noop(self):
        db = AsyncMock()
        mock_result = MagicMock()
        mock_result.scalar_one_or_none.return_value = None
        db.execute = AsyncMock(return_value=mock_result)

        until = datetime.now(timezone.utc) + timedelta(hours=1)
        ws = WatcherService(db=db)
        await ws.snooze_watcher("trg_nonexistent", until=until)
