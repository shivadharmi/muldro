"""Tests for WhatsApp connector — event ingestion and normalization."""

from unittest.mock import AsyncMock, MagicMock

import pytest

from src.connectors.whatsapp import WhatsAppConnector, WhatsAppMessagePayload
from tests.conftest import make_mock_settings


@pytest.fixture
def settings():
    return make_mock_settings()


@pytest.fixture
def mock_db():
    return MagicMock()


@pytest.fixture
def mock_event_processor():
    ep = MagicMock()
    ep.process = AsyncMock(return_value="evt_wa_001")
    return ep


def test_message_to_raw_event(settings, mock_db, mock_event_processor):
    """Should normalize a WhatsApp message into a RawEvent."""
    connector = WhatsAppConnector(
        settings=settings, db=mock_db, event_processor=mock_event_processor
    )
    msg = WhatsAppMessagePayload(
        message_id="wamid.12345",
        from_number="+1234567890",
        from_name="Alice",
        text="Can we reschedule the meeting?",
    )

    raw = connector._message_to_raw_event(msg, "whatsapp_test")

    assert raw.source == "whatsapp"
    assert raw.event_type == "whatsapp_message"
    assert raw.entity_type == "whatsapp_chat"
    assert raw.entity_id == "wa:+1234567890"
    assert "Alice" in raw.title
    assert raw.actor["phone"] == "+1234567890"
    assert raw.summary == "Can we reschedule the meeting?"


def test_reply_message(settings, mock_db, mock_event_processor):
    """Reply messages should be indicated in the title."""
    connector = WhatsAppConnector(
        settings=settings, db=mock_db, event_processor=mock_event_processor
    )
    msg = WhatsAppMessagePayload(
        message_id="wamid.67890",
        from_number="+1234567890",
        from_name="Bob",
        text="Yes, let's do Thursday",
        context_message_id="wamid.12345",
    )

    raw = connector._message_to_raw_event(msg, "whatsapp_test")

    assert "reply" in raw.title.lower()


@pytest.mark.asyncio
async def test_handle_webhook(settings, mock_db, mock_event_processor):
    """Should process a WhatsApp Business API webhook payload."""
    connector = WhatsAppConnector(
        settings=settings, db=mock_db, event_processor=mock_event_processor
    )

    payload = {
        "entry": [
            {
                "changes": [
                    {
                        "value": {
                            "contacts": [{"wa_id": "+1234567890", "profile": {"name": "Alice"}}],
                            "messages": [
                                {
                                    "id": "wamid.12345",
                                    "from": "+1234567890",
                                    "type": "text",
                                    "text": {"body": "Hello from WhatsApp"},
                                }
                            ],
                        }
                    }
                ]
            }
        ]
    }

    event_ids = await connector.handle_webhook(payload, "usr_default")

    assert len(event_ids) == 1
    assert event_ids[0] == "evt_wa_001"
    mock_event_processor.process.assert_called_once()


@pytest.mark.asyncio
async def test_handle_empty_webhook(settings, mock_db, mock_event_processor):
    """Should handle empty webhook payload gracefully."""
    connector = WhatsAppConnector(
        settings=settings, db=mock_db, event_processor=mock_event_processor
    )

    event_ids = await connector.handle_webhook({"entry": []}, "usr_default")

    assert event_ids == []
    mock_event_processor.process.assert_not_called()


@pytest.mark.asyncio
async def test_process_test_message(settings, mock_db, mock_event_processor):
    """Should process a test message directly."""
    connector = WhatsAppConnector(
        settings=settings, db=mock_db, event_processor=mock_event_processor
    )

    msg = WhatsAppMessagePayload(
        message_id="wamid.test001",
        from_number="+1234567890",
        from_name="Test User",
        text="Test message",
    )

    event_id = await connector.process_test_message(msg, "usr_default")

    assert event_id == "evt_wa_001"
