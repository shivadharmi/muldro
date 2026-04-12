"""Tests for DLQ retry dispatch — verifies _tick_dlq_retry actually re-executes operations."""

import logging
from unittest.mock import AsyncMock, MagicMock, patch

import pytest

from src.services.scheduler import SchedulerLoop
from tests.conftest import TEST_USER_ID, TEST_WORKSPACE_ID, make_mock_settings


def _make_dlq_entry(**overrides) -> MagicMock:
    """Factory for mock DeadLetterEntry objects."""
    defaults = dict(
        entry_id="dlq_test_001",
        user_id=TEST_USER_ID,
        workspace_id=TEST_WORKSPACE_ID,
        operation_type="background_task",
        payload={"run_id": "run_test_001", "plan_id": "plan_test_001"},
        attempt_count=1,
        max_attempts=3,
        status="pending",
        source_id=None,
    )
    defaults.update(overrides)
    entry = MagicMock()
    for k, v in defaults.items():
        setattr(entry, k, v)
    return entry


def _make_scheduler(settings=None, orchestrator=None, user_ids=None) -> SchedulerLoop:
    """Create a SchedulerLoop without calling __init__ fully, then set internals."""
    scheduler = SchedulerLoop.__new__(SchedulerLoop)
    scheduler._settings = settings or make_mock_settings()
    scheduler._orchestrator = orchestrator
    scheduler._user_ids = user_ids or [TEST_USER_ID]
    scheduler._running = False
    return scheduler


class TestDlqRetryDispatchBackgroundTask:
    """Verify background_task DLQ entries are re-dispatched via transition_run."""

    @pytest.mark.asyncio
    async def test_dlq_retry_dispatches_background_task(self):
        """Verify transition_run called with 'pending' and mark_resolved called."""
        entry = _make_dlq_entry(
            operation_type="background_task",
            payload={"run_id": "run_test_001", "plan_id": "plan_test_001"},
        )

        mock_run = MagicMock()
        mock_run.run_id = "run_test_001"
        mock_run.status = "failed"

        mock_dlq = MagicMock()
        mock_dlq.list_pending = AsyncMock(return_value=[entry])
        mock_dlq.mark_retrying = AsyncMock(return_value=True)
        mock_dlq.mark_resolved = AsyncMock()

        mock_db = MagicMock()
        mock_db.get = AsyncMock(return_value=mock_run)
        mock_db.commit = AsyncMock()
        mock_db.__aenter__ = AsyncMock(return_value=mock_db)
        mock_db.__aexit__ = AsyncMock(return_value=False)

        mock_factory = MagicMock(return_value=mock_db)

        scheduler = _make_scheduler()

        with (
            patch("src.services.scheduler.DeadLetterService", return_value=mock_dlq),
            patch("src.services.scheduler.transition_run") as mock_transition,
        ):
            await scheduler._tick_dlq_retry(mock_factory)

            mock_dlq.mark_retrying.assert_awaited_once_with(entry.entry_id)
            mock_db.get.assert_awaited_once()
            mock_transition.assert_called_once_with(mock_run, "pending")
            mock_dlq.mark_resolved.assert_awaited_once_with(entry.entry_id)


class TestDlqRetryUnknownOperationType:
    """Verify unknown operation types are logged as warnings."""

    @pytest.mark.asyncio
    async def test_dlq_retry_unknown_operation_type_logs_warning(self, caplog):
        """Verify warning logged for unknown operation types."""
        entry = _make_dlq_entry(
            operation_type="alien_abduction",
            payload={"ufo": True},
        )

        mock_dlq = MagicMock()
        mock_dlq.list_pending = AsyncMock(return_value=[entry])
        mock_dlq.mark_retrying = AsyncMock(return_value=True)
        mock_dlq.mark_resolved = AsyncMock()

        mock_db = MagicMock()
        mock_db.commit = AsyncMock()
        mock_db.__aenter__ = AsyncMock(return_value=mock_db)
        mock_db.__aexit__ = AsyncMock(return_value=False)

        mock_factory = MagicMock(return_value=mock_db)

        scheduler = _make_scheduler()

        with (
            patch("src.services.scheduler.DeadLetterService", return_value=mock_dlq),
            caplog.at_level(logging.WARNING, logger="src.services.scheduler"),
        ):
            await scheduler._tick_dlq_retry(mock_factory)

            mock_dlq.mark_retrying.assert_awaited_once_with(entry.entry_id)
            mock_dlq.mark_resolved.assert_not_called()
            assert any(
                "unknown" in r.message.lower() or "alien_abduction" in r.message
                for r in caplog.records
            )


class TestDlqRetryHandlerFailure:
    """Verify exceptions in dispatch handlers do not crash the loop."""

    @pytest.mark.asyncio
    async def test_dlq_retry_handler_failure_does_not_crash(self):
        """Exception in dispatch handler should be caught, not propagate."""
        entry = _make_dlq_entry(
            operation_type="background_task",
            payload={"run_id": "run_test_001"},
        )

        mock_dlq = MagicMock()
        mock_dlq.list_pending = AsyncMock(return_value=[entry])
        mock_dlq.mark_retrying = AsyncMock(return_value=True)
        mock_dlq.mark_resolved = AsyncMock()

        mock_db = MagicMock()
        # db.get raises an exception — simulating handler failure
        mock_db.get = AsyncMock(side_effect=RuntimeError("DB connection lost"))
        mock_db.commit = AsyncMock()
        mock_db.__aenter__ = AsyncMock(return_value=mock_db)
        mock_db.__aexit__ = AsyncMock(return_value=False)

        mock_factory = MagicMock(return_value=mock_db)

        scheduler = _make_scheduler()

        with patch("src.services.scheduler.DeadLetterService", return_value=mock_dlq):
            # Should not raise
            await scheduler._tick_dlq_retry(mock_factory)

            # mark_resolved should NOT have been called since dispatch failed
            mock_dlq.mark_resolved.assert_not_called()
            # Commit should still have been called (outer try/commit)
            mock_db.commit.assert_awaited()


class TestDlqRetryDispatchEmbedding:
    """Verify failed_embedding DLQ entries are re-dispatched via EmbeddingService."""

    @pytest.mark.asyncio
    async def test_dlq_retry_dispatches_failed_embedding(self):
        """Verify EmbeddingService.embed_text called and mark_resolved on success."""
        entry = _make_dlq_entry(
            operation_type="failed_embedding",
            payload={"text": "some text to embed"},
        )

        mock_dlq = MagicMock()
        mock_dlq.list_pending = AsyncMock(return_value=[entry])
        mock_dlq.mark_retrying = AsyncMock(return_value=True)
        mock_dlq.mark_resolved = AsyncMock()

        mock_embed_svc = MagicMock()
        mock_embed_svc.embed_text = AsyncMock(return_value=[0.1, 0.2, 0.3])

        mock_db = MagicMock()
        mock_db.commit = AsyncMock()
        mock_db.__aenter__ = AsyncMock(return_value=mock_db)
        mock_db.__aexit__ = AsyncMock(return_value=False)

        mock_factory = MagicMock(return_value=mock_db)

        scheduler = _make_scheduler()

        with (
            patch("src.services.scheduler.DeadLetterService", return_value=mock_dlq),
            patch(
                "src.services.scheduler.EmbeddingService",
                return_value=mock_embed_svc,
            ),
        ):
            await scheduler._tick_dlq_retry(mock_factory)

            mock_embed_svc.embed_text.assert_awaited_once_with("some text to embed")
            mock_dlq.mark_resolved.assert_awaited_once_with(entry.entry_id)


class TestDlqRetryDispatchPerception:
    """Verify perception_cycle DLQ entries bump perception via orchestrator."""

    @pytest.mark.asyncio
    async def test_dlq_retry_dispatches_perception_cycle(self):
        """Verify orchestrator._bump_perception_for_sources called."""
        entry = _make_dlq_entry(
            operation_type="perception_cycle",
            payload={"source": "gmail"},
        )

        mock_dlq = MagicMock()
        mock_dlq.list_pending = AsyncMock(return_value=[entry])
        mock_dlq.mark_retrying = AsyncMock(return_value=True)
        mock_dlq.mark_resolved = AsyncMock()

        mock_orch = MagicMock()
        mock_orch._bump_perception_for_sources = AsyncMock()

        mock_db = MagicMock()
        mock_db.commit = AsyncMock()
        mock_db.__aenter__ = AsyncMock(return_value=mock_db)
        mock_db.__aexit__ = AsyncMock(return_value=False)

        mock_factory = MagicMock(return_value=mock_db)

        scheduler = _make_scheduler(orchestrator=mock_orch)

        with patch("src.services.scheduler.DeadLetterService", return_value=mock_dlq):
            await scheduler._tick_dlq_retry(mock_factory)

            mock_orch._bump_perception_for_sources.assert_awaited_once_with(
                ["gmail"], entry.user_id, entry.workspace_id
            )
            mock_dlq.mark_resolved.assert_awaited_once_with(entry.entry_id)


class TestDlqRetryExhausted:
    """Verify exhausted entries are not dispatched."""

    @pytest.mark.asyncio
    async def test_dlq_retry_exhausted_entry_not_dispatched(self):
        """When mark_retrying returns False, no dispatch should happen."""
        entry = _make_dlq_entry(
            operation_type="background_task",
            payload={"run_id": "run_test_001"},
        )

        mock_dlq = MagicMock()
        mock_dlq.list_pending = AsyncMock(return_value=[entry])
        mock_dlq.mark_retrying = AsyncMock(return_value=False)
        mock_dlq.mark_resolved = AsyncMock()

        mock_db = MagicMock()
        mock_db.get = AsyncMock()
        mock_db.commit = AsyncMock()
        mock_db.__aenter__ = AsyncMock(return_value=mock_db)
        mock_db.__aexit__ = AsyncMock(return_value=False)

        mock_factory = MagicMock(return_value=mock_db)

        scheduler = _make_scheduler()

        with patch("src.services.scheduler.DeadLetterService", return_value=mock_dlq):
            await scheduler._tick_dlq_retry(mock_factory)

            mock_db.get.assert_not_awaited()
            mock_dlq.mark_resolved.assert_not_called()
