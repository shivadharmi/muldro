"""Tests for EventProcessor — scoring, dedup, normalization."""

import json
from unittest.mock import AsyncMock, MagicMock, patch

import pytest

from src.services.event_processor import DEFAULT_SCORES, EventProcessor, make_idempotency_key
from tests.conftest import TEST_USER_ID, make_mock_settings, make_raw_event


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


@patch("src.services.event_processor.get_anthropic_client")
@pytest.mark.asyncio
async def test_process_stores_event(mock_get_client, settings, mock_db):
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
    mock_get_client.return_value = mock_client

    processor = EventProcessor(settings=settings, db=mock_db)
    raw = make_raw_event()
    event_id = await processor.process(raw, TEST_USER_ID)

    assert event_id is not None
    assert event_id.startswith("evt_")
    mock_db.add.assert_called_once()
    mock_db.commit.assert_called_once()

    stored_event = mock_db.add.call_args[0][0]
    assert stored_event.importance_score == 0.85
    assert stored_event.urgency_score == 0.7
    assert stored_event.status == "processed"


@patch("src.services.event_processor.get_anthropic_client")
@pytest.mark.asyncio
async def test_process_deduplicates(mock_get_client, settings, mock_db):
    """Duplicate events (same idempotency key) should return None."""
    # Simulate existing event found
    result_mock = MagicMock()
    result_mock.scalar_one_or_none.return_value = "evt_existing"
    mock_db.execute = AsyncMock(return_value=result_mock)

    mock_get_client.return_value = MagicMock()

    processor = EventProcessor(settings=settings, db=mock_db)
    raw = make_raw_event()
    event_id = await processor.process(raw, TEST_USER_ID)

    assert event_id is None
    mock_db.add.assert_not_called()


@patch("src.services.event_processor.get_anthropic_client")
@pytest.mark.asyncio
async def test_process_handles_concurrent_unique_violation(mock_get_client, settings, mock_db):
    """Concurrent ingestion that loses the race on idempotency_key must be
    treated as a duplicate, not raised to the caller.

    Regression test for the ``normalized_events.idempotency_key`` unique
    violation that surfaced as ``event_ingest_failed`` / DLQ entries when
    two perception cycles raced for the same source. The pre-check
    ``SELECT`` is non-atomic; the INSERT must catch ``IntegrityError``,
    roll back the session, and return ``None`` so callers (and downstream
    triggers/embedding/event bus publish) treat it as a no-op.
    """
    from sqlalchemy.exc import IntegrityError

    scores = {
        "importance_score": 0.5,
        "urgency_score": 0.5,
        "confidence_score": 0.5,
        "importance_signals": {},
        "summary": "duplicate from race",
    }
    mock_client = MagicMock()
    mock_client.messages.create = AsyncMock(return_value=_make_claude_response(scores))
    mock_get_client.return_value = mock_client

    # Pre-check SELECT returns no existing row (race: both cycles miss).
    # commit() then raises IntegrityError — the other cycle won the race.
    mock_db.commit = AsyncMock(
        side_effect=IntegrityError("INSERT", {}, Exception("uq idempotency_key"))
    )
    mock_db.rollback = AsyncMock()

    processor = EventProcessor(settings=settings, db=mock_db)
    raw = make_raw_event()
    event_id = await processor.process(raw, TEST_USER_ID)

    assert event_id is None, "race-loser must be treated as duplicate"
    mock_db.rollback.assert_awaited_once()
    mock_db.add.assert_called_once()  # attempted, not silently skipped


@patch("src.services.event_processor.get_anthropic_client")
@pytest.mark.asyncio
async def test_score_fallback_on_error(mock_get_client, settings, mock_db):
    """If Claude scoring fails, default scores should be used."""
    mock_client = MagicMock()
    mock_client.messages.create = AsyncMock(side_effect=RuntimeError("API down"))
    mock_get_client.return_value = mock_client

    processor = EventProcessor(settings=settings, db=mock_db)
    raw = make_raw_event()
    event_id = await processor.process(raw, TEST_USER_ID)

    assert event_id is not None
    stored_event = mock_db.add.call_args[0][0]
    assert stored_event.importance_score == DEFAULT_SCORES["importance_score"]
    assert stored_event.urgency_score == DEFAULT_SCORES["urgency_score"]


@patch("src.services.event_processor.get_anthropic_client")
@pytest.mark.asyncio
async def test_thread_reply_not_deduplicated(mock_get_client, settings, mock_db):
    """Two messages in same thread (same entity_id) but different message_id both store."""
    scores = {
        "importance_score": 0.8,
        "urgency_score": 0.6,
        "confidence_score": 0.9,
        "importance_signals": {
            "from_priority_person": False,
            "contains_deadline": False,
            "contains_question": False,
            "related_to_active_project": False,
        },
        "summary": "thread message",
    }
    mock_client = MagicMock()
    mock_client.messages.create = AsyncMock(return_value=_make_claude_response(scores))
    mock_get_client.return_value = mock_client

    processor = EventProcessor(settings=settings, db=mock_db)

    msg1 = make_raw_event(entity_id="thr_100", raw_payload={"message_id": "msg_aaa"})
    msg2 = make_raw_event(entity_id="thr_100", raw_payload={"message_id": "msg_bbb"})

    eid1 = await processor.process(msg1, TEST_USER_ID)
    eid2 = await processor.process(msg2, TEST_USER_ID)

    assert eid1 is not None
    assert eid2 is not None
    assert mock_db.add.call_count == 2

    stored1 = mock_db.add.call_args_list[0][0][0]
    stored2 = mock_db.add.call_args_list[1][0][0]
    assert stored1.idempotency_key != stored2.idempotency_key
    assert stored1.entity_id == stored2.entity_id == "thr_100"


@pytest.mark.asyncio
async def test_idempotency_key_includes_message_id(settings, mock_db):
    """When raw_payload contains message_id, the key must include it as 4-part format."""
    processor = EventProcessor(settings=settings, db=mock_db)
    raw = make_raw_event(
        source="gmail",
        entity_id="thr_001",
        event_type="email_received",
        raw_payload={"message_id": "msg_xyz"},
    )

    # Call _process_inner directly to inspect the key (bypass semaphore)
    with patch.object(processor, "_score_event", new_callable=AsyncMock) as mock_score:
        mock_score.return_value = {
            "importance_score": 0.5,
            "urgency_score": 0.5,
            "confidence_score": 0.5,
            "importance_signals": {},
            "summary": "test",
        }
        await processor._process_inner(raw, TEST_USER_ID)

    stored = mock_db.add.call_args[0][0]
    assert stored.idempotency_key == "gmail:thr_001:msg_xyz:email_received"


@pytest.mark.asyncio
async def test_idempotency_key_fallback_no_message_id(settings, mock_db):
    """When raw_payload has no message_id, key falls back to 3-part format."""
    processor = EventProcessor(settings=settings, db=mock_db)

    # Test with raw_payload=None
    raw_none = make_raw_event(
        source="slack",
        entity_id="ch_001",
        event_type="message",
        raw_payload=None,
    )
    with patch.object(processor, "_score_event", new_callable=AsyncMock) as mock_score:
        mock_score.return_value = {
            "importance_score": 0.5,
            "urgency_score": 0.5,
            "confidence_score": 0.5,
            "importance_signals": {},
            "summary": "test",
        }
        await processor._process_inner(raw_none, TEST_USER_ID)

    stored = mock_db.add.call_args[0][0]
    assert stored.idempotency_key == "slack:ch_001:message"

    # Reset mock and test with raw_payload that has no message_id key
    mock_db.add.reset_mock()
    raw_no_mid = make_raw_event(
        source="github",
        entity_id="pr_001",
        event_type="pr_opened",
        raw_payload={"some_other_field": "value"},
    )
    with patch.object(processor, "_score_event", new_callable=AsyncMock) as mock_score:
        mock_score.return_value = {
            "importance_score": 0.5,
            "urgency_score": 0.5,
            "confidence_score": 0.5,
            "importance_signals": {},
            "summary": "test",
        }
        await processor._process_inner(raw_no_mid, TEST_USER_ID)

    stored = mock_db.add.call_args[0][0]
    assert stored.idempotency_key == "github:pr_001:pr_opened"


def test_make_idempotency_key_with_message_id():
    """Key includes message_id when present in raw_payload."""
    raw = make_raw_event(
        source="gmail",
        entity_id="thr_abc",
        event_type="email_received",
        raw_payload={"message_id": "msg_xyz"},
    )
    assert make_idempotency_key(raw) == "gmail:thr_abc:msg_xyz:email_received"


def test_make_idempotency_key_without_message_id():
    """Key falls back to 3-part format when no message_id."""
    raw = make_raw_event(
        source="calendar",
        entity_id="cal_evt_123",
        event_type="meeting_scheduled",
        raw_payload=None,
    )
    assert make_idempotency_key(raw) == "calendar:cal_evt_123:meeting_scheduled"


def test_make_idempotency_key_empty_message_id():
    """Empty string message_id should use fallback format."""
    raw = make_raw_event(
        source="slack",
        entity_id="ch_001",
        event_type="message_posted",
        raw_payload={"message_id": ""},
    )
    assert make_idempotency_key(raw) == "slack:ch_001:message_posted"
