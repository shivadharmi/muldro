"""Tests for StreamConsumerManager._handle_graph_sync.

TDD — tests written before implementation.
Covers:
  - syncs entity when entity_id present in payload
  - ignores non-entity events (no entity_id in payload)
  - skips sync when neo4j_url is not configured
  - skips when entity_id missing from payload
"""

from __future__ import annotations

from unittest.mock import AsyncMock, MagicMock, patch

import pytest

from tests.conftest import TEST_USER_ID, TEST_WORKSPACE_ID, make_mock_settings


def _make_bus_event(payload: dict, event_type: str = "entity.created") -> MagicMock:
    """Build a minimal BusEvent-like mock."""
    ev = MagicMock()
    ev.user_id = TEST_USER_ID
    ev.event_type = event_type
    ev.payload = payload
    ev.message_id = "1234567890-0"
    return ev


def _make_manager(neo4j_url: str = "bolt://localhost:7687"):
    """Instantiate StreamConsumerManager with mock settings."""
    from src.services.worker import StreamConsumerManager

    settings = make_mock_settings(neo4j_url=neo4j_url)
    mgr = StreamConsumerManager(settings)
    return mgr


class TestHandleGraphSyncEntityPresent:
    """Handler syncs entity to Neo4j when entity_id is in the payload."""

    @pytest.mark.asyncio
    async def test_syncs_entity_via_graph_sync_service(self):
        mgr = _make_manager()
        event = _make_bus_event({"entity_id": "ent_001"})

        mock_graph_sync = AsyncMock()
        mock_graph_sync.on_entity_change = AsyncMock()
        mock_graph_sync.close = AsyncMock()

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
                "src.services.worker.GraphSyncService",
                return_value=mock_graph_sync,
            ),
        ):
            await mgr._handle_graph_sync(event)

        mock_graph_sync.on_entity_change.assert_awaited_once_with(event)
        mock_graph_sync.close.assert_awaited_once()

    @pytest.mark.asyncio
    async def test_logs_and_closes_on_sync_error(self):
        """Even if on_entity_change raises, close() is still called."""
        mgr = _make_manager()
        event = _make_bus_event({"entity_id": "ent_002"})

        mock_graph_sync = AsyncMock()
        mock_graph_sync.on_entity_change = AsyncMock(side_effect=RuntimeError("neo4j down"))
        mock_graph_sync.close = AsyncMock()

        mock_db = AsyncMock()
        mock_db.__aenter__ = AsyncMock(return_value=mock_db)
        mock_db.__aexit__ = AsyncMock(return_value=False)

        mock_factory_instance = MagicMock()
        mock_factory_instance.return_value = mock_db

        with (
            patch("src.services.worker.get_session_factory", return_value=mock_factory_instance),
            patch(
                "src.services.worker.resolve_workspace_id",
                new_callable=AsyncMock,
                return_value=TEST_WORKSPACE_ID,
            ),
            patch("src.services.worker.GraphSyncService", return_value=mock_graph_sync),
        ):
            # Should not raise — errors are swallowed with a warning log
            await mgr._handle_graph_sync(event)

        mock_graph_sync.close.assert_awaited_once()


class TestHandleGraphSyncNoEntityId:
    """Handler does nothing when entity_id is absent from the payload."""

    @pytest.mark.asyncio
    async def test_skips_when_entity_id_missing(self):
        mgr = _make_manager()
        event = _make_bus_event({"some_other_field": "value"})  # no entity_id

        with patch("src.services.worker.get_session_factory") as mock_factory:
            await mgr._handle_graph_sync(event)

        # get_session_factory should never be called if entity_id absent
        mock_factory.assert_not_called()

    @pytest.mark.asyncio
    async def test_skips_when_entity_id_empty_string(self):
        mgr = _make_manager()
        event = _make_bus_event({"entity_id": ""})

        with patch("src.services.worker.get_session_factory") as mock_factory:
            await mgr._handle_graph_sync(event)

        mock_factory.assert_not_called()


class TestHandleGraphSyncNoNeo4j:
    """Handler skips Neo4j sync when neo4j_url is not configured."""

    @pytest.mark.asyncio
    async def test_skips_when_neo4j_url_not_set(self):
        mgr = _make_manager(neo4j_url="")  # no neo4j
        event = _make_bus_event({"entity_id": "ent_003"})

        with patch("src.services.worker.get_session_factory") as mock_factory:
            await mgr._handle_graph_sync(event)

        mock_factory.assert_not_called()

    @pytest.mark.asyncio
    async def test_skips_when_neo4j_url_none(self):
        mgr = _make_manager(neo4j_url=None)
        event = _make_bus_event({"entity_id": "ent_004"})

        with patch("src.services.worker.get_session_factory") as mock_factory:
            await mgr._handle_graph_sync(event)

        mock_factory.assert_not_called()


class TestConsumerGroupConstants:
    """MAIN_STREAM_GROUPS and AGENT_STREAM_GROUPS replace CONSUMER_GROUPS."""

    def test_main_stream_groups_has_contradiction_checker(self):
        from src.services.worker import StreamConsumerManager

        assert "contradiction_checker" in StreamConsumerManager.MAIN_STREAM_GROUPS

    def test_agent_stream_groups_has_graph_syncer(self):
        from src.services.worker import StreamConsumerManager

        assert "graph_syncer" in StreamConsumerManager.AGENT_STREAM_GROUPS

    def test_original_groups_still_in_main(self):
        from src.services.worker import StreamConsumerManager

        for grp in ("entity_extractor", "memory_extractor", "trigger_evaluator"):
            assert grp in StreamConsumerManager.MAIN_STREAM_GROUPS

    def test_consumer_groups_removed_or_aliased(self):
        """CONSUMER_GROUPS tuple should no longer exist on the class
        (replaced by MAIN_STREAM_GROUPS + AGENT_STREAM_GROUPS)."""
        from src.services.worker import StreamConsumerManager

        assert not hasattr(StreamConsumerManager, "CONSUMER_GROUPS"), (
            "CONSUMER_GROUPS should be replaced by MAIN_STREAM_GROUPS / AGENT_STREAM_GROUPS"
        )
