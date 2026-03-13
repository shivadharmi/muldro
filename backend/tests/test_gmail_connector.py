"""Tests for Gmail connector — message normalization and push handling."""

from datetime import datetime, timezone
from unittest.mock import AsyncMock, MagicMock, patch

import pytest

from src.connectors.gmail import GmailConnector, GmailMessagePayload
from tests.conftest import make_mock_settings


@pytest.fixture
def settings():
    return make_mock_settings()


@pytest.fixture
def mock_db():
    db = MagicMock()
    db.add = MagicMock()
    db.commit = AsyncMock()
    result_mock = MagicMock()
    result_mock.scalar_one_or_none.return_value = None
    db.execute = AsyncMock(return_value=result_mock)
    return db


def _make_test_message(**overrides) -> dict:
    defaults = dict(
        message_id="msg_001",
        thread_id="thr_001",
        from_email="alice@company.com",
        from_name="Alice Smith",
        to=["me@mycompany.com"],
        subject="Q1 Planning Follow-up",
        snippet="Let's finalize the roadmap before Friday",
        date=datetime(2026, 3, 13, 9, 0, tzinfo=timezone.utc).isoformat(),
    )
    defaults.update(overrides)
    return defaults


def test_message_to_raw_event(settings, mock_db):
    """GmailConnector should convert GmailMessagePayload to RawEvent correctly."""
    mock_processor = MagicMock()
    connector = GmailConnector(settings=settings, db=mock_db, event_processor=mock_processor)

    msg = GmailMessagePayload.model_validate(_make_test_message())
    raw = connector._message_to_raw_event(msg, "gmail_primary")

    assert raw.source == "gmail"
    assert raw.event_type == "email_received"
    assert raw.entity_type == "email_thread"
    assert raw.entity_id == "thr_001"
    assert raw.title == "Q1 Planning Follow-up"
    assert raw.actor["email"] == "alice@company.com"
    assert raw.actor["name"] == "Alice Smith"


@patch("src.services.event_processor.anthropic.AsyncAnthropic")
@pytest.mark.asyncio
async def test_handle_push_notification(mock_anthropic_cls, settings, mock_db):
    """Push notification with messages should create events."""
    import json

    # Mock Claude scoring response
    scores = {
        "importance_score": 0.6,
        "urgency_score": 0.4,
        "confidence_score": 0.7,
        "importance_signals": {
            "from_priority_person": False,
            "contains_deadline": True,
            "contains_question": False,
            "related_to_active_project": False,
        },
        "summary": "Q1 roadmap follow-up",
    }
    mock_client = MagicMock()
    response = MagicMock()
    response.content = [MagicMock(text=json.dumps(scores))]
    mock_client.messages.create = AsyncMock(return_value=response)
    mock_anthropic_cls.return_value = mock_client

    from src.services.event_processor import EventProcessor

    processor = EventProcessor(settings=settings, db=mock_db)
    connector = GmailConnector(settings=settings, db=mock_db, event_processor=processor)

    payload = {"messages": [_make_test_message()]}
    event_ids = await connector.handle_push_notification(payload, "usr_default")

    assert len(event_ids) == 1
    assert event_ids[0].startswith("evt_")


@patch("src.services.event_processor.anthropic.AsyncAnthropic")
@pytest.mark.asyncio
async def test_empty_push_returns_no_events(mock_anthropic_cls, settings, mock_db):
    """Push notification with no messages should return empty list."""
    mock_anthropic_cls.return_value = MagicMock()

    from src.services.event_processor import EventProcessor

    processor = EventProcessor(settings=settings, db=mock_db)
    connector = GmailConnector(settings=settings, db=mock_db, event_processor=processor)

    event_ids = await connector.handle_push_notification({}, "usr_default")
    assert event_ids == []
