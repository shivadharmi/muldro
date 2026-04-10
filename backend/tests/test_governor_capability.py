"""Tests for governor capability-based policy evaluation."""

from unittest.mock import AsyncMock, MagicMock

import pytest

from src.services.governor import Governor


def _make_plan(risk_level: str = "low"):
    plan = MagicMock()
    plan.plan_id = "plan_test"
    plan.goal = "Test plan"
    plan.risk_level = risk_level
    plan.reasoning_summary = "Test"
    plan.tasks = []
    return plan


class TestCapabilityBasedPolicy:
    @pytest.mark.asyncio
    async def test_low_risk_full_auto_executes(self):
        db = AsyncMock()
        gov = Governor(db=db)
        gov._get_policy_mode = AsyncMock(return_value="full_auto")
        result = await gov._apply_policy(_make_plan(risk_level="low"), "usr_1")
        assert result == "auto_execute"

    @pytest.mark.asyncio
    async def test_high_risk_requires_approval(self):
        db = AsyncMock()
        gov = Governor(db=db)
        gov._get_policy_mode = AsyncMock(return_value="full_auto")
        result = await gov._apply_policy(_make_plan(risk_level="high"), "usr_1")
        assert result == "approval_required"

    @pytest.mark.asyncio
    async def test_critical_risk_requires_approval(self):
        db = AsyncMock()
        gov = Governor(db=db)
        gov._get_policy_mode = AsyncMock(return_value="full_auto")
        result = await gov._apply_policy(_make_plan(risk_level="critical"), "usr_1")
        assert result == "approval_required"

    @pytest.mark.asyncio
    async def test_lockdown_blocks(self):
        db = AsyncMock()
        gov = Governor(db=db)
        gov._get_policy_mode = AsyncMock(return_value="lockdown")
        result = await gov._apply_policy(_make_plan(risk_level="low"), "usr_1")
        assert result == "blocked"

    @pytest.mark.asyncio
    async def test_suggest_only_blocks(self):
        db = AsyncMock()
        gov = Governor(db=db)
        gov._get_policy_mode = AsyncMock(return_value="suggest_only")
        result = await gov._apply_policy(_make_plan(risk_level="low"), "usr_1")
        assert result == "blocked"

    @pytest.mark.asyncio
    async def test_default_mode_requires_approval(self):
        db = AsyncMock()
        gov = Governor(db=db)
        gov._get_policy_mode = AsyncMock(return_value="approval_required")
        gov._check_trust = AsyncMock(return_value=False)
        result = await gov._apply_policy(_make_plan(risk_level="low"), "usr_1")
        assert result == "approval_required"

    @pytest.mark.asyncio
    async def test_default_mode_medium_risk_trust_auto(self):
        db = AsyncMock()
        gov = Governor(db=db)
        gov._get_policy_mode = AsyncMock(return_value="approval_required")
        gov._check_trust = AsyncMock(return_value=True)
        result = await gov._apply_policy(_make_plan(risk_level="medium"), "usr_1")
        assert result == "auto_execute"

    @pytest.mark.asyncio
    async def test_none_risk_treated_as_low(self):
        db = AsyncMock()
        gov = Governor(db=db)
        gov._get_policy_mode = AsyncMock(return_value="full_auto")
        result = await gov._apply_policy(_make_plan(risk_level="none"), "usr_1")
        assert result == "auto_execute"
