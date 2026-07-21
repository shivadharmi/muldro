"""Perception-cost redesign Task 5: ``ConnectorPoller.ingest_raw_events``
feeds the whole poll batch through a single ``EventProcessor.process_batch()``
call instead of looping ``process()`` per event — activating batched triage
(rules-first + one Haiku call per ``EventProcessor.BATCH_SIZE`` chunk) instead
of one scoring call per event.
"""

from __future__ import annotations

from unittest.mock import AsyncMock, MagicMock, patch

import pytest

from tests.conftest import TEST_USER_ID, TEST_WORKSPACE_ID, make_mock_settings, make_raw_event


def _make_poller():
    """Return a bare ``ConnectorPoller`` wired with mock DB/session plumbing.

    Mirrors ``_make_ingest_mocks`` in ``test_observation_cursor_upsert.py``.
    """
    from src.orchestrator.connector_poller import ConnectorPoller

    mock_db = MagicMock()
    mock_db.__aenter__ = AsyncMock(return_value=mock_db)
    mock_db.__aexit__ = AsyncMock(return_value=False)
    mock_db.execute = AsyncMock()
    mock_db.commit = AsyncMock()
    mock_db.rollback = AsyncMock()
    mock_db.add = MagicMock()

    mock_factory = MagicMock(return_value=mock_db)

    poller = ConnectorPoller.__new__(ConnectorPoller)
    poller._db_factory_provider = lambda: mock_factory
    poller._settings = make_mock_settings()
    poller._events = MagicMock()
    poller._events.ensure_event_bus = AsyncMock(return_value=MagicMock())
    return poller


def _make_req():
    req = MagicMock()
    req.world_model = MagicMock()
    req.memory_service = MagicMock()
    req.notifier = MagicMock()
    req.vector_store = MagicMock()
    req.extras = {}
    return req


class TestIngestUsesProcessBatch:
    """The contract: ``_ingest``/``ingest_raw_events`` calls
    ``EventProcessor.process_batch`` ONCE with the whole raw-event batch, and
    never calls the per-event ``process`` at all."""

    @pytest.mark.asyncio
    async def test_ingest_calls_process_batch_not_per_event(self):
        from src.orchestrator.connector_poller import ConnectorPoller

        poller = _make_poller()
        raw_events = [
            make_raw_event(entity_id="thr_001"),
            make_raw_event(entity_id="thr_002"),
            make_raw_event(entity_id="thr_003"),
        ]

        with (
            patch.object(poller, "_request_services", return_value=_make_req()),
            patch("src.services.event_processor.EventProcessor") as mock_ep,
            patch("src.services.dead_letter.DeadLetterService"),
        ):
            instance = mock_ep.return_value
            instance.process_batch = AsyncMock(return_value=["evt_1", "evt_2", "evt_3"])
            instance.process = AsyncMock()

            summaries = await ConnectorPoller.ingest_raw_events(
                poller,
                raw_events,
                TEST_USER_ID,
                TEST_WORKSPACE_ID,
            )

        assert instance.process_batch.await_count == 1
        assert instance.process.await_count == 0
        instance.process_batch.assert_awaited_once_with(raw_events, TEST_USER_ID, TEST_WORKSPACE_ID)
        assert len(summaries) == 3

    @pytest.mark.asyncio
    async def test_summary_shape_preserved(self):
        """The returned summaries keep the ``[source] event_type: title``
        shape the old per-event loop produced."""
        from src.orchestrator.connector_poller import ConnectorPoller

        poller = _make_poller()
        raw = make_raw_event(
            source="gmail", event_type="email_received", title="Investor follow-up on deck"
        )

        with (
            patch.object(poller, "_request_services", return_value=_make_req()),
            patch("src.services.event_processor.EventProcessor") as mock_ep,
            patch("src.services.dead_letter.DeadLetterService"),
        ):
            mock_ep.return_value.process_batch = AsyncMock(return_value=["evt_1"])

            summaries = await ConnectorPoller.ingest_raw_events(
                poller,
                [raw],
                TEST_USER_ID,
                TEST_WORKSPACE_ID,
            )

        assert summaries == ["[gmail] email_received: Investor follow-up on deck"]

    @pytest.mark.asyncio
    async def test_duplicates_excluded_from_summaries(self):
        """Events ``process_batch`` reports as duplicates (event_id is None)
        are dropped from the returned summary strings — they were not newly
        ingested this cycle."""
        from src.orchestrator.connector_poller import ConnectorPoller

        poller = _make_poller()
        raw_events = [
            make_raw_event(entity_id="thr_001", title="First"),
            make_raw_event(entity_id="thr_002", title="Duplicate"),
        ]

        with (
            patch.object(poller, "_request_services", return_value=_make_req()),
            patch("src.services.event_processor.EventProcessor") as mock_ep,
            patch("src.services.dead_letter.DeadLetterService"),
        ):
            mock_ep.return_value.process_batch = AsyncMock(return_value=["evt_1", None])

            summaries = await ConnectorPoller.ingest_raw_events(
                poller,
                raw_events,
                TEST_USER_ID,
                TEST_WORKSPACE_ID,
            )

        assert len(summaries) == 1
        assert "First" in summaries[0]

    @pytest.mark.asyncio
    async def test_empty_raw_events_calls_process_batch_with_empty_list(self):
        """No special-casing: an empty batch still goes through
        ``process_batch`` (which itself short-circuits on ``not events``)."""
        from src.orchestrator.connector_poller import ConnectorPoller

        poller = _make_poller()

        with (
            patch.object(poller, "_request_services", return_value=_make_req()),
            patch("src.services.event_processor.EventProcessor") as mock_ep,
            patch("src.services.dead_letter.DeadLetterService"),
        ):
            mock_ep.return_value.process_batch = AsyncMock(return_value=[])

            summaries = await ConnectorPoller.ingest_raw_events(
                poller,
                [],
                TEST_USER_ID,
                TEST_WORKSPACE_ID,
            )

        mock_ep.return_value.process_batch.assert_awaited_once_with(
            [], TEST_USER_ID, TEST_WORKSPACE_ID
        )
        assert summaries == []
