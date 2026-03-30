"""Tests for Governor v2 — policy modes + trust engine integration."""

from unittest.mock import AsyncMock, MagicMock

import pytest

from src.services.governor import (
    AUTO_EXECUTE_DECISIONS,
    BLOCKED_ACTIONS,
    Governor,
)
from tests.conftest import TEST_USER_ID


@pytest.fixture
def mock_db():
    db = AsyncMock()
    db.add = MagicMock()
    db.flush = AsyncMock()
    db.commit = AsyncMock()
    return db


@pytest.fixture
def mock_trust():
    trust = AsyncMock()
    trust.should_auto_approve = AsyncMock(return_value=False)
    return trust


@pytest.fixture
def mock_settings_svc():
    svc = AsyncMock()
    svc.get_policy_mode = AsyncMock(return_value="approval_required")
    return svc


def _make_plan(decision="send_email", risk="low"):
    plan = MagicMock()
    plan.plan_id = "plan_001"
    plan.goal = "Test"
    plan.decision = decision
    plan.risk_level = risk
    plan.reasoning_summary = "Test reasoning"
    plan.execution_mode = None
    plan.status = None
    plan.tasks = []
    return plan


class TestApplyPolicyModes:
    async def test_lockdown_blocks_everything(self, mock_db, mock_trust, mock_settings_svc):
        mock_settings_svc.get_policy_mode.return_value = "lockdown"
        gov = Governor(mock_db, trust_engine=mock_trust, settings_service=mock_settings_svc)
        plan = _make_plan(decision="fetch_info", risk="low")

        result = await gov._apply_policy(plan, TEST_USER_ID)
        assert result == "blocked"

    async def test_suggest_only_blocks(self, mock_db, mock_trust, mock_settings_svc):
        mock_settings_svc.get_policy_mode.return_value = "suggest_only"
        gov = Governor(mock_db, trust_engine=mock_trust, settings_service=mock_settings_svc)
        plan = _make_plan(decision="summarize", risk="low")

        result = await gov._apply_policy(plan, TEST_USER_ID)
        assert result == "blocked"

    async def test_full_auto_executes_low_risk(self, mock_db, mock_trust, mock_settings_svc):
        mock_settings_svc.get_policy_mode.return_value = "full_auto"
        gov = Governor(mock_db, trust_engine=mock_trust, settings_service=mock_settings_svc)
        plan = _make_plan(decision="send_email", risk="low")

        result = await gov._apply_policy(plan, TEST_USER_ID)
        assert result == "auto_execute"

    async def test_full_auto_requires_approval_high_risk(
        self, mock_db, mock_trust, mock_settings_svc
    ):
        mock_settings_svc.get_policy_mode.return_value = "full_auto"
        gov = Governor(mock_db, trust_engine=mock_trust, settings_service=mock_settings_svc)
        plan = _make_plan(decision="send_email", risk="high")

        result = await gov._apply_policy(plan, TEST_USER_ID)
        assert result == "approval_required"

    async def test_full_auto_still_blocks_dangerous(self, mock_db, mock_trust, mock_settings_svc):
        mock_settings_svc.get_policy_mode.return_value = "full_auto"
        gov = Governor(mock_db, trust_engine=mock_trust, settings_service=mock_settings_svc)
        plan = _make_plan(decision="delete_data", risk="low")

        result = await gov._apply_policy(plan, TEST_USER_ID)
        assert result == "blocked"


class TestTrustIntegration:
    async def test_trust_auto_approves(self, mock_db, mock_trust, mock_settings_svc):
        mock_trust.should_auto_approve.return_value = True
        gov = Governor(mock_db, trust_engine=mock_trust, settings_service=mock_settings_svc)
        plan = _make_plan(decision="send_email", risk="low")

        result = await gov._apply_policy(plan, TEST_USER_ID)
        assert result == "auto_execute"
        mock_trust.should_auto_approve.assert_called_once_with(TEST_USER_ID, "send_email", "low")

    async def test_no_trust_requires_approval(self, mock_db, mock_trust, mock_settings_svc):
        mock_trust.should_auto_approve.return_value = False
        gov = Governor(mock_db, trust_engine=mock_trust, settings_service=mock_settings_svc)
        plan = _make_plan(decision="send_email", risk="low")

        result = await gov._apply_policy(plan, TEST_USER_ID)
        assert result == "approval_required"

    async def test_no_trust_engine_defaults_to_approval(self, mock_db, mock_settings_svc):
        gov = Governor(mock_db, trust_engine=None, settings_service=mock_settings_svc)
        plan = _make_plan(decision="send_email", risk="low")

        result = await gov._apply_policy(plan, TEST_USER_ID)
        assert result == "approval_required"


class TestDefaultPolicyRules:
    async def test_auto_execute_actions(self, mock_db, mock_settings_svc):
        gov = Governor(mock_db, settings_service=mock_settings_svc)
        for action in AUTO_EXECUTE_DECISIONS:
            plan = _make_plan(decision=action, risk="low")
            result = await gov._apply_policy(plan, TEST_USER_ID)
            assert result == "auto_execute", f"Expected auto_execute for {action}"

    async def test_blocked_actions(self, mock_db, mock_settings_svc):
        gov = Governor(mock_db, settings_service=mock_settings_svc)
        for action in BLOCKED_ACTIONS:
            plan = _make_plan(decision=action, risk="low")
            result = await gov._apply_policy(plan, TEST_USER_ID)
            assert result == "blocked", f"Expected blocked for {action}"

    async def test_high_risk_always_needs_approval(self, mock_db, mock_settings_svc):
        gov = Governor(mock_db, settings_service=mock_settings_svc)
        plan = _make_plan(decision="summarize", risk="high")
        result = await gov._apply_policy(plan, TEST_USER_ID)
        assert result == "approval_required"


class TestFallbackBehavior:
    async def test_no_settings_service_uses_default(self, mock_db):
        gov = Governor(mock_db)
        plan = _make_plan(decision="send_email", risk="low")
        result = await gov._apply_policy(plan, TEST_USER_ID)
        assert result == "approval_required"

    async def test_settings_service_error_falls_back(self, mock_db, mock_settings_svc):
        mock_settings_svc.get_policy_mode.side_effect = Exception("DB error")
        gov = Governor(mock_db, settings_service=mock_settings_svc)
        plan = _make_plan(decision="send_email", risk="low")
        result = await gov._apply_policy(plan, TEST_USER_ID)
        assert result == "approval_required"
