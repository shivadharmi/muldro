"""Tests for Governor — policy evaluation and approval creation."""

from unittest.mock import AsyncMock, MagicMock

import pytest

from src.orchestrator.contracts import PolicyDecision
from src.services.governor import Governor
from tests.conftest import TEST_USER_ID


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
    plan.user_id = TEST_USER_ID
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
    result = await governor.evaluate_plan("plan_001", TEST_USER_ID)

    assert isinstance(result, PolicyDecision)
    assert result.decision == "approval_required"
    assert result.execution_id is not None
    # Should have added execution + approval + 2 audit entries
    assert mock_db.add.call_count >= 2


@pytest.mark.asyncio
async def test_governor_low_risk_requires_approval_by_default(mock_db):
    """Low-risk plans require approval by default (no trust engine)."""
    plan = _make_mock_plan(risk_level="low")

    result_mock = MagicMock()
    result_mock.scalar_one_or_none.return_value = plan
    mock_db.execute = AsyncMock(return_value=result_mock)

    governor = Governor(db=mock_db)
    result = await governor.evaluate_plan("plan_001", TEST_USER_ID)

    assert isinstance(result, PolicyDecision)
    assert result.decision == "approval_required"
    assert result.execution_id is not None


@pytest.mark.asyncio
async def test_governor_critical_risk_requires_approval(mock_db):
    """Plans with critical risk always require approval."""
    plan = _make_mock_plan(risk_level="critical")

    result_mock = MagicMock()
    result_mock.scalar_one_or_none.return_value = plan
    mock_db.execute = AsyncMock(return_value=result_mock)

    governor = Governor(db=mock_db)
    result = await governor.evaluate_plan("plan_001", TEST_USER_ID)

    assert isinstance(result, PolicyDecision)
    assert result.decision == "approval_required"


@pytest.mark.asyncio
async def test_governor_medium_risk_requires_approval(mock_db):
    """Medium-risk plans require approval without trust engine."""
    plan = _make_mock_plan(risk_level="medium")

    result_mock = MagicMock()
    result_mock.scalar_one_or_none.return_value = plan
    mock_db.execute = AsyncMock(return_value=result_mock)

    governor = Governor(db=mock_db)
    result = await governor.evaluate_plan("plan_001", TEST_USER_ID)

    assert isinstance(result, PolicyDecision)
    assert result.decision == "approval_required"


@pytest.mark.asyncio
async def test_governor_high_risk_requires_approval(mock_db):
    """High-risk plans always require approval regardless of decision."""
    plan = _make_mock_plan(decision="acknowledge", risk_level="high")

    result_mock = MagicMock()
    result_mock.scalar_one_or_none.return_value = plan
    mock_db.execute = AsyncMock(return_value=result_mock)

    governor = Governor(db=mock_db)
    result = await governor.evaluate_plan("plan_001", TEST_USER_ID)

    assert isinstance(result, PolicyDecision)
    assert result.decision == "approval_required"
    assert result.risk_level == "high"


@pytest.mark.asyncio
async def test_governor_plan_not_found_returns_blocked(mock_db):
    """Missing plan should return blocked PolicyDecision."""
    result_mock = MagicMock()
    result_mock.scalar_one_or_none.return_value = None
    mock_db.execute = AsyncMock(return_value=result_mock)

    governor = Governor(db=mock_db)
    result = await governor.evaluate_plan("plan_missing", TEST_USER_ID)

    assert isinstance(result, PolicyDecision)
    assert result.decision == "blocked"
    assert "not found" in result.justification.lower()


@pytest.mark.asyncio
async def test_governor_approval_id_set_when_approval_required(mock_db):
    """Approval ID should be populated when decision is approval_required."""
    plan = _make_mock_plan(decision="draft_reply")

    result_mock = MagicMock()
    result_mock.scalar_one_or_none.return_value = plan
    mock_db.execute = AsyncMock(return_value=result_mock)

    governor = Governor(db=mock_db)
    result = await governor.evaluate_plan("plan_001", TEST_USER_ID)

    assert result.decision == "approval_required"
    assert result.approval_id is not None
    assert result.approval_id.startswith("apr_")
