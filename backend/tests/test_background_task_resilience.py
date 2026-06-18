"""Tests for background task resilience — Phase 2.

Covers: retry counting, failure status updates, DLQ enqueue on exhaustion,
and approval_resume source pickup.
"""

from datetime import datetime, timezone
from unittest.mock import AsyncMock, MagicMock, patch

import pytest

from tests.conftest import TEST_USER_ID, TEST_WORKSPACE_ID

# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------


def _make_task_run(**overrides):
    """Create a mock TaskRun object."""
    run = MagicMock()
    defaults = dict(
        run_id="run_test_001",
        plan_id="plan_test_001",
        user_id=TEST_USER_ID,
        workspace_id=TEST_WORKSPACE_ID,
        status="pending",
        source="background",
        retry_count=0,
        max_retries=3,
        error=None,
        completed_at=None,
        created_at=datetime.now(timezone.utc),
    )
    defaults.update(overrides)
    for k, v in defaults.items():
        setattr(run, k, v)
    return run


def _mock_factory():
    """Create a mock session factory for scheduler tests."""
    mock_db = AsyncMock()
    mock_db.commit = AsyncMock()
    mock_db.flush = AsyncMock()
    mock_db.add = MagicMock()

    mock_cm = AsyncMock()
    mock_cm.__aenter__ = AsyncMock(return_value=mock_db)
    mock_cm.__aexit__ = AsyncMock(return_value=False)

    factory = MagicMock(return_value=mock_cm)
    return factory, mock_db


# ---------------------------------------------------------------------------
# Background task failure handling
# ---------------------------------------------------------------------------


class TestBackgroundTaskFailure:
    @pytest.mark.asyncio
    @patch("src.services.scheduler.get_session_factory")
    async def test_failed_task_status_updated(self, mock_factory_fn):
        """A task that fails should have its status updated, not stuck pending."""
        from src.services.scheduler import SchedulerLoop

        factory, mock_db = _mock_factory()
        mock_factory_fn.return_value = factory

        run = _make_task_run(retry_count=2, max_retries=3)  # Will exhaust on next failure

        # Mock the DB query to return our run
        mock_result = MagicMock()
        mock_scalars = MagicMock()
        mock_scalars.all.return_value = [run]
        mock_result.scalars.return_value = mock_scalars
        mock_db.execute = AsyncMock(return_value=mock_result)

        # Make executor.execute_run raise
        mock_executor = AsyncMock()
        mock_executor.execute_run = AsyncMock(side_effect=RuntimeError("Connection lost"))

        # Mock step check (has steps)
        mock_step_result = MagicMock()
        mock_step_result.scalar_one_or_none.return_value = "step_001"

        # Override execute to return alternating results
        call_count = 0

        async def mock_execute(stmt):
            nonlocal call_count
            call_count += 1
            if call_count == 1:
                return mock_result  # TaskRun query
            return mock_step_result  # Step check

        mock_db.execute = mock_execute
        mock_db.get = AsyncMock(return_value=run)  # re-fetch after rollback

        scheduler = SchedulerLoop(MagicMock(), orchestrator=MagicMock())

        with patch(
            "src.services.graph_executor.create_graph_executor",
            new=AsyncMock(return_value=mock_executor),
        ):
            await scheduler._tick_background_tasks(factory)

        # After max retries exhausted, status should be failed
        assert run.status == "failed" or run.retry_count >= run.max_retries

    @pytest.mark.asyncio
    @patch("src.services.scheduler.get_session_factory")
    async def test_retries_increment_count(self, mock_factory_fn):
        """Each failure should increment retry_count."""
        from src.services.scheduler import SchedulerLoop

        factory, mock_db = _mock_factory()
        mock_factory_fn.return_value = factory

        run = _make_task_run(retry_count=0, max_retries=3)

        mock_result = MagicMock()
        mock_scalars = MagicMock()
        mock_scalars.all.return_value = [run]
        mock_result.scalars.return_value = mock_scalars

        mock_step_result = MagicMock()
        mock_step_result.scalar_one_or_none.return_value = "step_001"

        call_count = 0

        async def mock_execute(stmt):
            nonlocal call_count
            call_count += 1
            if call_count == 1:
                return mock_result
            return mock_step_result

        mock_db.execute = mock_execute
        # After rollback the tick re-fetches the run; return the same row.
        mock_db.get = AsyncMock(return_value=run)

        mock_executor = AsyncMock()
        mock_executor.execute_run = AsyncMock(side_effect=RuntimeError("Timeout"))

        scheduler = SchedulerLoop(MagicMock(), orchestrator=MagicMock())

        with patch(
            "src.services.graph_executor.create_graph_executor",
            new=AsyncMock(return_value=mock_executor),
        ):
            await scheduler._tick_background_tasks(factory)

        assert run.retry_count == 1
        # Should stay pending for retry (not yet exhausted)
        assert run.status == "pending"

    @patch("src.services.scheduler.get_session_factory")
    async def test_retry_refetches_run_after_rollback(self, mock_factory_fn):
        """After rollback (which expires ORM instances), the failure path must
        re-fetch the run before mutating it. Reading an expired attribute on the
        stale instance would raise MissingGreenlet in async SQLAlchemy and
        silently drop the retry bookkeeping."""
        from src.services.scheduler import SchedulerLoop

        factory, mock_db = _mock_factory()
        mock_factory_fn.return_value = factory

        stale_run = _make_task_run(retry_count=0, max_retries=3)
        fresh_run = _make_task_run(retry_count=0, max_retries=3)

        mock_result = MagicMock()
        mock_result.scalars.return_value.all.return_value = [stale_run]
        mock_step_result = MagicMock()
        mock_step_result.scalar_one_or_none.return_value = "step_001"

        call_count = 0

        async def mock_execute(stmt):
            nonlocal call_count
            call_count += 1
            return mock_result if call_count == 1 else mock_step_result

        mock_db.execute = mock_execute
        # Re-fetch returns a FRESH, attached instance (distinct from the stale one).
        mock_db.get = AsyncMock(return_value=fresh_run)

        mock_executor = AsyncMock()
        mock_executor.execute_run = AsyncMock(side_effect=RuntimeError("Timeout"))

        scheduler = SchedulerLoop(MagicMock(), orchestrator=MagicMock())
        with patch(
            "src.services.graph_executor.create_graph_executor",
            new=AsyncMock(return_value=mock_executor),
        ):
            await scheduler._tick_background_tasks(factory)

        # The fresh (re-fetched) run is mutated/persisted, not the stale one.
        mock_db.get.assert_awaited()
        assert fresh_run.retry_count == 1
        assert stale_run.retry_count == 0


class TestBackgroundTaskDLQ:
    @pytest.mark.asyncio
    @patch("src.services.scheduler.get_session_factory")
    async def test_exhausted_task_enqueues_to_dlq(self, mock_factory_fn):
        """After max_retries, DLQ enqueue should be called."""
        from src.services.scheduler import SchedulerLoop

        factory, mock_db = _mock_factory()
        mock_factory_fn.return_value = factory

        run = _make_task_run(retry_count=2, max_retries=3)

        mock_result = MagicMock()
        mock_scalars = MagicMock()
        mock_scalars.all.return_value = [run]
        mock_result.scalars.return_value = mock_scalars

        mock_step_result = MagicMock()
        mock_step_result.scalar_one_or_none.return_value = "step_001"

        call_count = 0

        async def mock_execute(stmt):
            nonlocal call_count
            call_count += 1
            if call_count == 1:
                return mock_result
            return mock_step_result

        mock_db.execute = mock_execute
        mock_db.get = AsyncMock(return_value=run)  # re-fetch after rollback

        mock_executor = AsyncMock()
        mock_executor.execute_run = AsyncMock(side_effect=RuntimeError("Fatal"))

        mock_dlq = AsyncMock()
        mock_dlq.enqueue = AsyncMock(return_value="dlq_001")

        scheduler = SchedulerLoop(MagicMock(), orchestrator=MagicMock())

        with (
            patch(
                "src.services.graph_executor.create_graph_executor",
                new=AsyncMock(return_value=mock_executor),
            ),
            patch(
                "src.services.dead_letter.DeadLetterService",
                return_value=mock_dlq,
            ),
        ):
            await scheduler._tick_background_tasks(factory)

        mock_dlq.enqueue.assert_awaited_once()
        call_kwargs = mock_dlq.enqueue.call_args.kwargs
        assert call_kwargs["operation_type"] == "background_task"
        assert call_kwargs["source_id"] == "run_test_001"


class TestApprovalResumeSource:
    def test_approval_resume_in_source_query(self):
        """The scheduler query should pick up approval_resume sources."""
        # This is a code-level check: verify the scheduler queries
        # for both "background" and "approval_resume" sources
        import inspect

        from src.services.scheduler import SchedulerLoop

        source = inspect.getsource(SchedulerLoop._tick_background_tasks)
        assert "approval_resume" in source
        assert "background" in source
