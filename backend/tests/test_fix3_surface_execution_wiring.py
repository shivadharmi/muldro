"""Tests for Fix-3: Surface & Execution Wiring.

Covers:
- SurfaceKind and SurfacePreview contract additions
- surface_id parameter passing (no instance state)
- surface_id checkpoint persistence and resume retrieval
- Permanent step failure surface emission
- DAG-level failed branch step population
- Redis connection reuse in _publish_progress
"""

from datetime import datetime, timezone
from unittest.mock import ANY, AsyncMock, MagicMock, patch

import pytest

from src.services.graph_executor import GraphExecutor
from src.ui.contracts import SurfaceKind, SurfacePreview

# ── Helpers ──────────────────────────────────────────────────────


def _make_executor(redis_mock=None) -> GraphExecutor:
    settings = MagicMock()
    settings.redis_url = "redis://localhost"
    settings.resolved_model = "claude-sonnet-4-6-20250514"
    db = AsyncMock()
    executor = GraphExecutor(settings=settings, db=db, redis=redis_mock)
    return executor


def _make_step(step_id="step_01", status="pending", task_id="t1", input_data=None):
    step = MagicMock()
    step.step_id = step_id
    step.task_id = task_id
    step.status = status
    step.name = None
    step.input_data = input_data or {"capability": "email.send"}
    step.output_data = None
    step.depends_on = None
    step.started_at = None
    step.completed_at = None
    step.retry_count = 0
    step.max_retries = 1
    step.error = None
    return step


def _make_run(run_id="run_01", user_id="usr_01", workspace_id="ws_01", checkpoint=None):
    run = MagicMock()
    run.run_id = run_id
    run.user_id = user_id
    run.workspace_id = workspace_id
    run.plan_id = "plan_01"
    run.status = "running"
    run.checkpoint = checkpoint
    run.source = "plan"
    run.current_step_ids = []
    run.started_at = datetime.now(timezone.utc)
    run.completed_at = None
    run.timeout_seconds = None
    run.error = None
    return run


# ── Phase 1: Surface Contracts ───────────────────────────────────


class TestSurfaceContracts:
    def test_proactive_insight_is_valid_surface_kind(self):
        """proactive_insight should be accepted as a SurfaceKind value."""
        # SurfaceKind is a Literal type — validate by using it in a model
        from pydantic import BaseModel

        class TestModel(BaseModel):
            kind: SurfaceKind

        m = TestModel(kind="proactive_insight")
        assert m.kind == "proactive_insight"

    def test_proposal_is_valid_surface_preview_status(self):
        """proposal should be accepted as a SurfacePreview status."""
        preview = SurfacePreview(title="Test", status="proposal")
        assert preview.status == "proposal"

    def test_existing_statuses_still_valid(self):
        """Existing statuses should still work."""
        for status in [
            "pending",
            "running",
            "completed",
            "failed",
            "awaiting_approval",
            "cancelled",
        ]:
            preview = SurfacePreview(title="Test", status=status)
            assert preview.status == status


# ── Phase 2: surface_id Propagation ──────────────────────────────


class TestSurfaceIdPropagation:
    def test_no_current_surface_id_attribute(self):
        """GraphExecutor should not have _current_surface_id as instance state."""
        executor = _make_executor()
        assert not hasattr(executor, "_current_surface_id")

    @pytest.mark.asyncio
    async def test_surface_id_stored_in_checkpoint_on_execute(self):
        """execute_run should store surface_id in checkpoint."""
        executor = _make_executor()
        run = _make_run()
        run.status = "pending"

        # Mock DB query to return the run
        mock_result = MagicMock()
        mock_result.scalar_one_or_none.return_value = run
        executor._db.execute = AsyncMock(return_value=mock_result)

        # Mock _execute_dag to do nothing
        executor._execute_dag = AsyncMock()
        executor._audit = AsyncMock()
        executor._audit.log = AsyncMock()

        # Patch transition_run to just set status
        with patch("src.services.graph_executor.transition_run"):
            await executor.execute_run("run_01", surface_id="surf_test_123")

        # Verify surface_id was stored in checkpoint
        assert run.checkpoint is not None
        assert run.checkpoint.get("surface_id") == "surf_test_123"

    @pytest.mark.asyncio
    async def test_resume_run_retrieves_surface_id_from_checkpoint(self):
        """resume_run should retrieve surface_id from checkpoint and pass to _execute_dag."""
        executor = _make_executor()
        run = _make_run(checkpoint={"surface_id": "surf_resume_456"})
        run.status = "awaiting_approval"

        mock_result = MagicMock()
        mock_result.scalar_one_or_none.return_value = run
        executor._db.execute = AsyncMock(return_value=mock_result)
        executor._execute_dag = AsyncMock()

        with patch("src.services.graph_executor.transition_run"):
            await executor.resume_run("run_01")

        executor._execute_dag.assert_called_once_with(
            run, surface_id="surf_resume_456", cancel_event=ANY
        )

    @pytest.mark.asyncio
    async def test_resume_run_passes_none_when_no_checkpoint_surface(self):
        """resume_run passes None surface_id when checkpoint has no surface_id."""
        executor = _make_executor()
        run = _make_run(checkpoint={"status": "running"})
        run.status = "paused"

        mock_result = MagicMock()
        mock_result.scalar_one_or_none.return_value = run
        executor._db.execute = AsyncMock(return_value=mock_result)
        executor._execute_dag = AsyncMock()

        with patch("src.services.graph_executor.transition_run"):
            await executor.resume_run("run_01")

        executor._execute_dag.assert_called_once_with(run, surface_id=None, cancel_event=ANY)


# ── Phase 2.3: Permanent failure surface emission ────────────────


class TestStepFailureSurfaceEmission:
    @pytest.mark.asyncio
    async def test_permanent_failure_emits_surface_update(self):
        """When a step permanently fails, surface update with steps should be emitted."""
        redis = AsyncMock()
        executor = _make_executor(redis_mock=redis)
        run = _make_run()
        step = _make_step()
        step.retry_count = 1  # Already at max_retries (1)

        # Mock _get_all_steps
        all_steps = [step, _make_step(step_id="step_02", status="pending")]
        executor._get_all_steps = AsyncMock(return_value=all_steps)
        executor._emit_event = AsyncMock()
        executor._emit_surface_update = AsyncMock()

        with patch("src.services.graph_executor.transition_step"):
            await executor._handle_step_failure(
                run, step, Exception("boom"), 100, surface_id="surf_fail"
            )

        executor._emit_surface_update.assert_called_once()
        call_kwargs = executor._emit_surface_update.call_args.kwargs
        assert call_kwargs["surface_id"] == "surf_fail"
        assert call_kwargs["phase"] == "failed"
        assert len(call_kwargs["steps"]) == 2

    @pytest.mark.asyncio
    async def test_retryable_failure_no_surface_emission(self):
        """When a step fails but can retry, no surface update should be emitted."""
        executor = _make_executor()
        run = _make_run()
        step = _make_step()
        step.retry_count = 0
        step.max_retries = 3

        executor._emit_surface_update = AsyncMock()

        with patch("src.services.graph_executor.transition_step"):
            await executor._handle_step_failure(
                run, step, Exception("retry"), 100, surface_id="surf_x"
            )

        executor._emit_surface_update.assert_not_called()


# ── Phase 4: Redis connection reuse ──────────────────────────────


class TestPublishProgressRedisReuse:
    @pytest.mark.asyncio
    async def test_uses_self_redis_when_available(self):
        """_publish_progress should use self._redis instead of creating new connection."""
        redis = AsyncMock()
        executor = _make_executor(redis_mock=redis)

        await executor._publish_progress("run_01", {"event": "test"})

        redis.publish.assert_called_once()
        channel = redis.publish.call_args.args[0]
        assert channel == "jarvis:run_progress:run_01"

    @pytest.mark.asyncio
    async def test_fallback_creates_connection_when_no_redis(self):
        """_publish_progress should create a connection when self._redis is None."""
        executor = _make_executor(redis_mock=None)

        mock_redis = AsyncMock()
        with patch("redis.asyncio.from_url", return_value=mock_redis):
            await executor._publish_progress("run_01", {"event": "test"})

        mock_redis.publish.assert_called_once()
        mock_redis.aclose.assert_called_once()
