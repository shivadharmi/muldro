"""Tests for Operator — plan execution and email drafting."""

import json
from unittest.mock import AsyncMock, MagicMock, patch

import pytest

from src.services.operator import Operator
from tests.conftest import make_mock_settings


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


def _make_mock_execution():
    execution = MagicMock()
    execution.execution_id = "exec_001"
    execution.plan_id = "plan_001"
    execution.user_id = "usr_default"
    execution.status = "pending"
    return execution


def _make_mock_plan(tasks=None):
    plan = MagicMock()
    plan.plan_id = "plan_001"
    plan.goal = "Draft investor reply"
    plan.reasoning_summary = "Investor follow-up needed"
    plan.status = "policy_checked"
    plan.tasks = tasks or []
    return plan


def _make_mock_task(task_type="draft_email", input_data=None):
    task = MagicMock()
    task.task_id = "ptask_001"
    task.task_type = task_type
    task.input_data = input_data or {"tone": "professional"}
    task.status = "pending"
    return task


@patch("src.services.operator.anthropic.AsyncAnthropic")
@pytest.mark.asyncio
async def test_execute_plan_drafts_email(mock_anthropic_cls, settings, mock_db):
    """Operator should execute a draft_email task via Claude."""
    draft = {
        "subject": "Re: Investor Follow-up",
        "body": "Thank you for your interest. Here is the updated deck.",
        "tone": "professional",
    }

    mock_client = MagicMock()
    response = MagicMock()
    response.content = [MagicMock(text=json.dumps(draft))]
    mock_client.messages.create = AsyncMock(return_value=response)
    mock_anthropic_cls.return_value = mock_client

    execution = _make_mock_execution()
    task = _make_mock_task()
    plan = _make_mock_plan()

    # 1st call: get execution, 2nd: get plan, 3rd: get tasks
    exec_result = MagicMock()
    exec_result.scalar_one_or_none.return_value = execution
    plan_result = MagicMock()
    plan_result.scalar_one_or_none.return_value = plan
    tasks_result = MagicMock()
    tasks_result.scalars.return_value.all.return_value = [task]

    mock_db.execute = AsyncMock(side_effect=[exec_result, plan_result, tasks_result])

    operator = Operator(settings=settings, db=mock_db)
    success = await operator.execute_plan("exec_001", "usr_default")

    assert success is True
    assert execution.status == "completed"
    assert task.status == "completed"
    mock_client.messages.create.assert_called_once()


@patch("src.services.operator.anthropic.AsyncAnthropic")
@pytest.mark.asyncio
async def test_execute_plan_handles_failure(mock_anthropic_cls, settings, mock_db):
    """Operator should mark execution as failed on task error."""
    mock_client = MagicMock()
    mock_client.messages.create = AsyncMock(side_effect=RuntimeError("Claude unavailable"))
    mock_anthropic_cls.return_value = mock_client

    execution = _make_mock_execution()
    task = _make_mock_task()
    plan = _make_mock_plan()

    exec_result = MagicMock()
    exec_result.scalar_one_or_none.return_value = execution
    plan_result = MagicMock()
    plan_result.scalar_one_or_none.return_value = plan
    tasks_result = MagicMock()
    tasks_result.scalars.return_value.all.return_value = [task]

    mock_db.execute = AsyncMock(side_effect=[exec_result, plan_result, tasks_result])

    operator = Operator(settings=settings, db=mock_db)
    success = await operator.execute_plan("exec_001", "usr_default")

    assert success is False
    assert execution.status == "failed"
    assert task.status == "failed"


@patch("src.services.operator.anthropic.AsyncAnthropic")
@pytest.mark.asyncio
async def test_execute_stub_task(mock_anthropic_cls, settings, mock_db):
    """Stub tasks (fetch_info, acknowledge) should complete without Claude."""
    mock_anthropic_cls.return_value = MagicMock()

    execution = _make_mock_execution()
    task = _make_mock_task(task_type="fetch_info")
    plan = _make_mock_plan()

    exec_result = MagicMock()
    exec_result.scalar_one_or_none.return_value = execution
    plan_result = MagicMock()
    plan_result.scalar_one_or_none.return_value = plan
    tasks_result = MagicMock()
    tasks_result.scalars.return_value.all.return_value = [task]

    mock_db.execute = AsyncMock(side_effect=[exec_result, plan_result, tasks_result])

    operator = Operator(settings=settings, db=mock_db)
    success = await operator.execute_plan("exec_001", "usr_default")

    assert success is True
    assert task.status == "completed"
