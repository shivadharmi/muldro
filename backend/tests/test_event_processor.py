"""Tests for EventProcessor — scoring, dedup, normalization."""

import json
from unittest.mock import AsyncMock, MagicMock, patch

import pytest

from src.services.event_processor import DEFAULT_SCORES, EventProcessor
from tests.conftest import make_mock_settings, make_raw_event


@pytest.fixture
def mock_db():
    db = MagicMock()
    db.add = MagicMock()
    db.commit = AsyncMock()

    # Default: no duplicate found
    result_mock = MagicMock()
    result_mock.scalar_one_or_none.return_value = None
    db.execute = AsyncMock(return_value=result_mock)
    return db


@pytest.fixture
def settings():
    return make_mock_settings()


def _make_claude_response(scores: dict) -> MagicMock:
    """Build a mock Anthropic response with JSON content."""
    response = MagicMock()
    response.content = [MagicMock(text=json.dumps(scores))]
    return response


@patch("src.services.event_processor.anthropic.AsyncAnthropic")
@pytest.mark.asyncio
async def test_process_stores_event(mock_anthropic_cls, settings, mock_db):
    """Processing a new event should score it and store it."""
    scores = {
        "importance_score": 0.85,
        "urgency_score": 0.7,
        "confidence_score": 0.9,
        "importance_signals": {
            "from_priority_person": True,
            "contains_deadline": False,
            "contains_question": True,
            "related_to_active_project": True,
        },
        "summary": "Investor wants to discuss the deck",
    }
    mock_client = MagicMock()
    mock_client.messages.create = AsyncMock(return_value=_make_claude_response(scores))
    mock_anthropic_cls.return_value = mock_client

    processor = EventProcessor(settings=settings, db=mock_db)
    raw = make_raw_event()
    event_id = await processor.process(raw, "usr_default")

    assert event_id is not None
    assert event_id.startswith("evt_")
    mock_db.add.assert_called_once()
    mock_db.commit.assert_called_once()

    stored_event = mock_db.add.call_args[0][0]
    assert stored_event.importance_score == 0.85
    assert stored_event.urgency_score == 0.7
    assert stored_event.status == "processed"


@patch("src.services.event_processor.anthropic.AsyncAnthropic")
@pytest.mark.asyncio
async def test_process_deduplicates(mock_anthropic_cls, settings, mock_db):
    """Duplicate events (same idempotency key) should return None."""
    # Simulate existing event found
    result_mock = MagicMock()
    result_mock.scalar_one_or_none.return_value = "evt_existing"
    mock_db.execute = AsyncMock(return_value=result_mock)

    mock_anthropic_cls.return_value = MagicMock()

    processor = EventProcessor(settings=settings, db=mock_db)
    raw = make_raw_event()
    event_id = await processor.process(raw, "usr_default")

    assert event_id is None
    mock_db.add.assert_not_called()


@patch("src.services.event_processor.anthropic.AsyncAnthropic")
@pytest.mark.asyncio
async def test_score_fallback_on_error(mock_anthropic_cls, settings, mock_db):
    """If Claude scoring fails, default scores should be used."""
    mock_client = MagicMock()
    mock_client.messages.create = AsyncMock(side_effect=RuntimeError("API down"))
    mock_anthropic_cls.return_value = mock_client

    processor = EventProcessor(settings=settings, db=mock_db)
    raw = make_raw_event()
    event_id = await processor.process(raw, "usr_default")

    assert event_id is not None
    stored_event = mock_db.add.call_args[0][0]
    assert stored_event.importance_score == DEFAULT_SCORES["importance_score"]
    assert stored_event.urgency_score == DEFAULT_SCORES["urgency_score"]
