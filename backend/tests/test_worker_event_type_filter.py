"""Regression tests for StreamConsumerManager event-type filtering.

The main events stream (``muldro:events:{user_id}``) carries several event
types whose payloads include ``event_id`` — ``event_processed``,
``trigger.fired``, ``initiative.high_priority``. Before the filter was
added, the entity and memory extraction handlers fired on all of them,
which caused:
  - Redundant Claude extraction calls (3x per event under heavy load)
  - "Event not found for extraction" warnings when a stale message
    arrived after retention eviction removed the event row

These tests confirm that both handlers short-circuit on any event type
other than ``event_processed``.
"""

from __future__ import annotations

from unittest.mock import MagicMock

import pytest

from tests.conftest import TEST_USER_ID, make_mock_settings


def _make_bus_event(event_type: str, payload: dict) -> MagicMock:
    ev = MagicMock()
    ev.user_id = TEST_USER_ID
    ev.event_type = event_type
    ev.payload = payload
    ev.message_id = "1234567890-0"
    return ev


def _make_manager():
    from src.services.worker import StreamConsumerManager

    return StreamConsumerManager(make_mock_settings())


class TestEntityExtractionEventTypeFilter:
    @pytest.mark.asyncio
    @pytest.mark.parametrize(
        "wrong_type",
        ["trigger.fired", "initiative.high_priority", "memory.stored", ""],
    )
    async def test_skips_non_event_processed(self, wrong_type):
        """Non-event_processed messages must be ignored — no DB session opened."""
        import src.services.worker as worker_mod

        mgr = _make_manager()
        event = _make_bus_event(wrong_type, {"event_id": "evt_001"})

        # If we open a DB session, the test should fail — the filter must
        # short-circuit *before* get_session_factory().
        original_factory = worker_mod.get_session_factory
        try:
            worker_mod.get_session_factory = MagicMock(
                side_effect=AssertionError("DB session opened for non-event_processed event"),
            )
            await mgr._handle_entity_extraction(event)
        finally:
            worker_mod.get_session_factory = original_factory

    @pytest.mark.asyncio
    async def test_processes_event_processed(self):
        """event_processed messages proceed past the filter."""
        import src.services.worker as worker_mod

        mgr = _make_manager()
        event = _make_bus_event("event_processed", {"event_id": "evt_002"})

        called = {"get_session_factory": False}

        def fake_factory():
            called["get_session_factory"] = True
            # Raise afterwards so we don't have to mock the full DB chain —
            # we only care the filter let us through.
            raise RuntimeError("stop-after-filter")

        original_factory = worker_mod.get_session_factory
        try:
            worker_mod.get_session_factory = fake_factory
            with pytest.raises(RuntimeError, match="stop-after-filter"):
                await mgr._handle_entity_extraction(event)
        finally:
            worker_mod.get_session_factory = original_factory

        assert called["get_session_factory"] is True


class TestMemoryExtractionEventTypeFilter:
    @pytest.mark.asyncio
    @pytest.mark.parametrize(
        "wrong_type",
        ["trigger.fired", "initiative.high_priority", "entity.created"],
    )
    async def test_skips_non_event_processed(self, wrong_type):
        import src.services.worker as worker_mod

        mgr = _make_manager()
        event = _make_bus_event(wrong_type, {"event_id": "evt_003"})

        original_factory = worker_mod.get_session_factory
        try:
            worker_mod.get_session_factory = MagicMock(
                side_effect=AssertionError("DB session opened for non-event_processed event"),
            )
            await mgr._handle_memory_extraction(event)
        finally:
            worker_mod.get_session_factory = original_factory

    @pytest.mark.asyncio
    async def test_missing_event_id_still_short_circuits(self):
        """Even for event_processed, missing event_id returns early."""
        import src.services.worker as worker_mod

        mgr = _make_manager()
        event = _make_bus_event("event_processed", {})

        original_factory = worker_mod.get_session_factory
        try:
            worker_mod.get_session_factory = MagicMock(
                side_effect=AssertionError("DB session opened despite missing event_id"),
            )
            await mgr._handle_entity_extraction(event)
            await mgr._handle_memory_extraction(event)
        finally:
            worker_mod.get_session_factory = original_factory
