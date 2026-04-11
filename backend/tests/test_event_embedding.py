"""Tests for event embedding into Qdrant on ingest (Spec 5A)."""

import json
from unittest.mock import AsyncMock, MagicMock, patch

import pytest

from src.services.event_processor import EventProcessor
from tests.conftest import TEST_USER_ID, make_mock_settings, make_raw_event


@pytest.fixture
def mock_db():
    db = MagicMock()
    db.add = MagicMock()
    db.commit = AsyncMock()
    db.flush = AsyncMock()
    result_mock = MagicMock()
    result_mock.scalar_one_or_none.return_value = None
    db.execute = AsyncMock(return_value=result_mock)
    return db


@pytest.fixture
def settings():
    return make_mock_settings()


def _make_claude_response(importance: float = 0.85) -> MagicMock:
    scores = {
        "importance_score": importance,
        "urgency_score": 0.5,
        "confidence_score": 0.8,
        "importance_signals": {
            "from_priority_person": False,
            "contains_deadline": False,
            "contains_question": False,
            "related_to_active_project": False,
        },
        "summary": "Test summary",
    }
    response = MagicMock()
    response.content = [MagicMock(text=json.dumps(scores))]
    return response


@patch("src.services.event_processor.get_anthropic_client")
@pytest.mark.asyncio
async def test_event_embedding_above_threshold(mock_get_client, settings, mock_db):
    """Events with importance >= 0.3 should be embedded into Qdrant."""
    mock_client = MagicMock()
    mock_client.messages.create = AsyncMock(return_value=_make_claude_response(importance=0.85))
    mock_get_client.return_value = mock_client

    embedding_service = MagicMock()
    embedding_service.embed_text = AsyncMock(return_value=[0.1] * 1024)

    vector_store = MagicMock()
    vector_store.upsert = AsyncMock()

    processor = EventProcessor(
        settings=settings,
        db=mock_db,
        embedding_service=embedding_service,
        vector_store=vector_store,
    )
    raw = make_raw_event(title="Investor update", summary="Key metrics for Q1")
    event_id = await processor.process(raw, TEST_USER_ID)

    assert event_id is not None
    vector_store.upsert.assert_called_once()
    call_kwargs = vector_store.upsert.call_args
    assert call_kwargs.kwargs["collection"] == "events"
    payload = call_kwargs.kwargs["payload"]
    assert "event_type" in payload
    assert "source" in payload
    assert "importance_score" in payload
    assert "occurred_at" in payload


@patch("src.services.event_processor.get_anthropic_client")
@pytest.mark.asyncio
async def test_event_embedding_below_threshold_skipped(mock_get_client, settings, mock_db):
    """Events with importance < 0.3 should NOT be embedded into Qdrant."""
    mock_client = MagicMock()
    mock_client.messages.create = AsyncMock(return_value=_make_claude_response(importance=0.2))
    mock_get_client.return_value = mock_client

    embedding_service = MagicMock()
    embedding_service.embed_text = AsyncMock(return_value=[0.1] * 1024)

    vector_store = MagicMock()
    vector_store.upsert = AsyncMock()

    processor = EventProcessor(
        settings=settings,
        db=mock_db,
        embedding_service=embedding_service,
        vector_store=vector_store,
    )
    raw = make_raw_event()
    event_id = await processor.process(raw, TEST_USER_ID)

    assert event_id is not None
    vector_store.upsert.assert_not_called()


@patch("src.services.event_processor.get_anthropic_client")
@pytest.mark.asyncio
async def test_event_embedding_graceful_on_embed_failure(mock_get_client, settings, mock_db):
    """If embedding returns None, upsert is NOT called and processing succeeds."""
    mock_client = MagicMock()
    mock_client.messages.create = AsyncMock(return_value=_make_claude_response(importance=0.85))
    mock_get_client.return_value = mock_client

    embedding_service = MagicMock()
    embedding_service.embed_text = AsyncMock(return_value=None)

    vector_store = MagicMock()
    vector_store.upsert = AsyncMock()

    processor = EventProcessor(
        settings=settings,
        db=mock_db,
        embedding_service=embedding_service,
        vector_store=vector_store,
    )
    raw = make_raw_event()
    event_id = await processor.process(raw, TEST_USER_ID)

    assert event_id is not None
    vector_store.upsert.assert_not_called()
