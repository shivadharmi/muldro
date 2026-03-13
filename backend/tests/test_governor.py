"""Tests for Governor — policy evaluation and approval creation."""

from unittest.mock import AsyncMock, MagicMock

import pytest

from src.services.governor import Governor


@pytest.fixture
def mock_db():
    db = MagicMock()
    db.add = MagicMock()
    db.commit = AsyncMock()
    db.flush = AsyncMock()
    return db


def _make_mock_plan(decision="draft_reply", risk_level="medium", tasks=None):
    plan = MagicMock()
    plan.plan_id = "plan_001"
    plan.user_id = "usr_default"
    plan.goal = "Reply to investor email"
    plan.decision = decision
    plan.risk_level = risk_level
    plan.reasoning_summary = "Investor needs response"
    plan.execution_mode = "approval_required"
    plan.status = "created"
    plan.tasks = tasks or []
    return plan


@pytest.mark.asyncio
async def test_governor_requires_approval_for_draft(mock_db):
    """Plans with draft_reply decision should require approval."""
    plan = _make_mock_plan(decision="draft_reply")

    result_mock = MagicMock()
    result_mock.scalar_one_or_none.return_value = plan
    mock_db.execute = AsyncMock(return_value=result_mock)

    governor = Governor(db=mock_db)
    decision = await governor.evaluate_plan("plan_001", "usr_default")

    assert decision == "approval_required"
    # Should have added execution + approval + 2 audit entries
    assert mock_db.add.call_count >= 2


@pytest.mark.asyncio
async def test_governor_auto_executes_acknowledge(mock_db):
    """Plans with acknowledge decision should auto-execute."""
    plan = _make_mock_plan(decision="acknowledge")

    result_mock = MagicMock()
    result_mock.scalar_one_or_none.return_value = plan
    mock_db.execute = AsyncMock(return_value=result_mock)

    governor = Governor(db=mock_db)
    decision = await governor.evaluate_plan("plan_001", "usr_default")

    assert decision == "auto_execute"


@pytest.mark.asyncio
async def test_governor_blocks_dangerous_actions(mock_db):
    """Plans with blocked decision types should be blocked."""
    plan = _make_mock_plan(decision="delete_data")

    result_mock = MagicMock()
    result_mock.scalar_one_or_none.return_value = plan
    mock_db.execute = AsyncMock(return_value=result_mock)

    governor = Governor(db=mock_db)
    decision = await governor.evaluate_plan("plan_001", "usr_default")

    assert decision == "blocked"


@pytest.mark.asyncio
async def test_governor_checks_task_types(mock_db):
    """Governor should check task types even if plan decision is neutral."""
    mock_task = MagicMock()
    mock_task.task_type = "send_email"
    plan = _make_mock_plan(decision="create_task", tasks=[mock_task])

    result_mock = MagicMock()
    result_mock.scalar_one_or_none.return_value = plan
    mock_db.execute = AsyncMock(return_value=result_mock)

    governor = Governor(db=mock_db)
    decision = await governor.evaluate_plan("plan_001", "usr_default")

    assert decision == "approval_required"


@pytest.mark.asyncio
async def test_governor_high_risk_requires_approval(mock_db):
    """High-risk plans always require approval regardless of decision."""
    plan = _make_mock_plan(decision="acknowledge", risk_level="high")

    result_mock = MagicMock()
    result_mock.scalar_one_or_none.return_value = plan
    mock_db.execute = AsyncMock(return_value=result_mock)

    governor = Governor(db=mock_db)
    decision = await governor.evaluate_plan("plan_001", "usr_default")

    assert decision == "approval_required"
