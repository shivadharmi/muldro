"""Tests for EventCorrelator service."""

from datetime import datetime, timezone
from unittest.mock import AsyncMock, MagicMock

import pytest

from src.services.event_correlator import EventCorrelator


@pytest.fixture
def mock_db():
    """Mock AsyncSession."""
    return AsyncMock()


@pytest.fixture
def correlator(mock_db):
    return EventCorrelator(db=mock_db)


def make_mock_event(**overrides):
    """Factory for mock NormalizedEvent."""
    event = MagicMock()
    defaults = {
        "event_id": "evt_001",
        "user_id": "user_001",
        "source": "gmail",
        "event_type": "email_received",
        "entity_type": "email_thread",
        "entity_id": "thread_001",
        "occurred_at": datetime(2026, 3, 16, 10, 0, tzinfo=timezone.utc),
        "title": "Test email",
        "actor_entities": [{"email": "test@example.com", "name": "Test User"}],
    }
    for key, value in {**defaults, **overrides}.items():
        setattr(event, key, value)
    return event


@pytest.mark.asyncio
async def test_correlate_no_event(correlator, mock_db):
    """Test correlate returns empty when event not found."""
    result = MagicMock()
    result.scalar_one_or_none.return_value = None
    mock_db.execute = AsyncMock(return_value=result)

    correlations = await correlator.correlate("evt_nonexistent", "user_001")
    assert correlations == []


@pytest.mark.asyncio
async def test_correlate_finds_by_entity(correlator, mock_db):
    """Test correlate finds events by same entity."""
    event = make_mock_event()

    # First query returns the target event
    first_result = MagicMock()
    first_result.scalar_one_or_none.return_value = event

    # Second query returns related events by entity
    entity_result = MagicMock()
    related_event = make_mock_event(event_id="evt_002", title="Related email")
    entity_result.scalars.return_value.all.return_value = [related_event]

    # Third query for actor events
    actor_result = MagicMock()
    actor_result.scalars.return_value.all.return_value = []

    # Fourth query for burst detection
    burst_result = MagicMock()
    burst_result.scalars.return_value.all.return_value = []

    mock_db.execute = AsyncMock(
        side_effect=[first_result, entity_result, actor_result, burst_result]
    )

    correlations = await correlator.correlate("evt_001", "user_001")

    assert len(correlations) >= 1
    entity_correlation = next((c for c in correlations if c["type"] == "same_entity"), None)
    assert entity_correlation is not None
    assert entity_correlation["entity_id"] == "thread_001"
    assert len(entity_correlation["events"]) == 1
    assert entity_correlation["events"][0]["event_id"] == "evt_002"


@pytest.mark.asyncio
async def test_correlate_finds_burst(correlator, mock_db):
    """Test correlate detects burst of same event type."""
    event = make_mock_event()

    # First query returns the target event
    first_result = MagicMock()
    first_result.scalar_one_or_none.return_value = event

    # Second query for entity
    entity_result = MagicMock()
    entity_result.scalars.return_value.all.return_value = []

    # Third query for actor
    actor_result = MagicMock()
    actor_result.scalars.return_value.all.return_value = []

    # Fourth query for burst (>3 events)
    burst_result = MagicMock()
    burst_result.scalars.return_value.all.return_value = [
        "evt_001",
        "evt_002",
        "evt_003",
        "evt_004",
    ]

    mock_db.execute = AsyncMock(
        side_effect=[first_result, entity_result, actor_result, burst_result]
    )

    correlations = await correlator.correlate("evt_001", "user_001")

    burst_correlation = next((c for c in correlations if c["type"] == "burst"), None)
    assert burst_correlation is not None
    assert burst_correlation["source"] == "gmail"
    assert burst_correlation["event_type"] == "email_received"
    assert burst_correlation["count"] == 4


@pytest.mark.asyncio
async def test_detect_thread_no_events(correlator, mock_db):
    """Test detect_thread returns None when fewer than 2 events."""
    result = MagicMock()
    result.scalars.return_value.all.return_value = [make_mock_event()]
    mock_db.execute = AsyncMock(return_value=result)

    thread = await correlator.detect_thread("user_001", "thread_001")
    assert thread is None


@pytest.mark.asyncio
async def test_detect_thread_multiple_events(correlator, mock_db):
    """Test detect_thread returns thread info for multiple events."""
    events = [
        make_mock_event(
            event_id="evt_001",
            occurred_at=datetime(2026, 3, 16, 10, 0, tzinfo=timezone.utc),
            source="gmail",
        ),
        make_mock_event(
            event_id="evt_002",
            occurred_at=datetime(2026, 3, 16, 11, 0, tzinfo=timezone.utc),
            source="slack",
        ),
    ]
    result = MagicMock()
    result.scalars.return_value.all.return_value = events
    mock_db.execute = AsyncMock(return_value=result)

    thread = await correlator.detect_thread("user_001", "thread_001")

    assert thread is not None
    assert thread["entity_id"] == "thread_001"
    assert thread["event_count"] == 2
    assert thread["first_at"] == "2026-03-16T10:00:00+00:00"
    assert thread["last_at"] == "2026-03-16T11:00:00+00:00"
    assert set(thread["sources"]) == {"gmail", "slack"}


@pytest.mark.asyncio
async def test_get_event_context(correlator, mock_db):
    """Test get_event_context returns full context."""
    event = make_mock_event()

    # First call for correlate
    first_result = MagicMock()
    first_result.scalar_one_or_none.return_value = event

    # Second for entity query in correlate
    entity_result = MagicMock()
    entity_result.scalars.return_value.all.return_value = []

    # Third for actor query
    actor_result = MagicMock()
    actor_result.scalars.return_value.all.return_value = []

    # Fourth for burst
    burst_result = MagicMock()
    burst_result.scalars.return_value.all.return_value = []

    # Fifth for get_event_context's own query
    context_result = MagicMock()
    context_result.scalar_one_or_none.return_value = event

    # Sixth for detect_thread
    thread_result = MagicMock()
    thread_result.scalars.return_value.all.return_value = [
        event,
        make_mock_event(event_id="evt_002"),
    ]

    mock_db.execute = AsyncMock(
        side_effect=[
            first_result,
            entity_result,
            actor_result,
            burst_result,
            context_result,
            thread_result,
        ]
    )

    context = await correlator.get_event_context("evt_001", "user_001")

    assert context["event_id"] == "evt_001"
    assert "correlations" in context
    assert "thread" in context
    assert context["thread"]["event_count"] == 2
