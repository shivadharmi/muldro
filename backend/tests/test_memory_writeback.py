"""Tests for Phase 3A: Memory writeback from execution results."""

from unittest.mock import AsyncMock, MagicMock, patch

from tests.conftest import make_mock_settings


def _make_executor(memory_service=None):
    """Create a GraphExecutor with mocked dependencies."""
    from src.services.graph_executor import GraphExecutor

    settings = make_mock_settings()
    db = AsyncMock()
    db.add = MagicMock()
    db.commit = AsyncMock()
    db.flush = AsyncMock()
    db.execute = AsyncMock()

    with patch("src.services.graph_executor.get_anthropic_client"):
        executor = GraphExecutor(
            settings=settings,
            db=db,
            memory_service=memory_service,
        )
    return executor


def _make_run(run_id="run_001", plan_id="plan_001", user_id="usr_1", status="completed"):
    run = MagicMock()
    run.run_id = run_id
    run.plan_id = plan_id
    run.user_id = user_id
    run.status = status
    return run


def _make_step(step_id, task_id, status="completed", output_data=None):
    step = MagicMock()
    step.step_id = step_id
    step.task_id = task_id
    step.status = status
    step.output_data = output_data
    return step


class TestMemoryWriteback:
    async def test_writeback_calls_extract_and_store(self):
        """Completed run with output data triggers memory extraction."""
        mem_svc = AsyncMock()
        mem_svc.extract_and_store = AsyncMock()
        executor = _make_executor(memory_service=mem_svc)

        run = _make_run()
        steps = [
            _make_step("s1", "t1", "completed", {"result": "email sent"}),
            _make_step("s2", "t2", "completed", {"result": "calendar created"}),
        ]
        executor._get_all_steps = AsyncMock(return_value=steps)

        await executor._writeback_memories(run)

        mem_svc.extract_and_store.assert_called_once()
        call_kwargs = mem_svc.extract_and_store.call_args[1]
        assert call_kwargs["user_id"] == "usr_1"
        assert "plan_001" in call_kwargs["source_text"]
        assert "run_001" in call_kwargs["source_event_ids"]

    async def test_writeback_skipped_without_memory_service(self):
        """No memory_service means writeback is silently skipped."""
        executor = _make_executor(memory_service=None)
        run = _make_run()
        # Should not raise
        await executor._writeback_memories(run)

    async def test_writeback_skipped_no_completed_steps(self):
        """No completed steps with output → no writeback."""
        mem_svc = AsyncMock()
        executor = _make_executor(memory_service=mem_svc)

        run = _make_run()
        steps = [
            _make_step("s1", "t1", "failed", None),
            _make_step("s2", "t2", "skipped", None),
        ]
        executor._get_all_steps = AsyncMock(return_value=steps)

        await executor._writeback_memories(run)
        mem_svc.extract_and_store.assert_not_called()

    async def test_writeback_caps_at_5_steps(self):
        """Only first 5 completed steps are included in writeback text."""
        mem_svc = AsyncMock()
        executor = _make_executor(memory_service=mem_svc)

        run = _make_run()
        steps = [_make_step(f"s{i}", f"t{i}", "completed", {"r": f"result_{i}"}) for i in range(10)]
        executor._get_all_steps = AsyncMock(return_value=steps)

        await executor._writeback_memories(run)

        call_kwargs = mem_svc.extract_and_store.call_args[1]
        # Header + 5 step lines
        lines = call_kwargs["source_text"].split("\n")
        assert len(lines) == 6  # 1 header + 5 steps

    async def test_writeback_tolerates_extract_failure(self):
        """If extract_and_store raises, writeback doesn't propagate."""
        mem_svc = AsyncMock()
        mem_svc.extract_and_store = AsyncMock(side_effect=RuntimeError("boom"))
        executor = _make_executor(memory_service=mem_svc)

        run = _make_run()
        steps = [_make_step("s1", "t1", "completed", {"ok": True})]
        executor._get_all_steps = AsyncMock(return_value=steps)

        # Should not raise
        await executor._writeback_memories(run)

    async def test_writeback_only_includes_steps_with_output(self):
        """Steps with None output_data are excluded."""
        mem_svc = AsyncMock()
        executor = _make_executor(memory_service=mem_svc)

        run = _make_run()
        steps = [
            _make_step("s1", "t1", "completed", {"data": "yes"}),
            _make_step("s2", "t2", "completed", None),  # no output
        ]
        executor._get_all_steps = AsyncMock(return_value=steps)

        await executor._writeback_memories(run)

        call_kwargs = mem_svc.extract_and_store.call_args[1]
        lines = call_kwargs["source_text"].split("\n")
        assert len(lines) == 2  # header + 1 step with output


class TestGraphExecutorMemoryServiceParam:
    def test_memory_service_stored(self):
        """memory_service param is stored on the executor."""
        mem_svc = AsyncMock()
        executor = _make_executor(memory_service=mem_svc)
        assert executor._memory_service is mem_svc

    def test_memory_service_defaults_none(self):
        executor = _make_executor()
        assert executor._memory_service is None
