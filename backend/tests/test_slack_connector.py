"""Tests for Slack connector — event ingestion and normalization."""

from unittest.mock import AsyncMock, MagicMock

import pytest

from src.connectors.slack import SlackConnector, SlackMessagePayload
from tests.conftest import make_mock_settings


@pytest.fixture
def settings():
    return make_mock_settings()


@pytest.fixture
def mock_db():
    db = MagicMock()
    db.add = MagicMock()
    db.commit = AsyncMock()
    db.execute = AsyncMock()
    return db


@pytest.fixture
def mock_event_processor():
    ep = MagicMock()
    ep.process = AsyncMock(return_value="evt_slack_001")
    return ep


def test_message_to_raw_event(settings, mock_db, mock_event_processor):
    """Should normalize a Slack message into a RawEvent."""
    connector = SlackConnector(settings=settings, db=mock_db, event_processor=mock_event_processor)
    msg = SlackMessagePayload(
        channel_id="C12345",
        channel_name="general",
        message_ts="1678901234.567890",
        user_id="U12345",
        user_name="alice",
        user_email="alice@company.com",
        text="Hey team, here's the update on the launch",
    )

    raw = connector._message_to_raw_event(msg, "workspace_001")

    assert raw.source == "slack"
    assert raw.event_type == "slack_message"
    assert raw.entity_type == "slack_thread"
    assert raw.entity_id == "C12345:1678901234.567890"
    assert "general" in raw.title
    assert raw.actor["name"] == "alice"
    assert raw.actor["email"] == "alice@company.com"


def test_thread_reply_uses_thread_ts(settings, mock_db, mock_event_processor):
    """Thread replies should use thread_ts as the entity_id component."""
    connector = SlackConnector(settings=settings, db=mock_db, event_processor=mock_event_processor)
    msg = SlackMessagePayload(
        channel_id="C12345",
        channel_name="general",
        message_ts="1678901299.000000",
        thread_ts="1678901234.567890",
        user_id="U12345",
        text="Replying in thread",
    )

    raw = connector._message_to_raw_event(msg, "workspace_001")

    assert raw.entity_id == "C12345:1678901234.567890"
    assert "thread reply" in raw.title.lower()


@pytest.mark.asyncio
async def test_handle_event_callback(settings, mock_db, mock_event_processor):
    """Should process a Slack Events API callback."""
    connector = SlackConnector(settings=settings, db=mock_db, event_processor=mock_event_processor)

    payload = {
        "type": "event_callback",
        "team_id": "T12345",
        "event": {
            "type": "message",
            "channel": "C12345",
            "user": "U12345",
            "text": "Important update about the project",
            "ts": "1678901234.567890",
        },
    }

    event_ids = await connector.handle_event_callback(payload, "usr_default")

    assert len(event_ids) == 1
    assert event_ids[0] == "evt_slack_001"
    mock_event_processor.process.assert_called_once()


@pytest.mark.asyncio
async def test_skips_bot_messages(settings, mock_db, mock_event_processor):
    """Should skip bot messages."""
    connector = SlackConnector(settings=settings, db=mock_db, event_processor=mock_event_processor)

    payload = {
        "type": "event_callback",
        "team_id": "T12345",
        "event": {
            "type": "message",
            "subtype": "bot_message",
            "channel": "C12345",
            "text": "Bot notification",
            "ts": "1678901234.567890",
        },
    }

    event_ids = await connector.handle_event_callback(payload, "usr_default")

    assert event_ids == []
    mock_event_processor.process.assert_not_called()


@pytest.mark.asyncio
async def test_process_test_message(settings, mock_db, mock_event_processor):
    """Should process a test message directly."""
    connector = SlackConnector(settings=settings, db=mock_db, event_processor=mock_event_processor)

    msg = SlackMessagePayload(
        channel_id="C12345",
        channel_name="general",
        message_ts="1678901234.567890",
        user_id="U12345",
        user_name="alice",
        text="Test message for dev",
    )

    event_id = await connector.process_test_message(msg, "usr_default")

    assert event_id == "evt_slack_001"
    mock_event_processor.process.assert_called_once()
