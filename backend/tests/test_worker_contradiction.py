"""Tests for StreamConsumerManager._handle_contradiction_check.

TDD — tests written before implementation.
Covers:
  - handler calls MemoryService.check_contradictions with correct args
  - skips when memory_id missing from payload
  - skips when fact_text missing from payload
"""

from __future__ import annotations

from unittest.mock import AsyncMock, MagicMock, patch

import pytest

from tests.conftest import TEST_USER_ID, TEST_WORKSPACE_ID, make_mock_settings


def _make_bus_event(payload: dict, event_type: str = "memory.stored") -> MagicMock:
    """Build a minimal BusEvent-like mock."""
    ev = MagicMock()
    ev.user_id = TEST_USER_ID
    ev.event_type = event_type
    ev.payload = payload
    ev.message_id = "1234567890-0"
    return ev


def _make_manager():
    from src.services.worker import StreamConsumerManager

    settings = make_mock_settings()
    mgr = StreamConsumerManager(settings)
    return mgr


class TestHandleContradictionCheck:
    """Handler calls MemoryService.check_contradictions when payload is valid."""

    @pytest.mark.asyncio
    async def test_calls_check_contradictions_with_correct_args(self):
        mgr = _make_manager()
        event = _make_bus_event({"memory_id": "mem_001", "fact_text": "Alice lives in Berlin"})

        mock_memory_service = MagicMock()
        mock_memory_service.check_contradictions = AsyncMock(return_value=[])

        mock_db = AsyncMock()
        mock_db.__aenter__ = AsyncMock(return_value=mock_db)
        mock_db.__aexit__ = AsyncMock(return_value=False)

        mock_factory_instance = MagicMock()
        mock_factory_instance.return_value = mock_db

        with (
            patch(
                "src.services.worker.get_session_factory",
                return_value=mock_factory_instance,
            ),
            patch(
                "src.services.worker.resolve_workspace_id",
                new_callable=AsyncMock,
                return_value=TEST_WORKSPACE_ID,
            ),
            patch(
                "src.services.memory_service.MemoryService",
                return_value=mock_memory_service,
            ),
        ):
            await mgr._handle_contradiction_check(event)

        mock_memory_service.check_contradictions.assert_awaited_once_with(
            user_id=TEST_USER_ID,
            new_fact="Alice lives in Berlin",
            new_memory_id="mem_001",
            workspace_id=TEST_WORKSPACE_ID,
        )

    @pytest.mark.asyncio
    async def test_logs_superseded_memories(self):
        """If contradictions found (superseded list non-empty), no exception raised."""
        mgr = _make_manager()
        event = _make_bus_event({"memory_id": "mem_002", "fact_text": "Alice lives in Paris"})

        mock_memory_service = MagicMock()
        mock_memory_service.check_contradictions = AsyncMock(
            return_value=["mem_old_001", "mem_old_002"]
        )

        mock_db = AsyncMock()
        mock_db.__aenter__ = AsyncMock(return_value=mock_db)
        mock_db.__aexit__ = AsyncMock(return_value=False)
        mock_db.commit = AsyncMock()

        mock_factory_instance = MagicMock()
        mock_factory_instance.return_value = mock_db

        with (
            patch("src.services.worker.get_session_factory", return_value=mock_factory_instance),
            patch(
                "src.services.worker.resolve_workspace_id",
                new_callable=AsyncMock,
                return_value=TEST_WORKSPACE_ID,
            ),
            patch(
                "src.services.memory_service.MemoryService",
                return_value=mock_memory_service,
            ),
        ):
            # Should complete without raising
            await mgr._handle_contradiction_check(event)

        mock_memory_service.check_contradictions.assert_awaited_once()


class TestHandleContradictionCheckSkipsOnMissingFields:
    """Handler is a no-op when required payload fields are absent."""

    @pytest.mark.asyncio
    async def test_skips_when_memory_id_missing(self):
        mgr = _make_manager()
        event = _make_bus_event({"fact_text": "Alice lives in Berlin"})  # no memory_id

        with patch("src.services.worker.get_session_factory") as mock_factory:
            await mgr._handle_contradiction_check(event)

        mock_factory.assert_not_called()

    @pytest.mark.asyncio
    async def test_skips_when_memory_id_empty(self):
        mgr = _make_manager()
        event = _make_bus_event({"memory_id": "", "fact_text": "Some fact"})

        with patch("src.services.worker.get_session_factory") as mock_factory:
            await mgr._handle_contradiction_check(event)

        mock_factory.assert_not_called()

    @pytest.mark.asyncio
    async def test_skips_when_fact_text_missing(self):
        mgr = _make_manager()
        event = _make_bus_event({"memory_id": "mem_003"})  # no fact_text

        with patch("src.services.worker.get_session_factory") as mock_factory:
            await mgr._handle_contradiction_check(event)

        mock_factory.assert_not_called()

    @pytest.mark.asyncio
    async def test_skips_when_fact_text_empty(self):
        mgr = _make_manager()
        event = _make_bus_event({"memory_id": "mem_004", "fact_text": ""})

        with patch("src.services.worker.get_session_factory") as mock_factory:
            await mgr._handle_contradiction_check(event)

        mock_factory.assert_not_called()
