"""Tests for EventBus — Redis Streams-based event bus."""

import json
from datetime import datetime, timezone
from unittest.mock import AsyncMock

import pytest

from src.services.event_bus import BusEvent, EventBus
from tests.conftest import TEST_USER_ID


@pytest.fixture
def mock_redis():
    r = AsyncMock()
    r.xadd = AsyncMock(return_value="1234567890-0")
    r.xreadgroup = AsyncMock(return_value=[])
    r.xack = AsyncMock()
    r.xpending_range = AsyncMock(return_value=[])
    r.xrange = AsyncMock(return_value=[])
    r.xgroup_create = AsyncMock()
    return r


@pytest.fixture
def bus(mock_redis):
    return EventBus(mock_redis)


class TestPublish:
    async def test_publishes_to_stream(self, bus, mock_redis):
        event_id = await bus.publish(
            f"jarvis:events:{TEST_USER_ID}",
            "email_received",
            {"subject": "Test"},
            user_id=TEST_USER_ID,
        )
        assert event_id.startswith("be_")
        mock_redis.xadd.assert_called_once()
        call_args = mock_redis.xadd.call_args
        assert call_args[0][0] == f"jarvis:events:{TEST_USER_ID}"
        data = call_args[0][1]
        assert data["event_type"] == "email_received"
        assert json.loads(data["payload"]) == {"subject": "Test"}

    async def test_includes_metadata(self, bus, mock_redis):
        await bus.publish(
            f"jarvis:events:{TEST_USER_ID}",
            "test",
            {},
            metadata={"trace_id": "tr_123"},
        )
        data = mock_redis.xadd.call_args[0][1]
        assert json.loads(data["metadata"]) == {"trace_id": "tr_123"}


class TestSubscribe:
    async def test_processes_messages(self, bus, mock_redis):
        mock_redis.xreadgroup = AsyncMock(
            return_value=[
                (
                    f"jarvis:events:{TEST_USER_ID}",
                    [
                        (
                            "1-0",
                            {
                                "event_id": "be_test",
                                "event_type": "email_received",
                                "user_id": TEST_USER_ID,
                                "payload": json.dumps({"key": "val"}),
                                "metadata": "{}",
                                "created_at": datetime.now(timezone.utc).isoformat(),
                            },
                        )
                    ],
                )
            ]
        )
        handler = AsyncMock()

        count = await bus.subscribe(
            f"jarvis:events:{TEST_USER_ID}",
            "test_group",
            "consumer-1",
            handler,
        )

        assert count == 1
        handler.assert_called_once()
        event = handler.call_args[0][0]
        assert isinstance(event, BusEvent)
        assert event.event_type == "email_received"
        assert event.payload == {"key": "val"}

    async def test_acks_after_processing(self, bus, mock_redis):
        mock_redis.xreadgroup = AsyncMock(
            return_value=[
                (
                    "stream",
                    [
                        (
                            "1-0",
                            {
                                "event_id": "be_1",
                                "event_type": "test",
                                "user_id": "u",
                                "payload": "{}",
                                "metadata": "{}",
                                "created_at": datetime.now(timezone.utc).isoformat(),
                            },
                        )
                    ],
                )
            ]
        )
        handler = AsyncMock()
        await bus.subscribe("stream", "grp", "c1", handler)
        mock_redis.xack.assert_called_once_with("stream", "grp", "1-0")

    async def test_no_messages(self, bus, mock_redis):
        mock_redis.xreadgroup = AsyncMock(return_value=[])
        handler = AsyncMock()
        count = await bus.subscribe("stream", "grp", "c1", handler)
        assert count == 0
        handler.assert_not_called()


class TestConsumerGroup:
    async def test_creates_group(self, bus, mock_redis):
        await bus.create_consumer_group("stream", "grp")
        mock_redis.xgroup_create.assert_called_once_with("stream", "grp", id="0", mkstream=True)

    async def test_ignores_busygroup(self, bus, mock_redis):
        mock_redis.xgroup_create = AsyncMock(
            side_effect=Exception("BUSYGROUP Consumer Group name already exists")
        )
        await bus.create_consumer_group("stream", "grp")  # Should not raise


class TestReplay:
    async def test_replays_range(self, bus, mock_redis):
        mock_redis.xrange = AsyncMock(
            return_value=[
                (
                    "1-0",
                    {
                        "event_id": "be_1",
                        "event_type": "test",
                        "user_id": "u",
                        "payload": "{}",
                        "metadata": "{}",
                        "created_at": datetime.now(timezone.utc).isoformat(),
                    },
                ),
                (
                    "2-0",
                    {
                        "event_id": "be_2",
                        "event_type": "test2",
                        "user_id": "u",
                        "payload": "{}",
                        "metadata": "{}",
                        "created_at": datetime.now(timezone.utc).isoformat(),
                    },
                ),
            ]
        )
        handler = AsyncMock()
        count = await bus.replay("stream", handler)
        assert count == 2
        assert handler.call_count == 2


class TestStreamNames:
    def test_event_stream(self, bus):
        assert bus.event_stream("usr_123") == "jarvis:events:usr_123"

    def test_agent_stream(self, bus):
        assert bus.agent_stream("usr_123") == "jarvis:agent_events:usr_123"
