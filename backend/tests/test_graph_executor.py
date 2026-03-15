"""Tests for GraphExecutor — DAG-based execution engine."""

from unittest.mock import AsyncMock, MagicMock, patch

import pytest

from tests.conftest import make_mock_settings


@pytest.fixture
def mock_db():
    db = AsyncMock()
    db.add = MagicMock()
    db.flush = AsyncMock()
    db.commit = AsyncMock()
    db.refresh = AsyncMock()
    return db


@pytest.fixture
def settings():
    return make_mock_settings()


def _make_plan_task(task_id, task_type="summarize", depends_on=None, input_data=None):
    task = MagicMock()
    task.task_id = task_id
    task.task_type = task_type
    task.depends_on = depends_on or []
    task.input_data = input_data or {"task_type": task_type}
    task.id = hash(task_id)
    return task


def _make_plan(plan_id="plan_001", tasks=None):
    plan = MagicMock()
    plan.plan_id = plan_id
    plan.goal = "Test plan"
    plan.tasks = tasks or []
    return plan


def _make_executor(settings, mock_db):
    with patch("src.services.graph_executor.get_anthropic_client") as mock_client:
        mock_client.return_value = MagicMock()
        from src.services.graph_executor import GraphExecutor

        return GraphExecutor(settings, mock_db)


class TestBuildGraphDefinition:
    def test_builds_from_tasks(self, settings, mock_db):
        executor = _make_executor(settings, mock_db)
        tasks = [
            _make_plan_task("t1", "summarize"),
            _make_plan_task("t2", "draft_email", depends_on=["t1"]),
        ]
        graph = executor._build_graph_definition(tasks)
        assert len(graph["nodes"]) == 2
        assert len(graph["edges"]) == 1
        assert graph["edges"][0] == {"from": "t1", "to": "t2"}

    def test_no_edges_for_independent_tasks(self, settings, mock_db):
        executor = _make_executor(settings, mock_db)
        tasks = [
            _make_plan_task("t1", "summarize"),
            _make_plan_task("t2", "fetch_info"),
        ]
        graph = executor._build_graph_definition(tasks)
        assert len(graph["edges"]) == 0


class TestCreateRun:
    @patch("src.services.graph_executor.get_anthropic_client")
    async def test_creates_run_with_steps(self, mock_client, settings, mock_db):
        mock_client.return_value = MagicMock()
        from src.services.graph_executor import GraphExecutor

        plan = _make_plan(
            tasks=[
                _make_plan_task("t1"),
                _make_plan_task("t2", depends_on=["t1"]),
            ]
        )

        result_mock = MagicMock()
        result_mock.scalar_one_or_none.return_value = plan
        tasks_result = MagicMock()
        tasks_result.scalars.return_value.all.return_value = plan.tasks
        mock_db.execute = AsyncMock(side_effect=[result_mock, tasks_result])

        executor = GraphExecutor(settings, mock_db)
        run = await executor.create_run("plan_001", "usr_default")

        assert run.run_id.startswith("run_")
        assert run.plan_id == "plan_001"
        assert run.status == "pending"
        # Should have added: 1 run + 2 steps = 3 calls
        assert mock_db.add.call_count == 3


class TestCancelRun:
    @patch("src.services.graph_executor.get_anthropic_client")
    async def test_cancels_and_skips_pending(self, mock_client, settings, mock_db):
        mock_client.return_value = MagicMock()
        from src.services.graph_executor import GraphExecutor

        run = MagicMock()
        run.run_id = "run_001"
        run.status = "running"

        step2 = MagicMock()
        step2.status = "pending"
        step3 = MagicMock()
        step3.status = "ready"

        run_result = MagicMock()
        run_result.scalar_one_or_none.return_value = run
        steps_result = MagicMock()
        steps_result.scalars.return_value.all.return_value = [step2, step3]

        mock_db.execute = AsyncMock(side_effect=[run_result, steps_result])

        executor = GraphExecutor(settings, mock_db)
        cancelled = await executor.cancel_run("run_001")

        assert cancelled.status == "cancelled"
        assert step2.status == "skipped"
        assert step3.status == "skipped"


class TestPauseRun:
    @patch("src.services.graph_executor.get_anthropic_client")
    async def test_pauses_running(self, mock_client, settings, mock_db):
        mock_client.return_value = MagicMock()
        from src.services.graph_executor import GraphExecutor

        run = MagicMock()
        run.run_id = "run_001"
        run.status = "running"
        run.current_step_ids = []

        run_result = MagicMock()
        run_result.scalar_one_or_none.return_value = run
        mock_db.execute = AsyncMock(return_value=run_result)

        executor = GraphExecutor(settings, mock_db)
        paused = await executor.pause_run("run_001", "manual_pause")

        assert paused.status == "paused"
        mock_db.commit.assert_called_once()


class TestResumeRun:
    @patch("src.services.graph_executor.get_anthropic_client")
    async def test_rejects_non_paused(self, mock_client, settings, mock_db):
        mock_client.return_value = MagicMock()
        from src.services.graph_executor import GraphExecutor

        run = MagicMock()
        run.run_id = "run_001"
        run.status = "running"

        run_result = MagicMock()
        run_result.scalar_one_or_none.return_value = run
        mock_db.execute = AsyncMock(return_value=run_result)

        executor = GraphExecutor(settings, mock_db)
        with pytest.raises(ValueError, match="not paused"):
            await executor.resume_run("run_001")
