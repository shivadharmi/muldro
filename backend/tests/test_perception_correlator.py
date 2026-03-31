"""Tests for EventCorrelator integration into perception cycle."""

from unittest.mock import AsyncMock, MagicMock

import pytest

from src.services.event_correlator import EventCorrelator


@pytest.mark.asyncio
async def test_correlator_detects_thread():
    """EventCorrelator.detect_thread returns thread info for multi-event entity."""
    mock_db = MagicMock()

    mock_events = []
    for i in range(3):
        evt = MagicMock()
        evt.event_id = f"evt_{i}"
        evt.source = "gmail"
        evt.event_type = "email_received"
        evt.entity_id = "thr_shared"
        evt.title = f"Message {i}"
        evt.occurred_at = MagicMock()
        evt.occurred_at.isoformat.return_value = f"2026-03-31T{10 + i}:00:00+00:00"
        evt.actor_entities = None
        mock_events.append(evt)

    result_mock = MagicMock()
    result_mock.scalars.return_value.all.return_value = mock_events
    mock_db.execute = AsyncMock(return_value=result_mock)

    correlator = EventCorrelator(mock_db)
    thread = await correlator.detect_thread("usr_test", "thr_shared", workspace_id="ws_test")

    assert thread is not None
    assert thread["event_count"] == 3
    assert thread["entity_id"] == "thr_shared"


@pytest.mark.asyncio
async def test_correlator_no_thread_for_single_event():
    """Single event should not be detected as a thread."""
    mock_db = MagicMock()

    evt = MagicMock()
    evt.event_id = "evt_0"
    mock_events = [evt]

    result_mock = MagicMock()
    result_mock.scalars.return_value.all.return_value = mock_events
    mock_db.execute = AsyncMock(return_value=result_mock)

    correlator = EventCorrelator(mock_db)
    thread = await correlator.detect_thread("usr_test", "thr_single", workspace_id="ws_test")

    assert thread is None
