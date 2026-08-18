"""Tests for TriggerEngine — reactive event triggers."""

from unittest.mock import AsyncMock, MagicMock

import pytest

from src.services.event_bus import BusEvent
from src.services.trigger_engine import TriggerEngine
from tests.conftest import TEST_USER_ID


@pytest.fixture
def mock_db():
    db = AsyncMock()
    db.add = MagicMock()
    db.flush = AsyncMock()
    db.commit = AsyncMock()
    db.delete = AsyncMock()
    return db


@pytest.fixture
def engine(mock_db):
    return TriggerEngine(mock_db)


def _make_trigger(
    trigger_id="trg_001",
    conditions=None,
    action_type="notify",
    action_config=None,
    enabled=True,
):
    t = MagicMock()
    t.trigger_id = trigger_id
    t.user_id = TEST_USER_ID
    t.name = "Test trigger"
    t.conditions = conditions or {}
    t.action_type = action_type
    t.action_config = action_config or {}
    t.enabled = enabled
    t.fire_count = 0
    t.last_fired_at = None
    return t


def _make_bus_event(
    event_type="email_received",
    source="gmail",
    importance_score=0.8,
    user_id=TEST_USER_ID,
):
    return BusEvent(
        event_id="be_test",
        stream=f"muldro:events:{TEST_USER_ID}",
        event_type=event_type,
        user_id=user_id,
        payload={
            "event_id": "evt_001",
            "source": source,
            "importance_score": importance_score,
        },
    )


class TestCreateTrigger:
    async def test_creates_trigger(self, engine, mock_db):
        await engine.create_trigger(
            user_id=TEST_USER_ID,
            name="High priority email",
            conditions={"event_type": "email_received", "importance_threshold": 0.9},
            action_type="notify",
            action_config={"message": "Important email!"},
        )
        mock_db.add.assert_called_once()
        mock_db.commit.assert_called_once()


class TestEvaluate:
    async def test_matches_event_type(self, engine, mock_db):
        trigger = _make_trigger(conditions={"event_type": "email_received"})
        result_mock = MagicMock()
        result_mock.scalars.return_value.all.return_value = [trigger]
        mock_db.execute = AsyncMock(return_value=result_mock)

        event = _make_bus_event(event_type="email_received")
        fired = await engine.evaluate(event)

        assert len(fired) == 1
        assert fired[0]["trigger_id"] == "trg_001"
        assert trigger.fire_count == 1

    async def test_no_match_on_different_type(self, engine, mock_db):
        trigger = _make_trigger(conditions={"event_type": "pr_opened"})
        result_mock = MagicMock()
        result_mock.scalars.return_value.all.return_value = [trigger]
        mock_db.execute = AsyncMock(return_value=result_mock)

        event = _make_bus_event(event_type="email_received")
        fired = await engine.evaluate(event)
        assert len(fired) == 0

    async def test_matches_source(self, engine, mock_db):
        trigger = _make_trigger(conditions={"source": "github"})
        result_mock = MagicMock()
        result_mock.scalars.return_value.all.return_value = [trigger]
        mock_db.execute = AsyncMock(return_value=result_mock)

        event = _make_bus_event(source="github")
        fired = await engine.evaluate(event)
        assert len(fired) == 1

    async def test_importance_threshold_below(self, engine, mock_db):
        trigger = _make_trigger(conditions={"importance_threshold": 0.9})
        result_mock = MagicMock()
        result_mock.scalars.return_value.all.return_value = [trigger]
        mock_db.execute = AsyncMock(return_value=result_mock)

        event = _make_bus_event(importance_score=0.5)
        fired = await engine.evaluate(event)
        assert len(fired) == 0

    async def test_importance_threshold_above(self, engine, mock_db):
        trigger = _make_trigger(conditions={"importance_threshold": 0.9})
        result_mock = MagicMock()
        result_mock.scalars.return_value.all.return_value = [trigger]
        mock_db.execute = AsyncMock(return_value=result_mock)

        event = _make_bus_event(importance_score=0.95)
        fired = await engine.evaluate(event)
        assert len(fired) == 1

    async def test_skips_disabled(self, engine, mock_db):
        """Disabled triggers are excluded by the DB query (enabled=True)."""
        result_mock = MagicMock()
        result_mock.scalars.return_value.all.return_value = []  # DB filters out disabled
        mock_db.execute = AsyncMock(return_value=result_mock)

        event = _make_bus_event()
        fired = await engine.evaluate(event)
        assert len(fired) == 0

    async def test_multiple_conditions_all_must_match(self, engine, mock_db):
        trigger = _make_trigger(
            conditions={
                "event_type": "email_received",
                "source": "gmail",
                "importance_threshold": 0.7,
            }
        )
        result_mock = MagicMock()
        result_mock.scalars.return_value.all.return_value = [trigger]
        mock_db.execute = AsyncMock(return_value=result_mock)

        event = _make_bus_event(event_type="email_received", source="gmail", importance_score=0.8)
        fired = await engine.evaluate(event)
        assert len(fired) == 1

    async def test_multiple_conditions_partial_fail(self, engine, mock_db):
        trigger = _make_trigger(
            conditions={
                "event_type": "email_received",
                "source": "slack",  # Won't match
            }
        )
        result_mock = MagicMock()
        result_mock.scalars.return_value.all.return_value = [trigger]
        mock_db.execute = AsyncMock(return_value=result_mock)

        event = _make_bus_event(event_type="email_received", source="gmail")
        fired = await engine.evaluate(event)
        assert len(fired) == 0
