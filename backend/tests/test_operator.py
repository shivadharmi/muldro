"""Tests for Operator — plan execution via GraphExecutor."""

from unittest.mock import AsyncMock, MagicMock

import pytest

from src.services.operator import Operator
from tests.conftest import TEST_USER_ID, make_mock_settings


@pytest.fixture
def settings():
    return make_mock_settings()


@pytest.fixture
def mock_db():
    db = MagicMock()
    db.add = MagicMock()
    db.commit = AsyncMock()
    db.flush = AsyncMock()
    return db


def _make_mock_run(run_id="run_001", plan_id="plan_001"):
    run = MagicMock()
    run.run_id = run_id
    run.plan_id = plan_id
    run.user_id = TEST_USER_ID
    run.status = "pending"
    return run


def _make_mock_plan():
    plan = MagicMock()
    plan.plan_id = "plan_001"
    plan.goal = "Draft investor reply"
    plan.reasoning_summary = "Investor follow-up needed"
    plan.status = "policy_checked"
    return plan


@pytest.mark.asyncio
async def test_execute_plan_delegates_to_graph(settings, mock_db):
    """Operator should delegate execution to GraphExecutor."""
    run = _make_mock_run()
    plan = _make_mock_plan()

    run_result = MagicMock()
    run_result.scalar_one_or_none.return_value = run
    plan_result = MagicMock()
    plan_result.scalar_one_or_none.return_value = plan

    mock_db.execute = AsyncMock(side_effect=[run_result, plan_result])

    mock_graph = MagicMock()
    completed_run = MagicMock()
    completed_run.status = "completed"
    mock_graph.populate_run_steps = AsyncMock()
    mock_graph.execute_run = AsyncMock(return_value=completed_run)

    operator = Operator(settings=settings, db=mock_db, graph_executor=mock_graph)
    success = await operator.execute_plan("run_001", TEST_USER_ID)

    assert success is True
    assert plan.status == "completed"
    mock_graph.populate_run_steps.assert_called_once_with("run_001", "plan_001")
    mock_graph.execute_run.assert_called_once_with("run_001")


@pytest.mark.asyncio
async def test_execute_plan_handles_graph_failure(settings, mock_db):
    """Operator should mark run as failed on GraphExecutor failure."""
    run = _make_mock_run()
    plan = _make_mock_plan()

    run_result = MagicMock()
    run_result.scalar_one_or_none.return_value = run
    plan_result = MagicMock()
    plan_result.scalar_one_or_none.return_value = plan

    mock_db.execute = AsyncMock(side_effect=[run_result, plan_result])

    mock_graph = MagicMock()
    completed_run = MagicMock()
    completed_run.status = "failed"
    mock_graph.populate_run_steps = AsyncMock()
    mock_graph.execute_run = AsyncMock(return_value=completed_run)

    operator = Operator(settings=settings, db=mock_db, graph_executor=mock_graph)
    success = await operator.execute_plan("run_001", TEST_USER_ID)

    assert success is False
    assert plan.status == "failed"


@pytest.mark.asyncio
async def test_execute_plan_fails_without_graph_executor(settings, mock_db):
    """Operator should fail gracefully when GraphExecutor is not available."""
    run = _make_mock_run()
    plan = _make_mock_plan()

    run_result = MagicMock()
    run_result.scalar_one_or_none.return_value = run
    plan_result = MagicMock()
    plan_result.scalar_one_or_none.return_value = plan

    mock_db.execute = AsyncMock(side_effect=[run_result, plan_result])

    operator = Operator(settings=settings, db=mock_db, graph_executor=None)
    success = await operator.execute_plan("run_001", TEST_USER_ID)

    assert success is False
    assert run.status == "failed"


@pytest.mark.asyncio
async def test_execute_plan_not_found(settings, mock_db):
    """Operator should return False when run not found."""
    run_result = MagicMock()
    run_result.scalar_one_or_none.return_value = None
    mock_db.execute = AsyncMock(return_value=run_result)

    operator = Operator(settings=settings, db=mock_db)
    success = await operator.execute_plan("run_missing", TEST_USER_ID)

    assert success is False


@pytest.mark.asyncio
async def test_execute_plan_graph_exception(settings, mock_db):
    """Operator should handle GraphExecutor exceptions gracefully."""
    run = _make_mock_run()
    plan = _make_mock_plan()

    run_result = MagicMock()
    run_result.scalar_one_or_none.return_value = run
    plan_result = MagicMock()
    plan_result.scalar_one_or_none.return_value = plan

    mock_db.execute = AsyncMock(side_effect=[run_result, plan_result])

    mock_graph = MagicMock()
    mock_graph.populate_run_steps = AsyncMock(
        side_effect=RuntimeError("DAG build failed")
    )

    operator = Operator(settings=settings, db=mock_db, graph_executor=mock_graph)
    success = await operator.execute_plan("run_001", TEST_USER_ID)

    assert success is False
    assert run.status == "failed"
    assert "DAG build failed" in run.error["message"]
