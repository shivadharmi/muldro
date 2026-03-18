"""Tests for Governor time-based policy enforcement."""

from datetime import datetime, timezone
from unittest.mock import AsyncMock, MagicMock, patch

import pytest

from src.services.governor import Governor
from tests.conftest import TEST_USER_ID


@pytest.fixture
def mock_db():
    db = AsyncMock()
    db.add = MagicMock()
    db.flush = AsyncMock()
    db.commit = AsyncMock()
    return db


@pytest.fixture
def mock_settings_svc():
    svc = AsyncMock()
    svc.get_policy_mode = AsyncMock(return_value="approval_required")
    svc.get = AsyncMock(return_value=None)
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


class TestTimeBasedPolicyOverride:
    @pytest.mark.asyncio
    async def test_time_override_returns_correct_mode_during_window(
        self, mock_db, mock_settings_svc
    ):
        """Time-based override should return correct mode during matching window."""
        time_policies = [
            {"start_hour": 9, "end_hour": 17, "mode": "full_auto"},
        ]
        mock_settings_svc.get = AsyncMock(return_value=time_policies)

        gov = Governor(mock_db, settings_service=mock_settings_svc)

        # Mock current time to be 14:00 UTC (within 9-17 window)
        with patch("src.services.governor.datetime") as mock_dt:
            mock_dt.now.return_value = datetime(2026, 3, 16, 14, 0, tzinfo=timezone.utc)
            mock_dt.side_effect = lambda *args, **kwargs: datetime(*args, **kwargs)

            override = await gov._get_time_based_policy_override(TEST_USER_ID)
            assert override == "full_auto"

    @pytest.mark.asyncio
    async def test_time_override_returns_none_outside_window(self, mock_db, mock_settings_svc):
        """Time-based override should return None outside all windows."""
        time_policies = [
            {"start_hour": 9, "end_hour": 17, "mode": "full_auto"},
        ]
        mock_settings_svc.get = AsyncMock(return_value=time_policies)

        gov = Governor(mock_db, settings_service=mock_settings_svc)

        # Mock current time to be 20:00 UTC (outside 9-17 window)
        with patch("src.services.governor.datetime") as mock_dt:
            mock_dt.now.return_value = datetime(2026, 3, 16, 20, 0, tzinfo=timezone.utc)
            mock_dt.side_effect = lambda *args, **kwargs: datetime(*args, **kwargs)

            override = await gov._get_time_based_policy_override(TEST_USER_ID)
            assert override is None

    @pytest.mark.asyncio
    async def test_overnight_range_works_correctly(self, mock_db, mock_settings_svc):
        """Overnight range (start_hour > end_hour) should work correctly."""
        time_policies = [
            {"start_hour": 22, "end_hour": 6, "mode": "lockdown"},
        ]
        mock_settings_svc.get = AsyncMock(return_value=time_policies)

        gov = Governor(mock_db, settings_service=mock_settings_svc)

        # Test during late night (23:00 - should be in lockdown)
        with patch("src.services.governor.datetime") as mock_dt:
            mock_dt.now.return_value = datetime(2026, 3, 16, 23, 0, tzinfo=timezone.utc)
            mock_dt.side_effect = lambda *args, **kwargs: datetime(*args, **kwargs)

            override = await gov._get_time_based_policy_override(TEST_USER_ID)
            assert override == "lockdown"

        # Test during early morning (03:00 - should be in lockdown)
        with patch("src.services.governor.datetime") as mock_dt:
            mock_dt.now.return_value = datetime(2026, 3, 16, 3, 0, tzinfo=timezone.utc)
            mock_dt.side_effect = lambda *args, **kwargs: datetime(*args, **kwargs)

            override = await gov._get_time_based_policy_override(TEST_USER_ID)
            assert override == "lockdown"

        # Test during daytime (12:00 - should NOT be in lockdown)
        with patch("src.services.governor.datetime") as mock_dt:
            mock_dt.now.return_value = datetime(2026, 3, 16, 12, 0, tzinfo=timezone.utc)
            mock_dt.side_effect = lambda *args, **kwargs: datetime(*args, **kwargs)

            override = await gov._get_time_based_policy_override(TEST_USER_ID)
            assert override is None

    @pytest.mark.asyncio
    async def test_day_of_week_filtering(self, mock_db, mock_settings_svc):
        """Time policy with day-of-week filter should only apply on specified days."""
        time_policies = [
            # Weekdays only (Mon=0 to Fri=4)
            {"start_hour": 9, "end_hour": 17, "mode": "full_auto", "days": [0, 1, 2, 3, 4]},
        ]
        mock_settings_svc.get = AsyncMock(return_value=time_policies)

        gov = Governor(mock_db, settings_service=mock_settings_svc)

        # Test on Monday (weekday=0) at 14:00 - should match
        with patch("src.services.governor.datetime") as mock_dt:
            # March 10, 2026 is a Tuesday (weekday=1)
            mock_dt.now.return_value = datetime(2026, 3, 10, 14, 0, tzinfo=timezone.utc)
            mock_dt.side_effect = lambda *args, **kwargs: datetime(*args, **kwargs)

            override = await gov._get_time_based_policy_override(TEST_USER_ID)
            assert override == "full_auto"

        # Test on Sunday (weekday=6) at 14:00 - should NOT match
        with patch("src.services.governor.datetime") as mock_dt:
            # March 15, 2026 is a Sunday (weekday=6)
            mock_dt.now.return_value = datetime(2026, 3, 15, 14, 0, tzinfo=timezone.utc)
            mock_dt.side_effect = lambda *args, **kwargs: datetime(*args, **kwargs)

            override = await gov._get_time_based_policy_override(TEST_USER_ID)
            assert override is None

    @pytest.mark.asyncio
    async def test_no_day_filter_applies_all_days(self, mock_db, mock_settings_svc):
        """Time policy without day filter should apply to all days."""
        time_policies = [
            {"start_hour": 9, "end_hour": 17, "mode": "full_auto"},
        ]
        mock_settings_svc.get = AsyncMock(return_value=time_policies)

        gov = Governor(mock_db, settings_service=mock_settings_svc)

        # Test on Sunday at 14:00 - should match
        with patch("src.services.governor.datetime") as mock_dt:
            # March 15, 2026 is a Sunday
            mock_dt.now.return_value = datetime(2026, 3, 15, 14, 0, tzinfo=timezone.utc)
            mock_dt.side_effect = lambda *args, **kwargs: datetime(*args, **kwargs)

            override = await gov._get_time_based_policy_override(TEST_USER_ID)
            assert override == "full_auto"

    @pytest.mark.asyncio
    async def test_graceful_handling_no_settings_service(self, mock_db):
        """Should return None when settings_service is None."""
        gov = Governor(mock_db, settings_service=None)

        override = await gov._get_time_based_policy_override(TEST_USER_ID)
        assert override is None

    @pytest.mark.asyncio
    async def test_graceful_handling_settings_service_raises(self, mock_db, mock_settings_svc):
        """Should return None and log warning when settings_service raises."""
        mock_settings_svc.get = AsyncMock(side_effect=Exception("DB error"))

        gov = Governor(mock_db, settings_service=mock_settings_svc)

        override = await gov._get_time_based_policy_override(TEST_USER_ID)
        assert override is None

    @pytest.mark.asyncio
    async def test_graceful_handling_no_time_policies(self, mock_db, mock_settings_svc):
        """Should return None when time_policies is None or empty."""
        mock_settings_svc.get = AsyncMock(return_value=None)

        gov = Governor(mock_db, settings_service=mock_settings_svc)

        override = await gov._get_time_based_policy_override(TEST_USER_ID)
        assert override is None

        # Test with empty list
        mock_settings_svc.get = AsyncMock(return_value=[])
        override = await gov._get_time_based_policy_override(TEST_USER_ID)
        assert override is None

    @pytest.mark.asyncio
    async def test_invalid_policy_data_ignored(self, mock_db, mock_settings_svc):
        """Invalid policy entries should be silently ignored."""
        time_policies = [
            # Missing mode
            {"start_hour": 9, "end_hour": 17},
            # Not a dict
            "invalid",
            # Invalid mode value
            {"start_hour": 18, "end_hour": 20, "mode": "invalid_mode"},
            # Valid policy
            {"start_hour": 12, "end_hour": 13, "mode": "lockdown"},
        ]
        mock_settings_svc.get = AsyncMock(return_value=time_policies)

        gov = Governor(mock_db, settings_service=mock_settings_svc)

        # Test at 12:30 - should match the valid policy
        with patch("src.services.governor.datetime") as mock_dt:
            mock_dt.now.return_value = datetime(2026, 3, 16, 12, 30, tzinfo=timezone.utc)
            mock_dt.side_effect = lambda *args, **kwargs: datetime(*args, **kwargs)

            override = await gov._get_time_based_policy_override(TEST_USER_ID)
            assert override == "lockdown"

    @pytest.mark.asyncio
    async def test_first_matching_policy_wins(self, mock_db, mock_settings_svc):
        """When multiple policies match, the first one should be used."""
        time_policies = [
            {"start_hour": 9, "end_hour": 17, "mode": "full_auto"},
            {"start_hour": 9, "end_hour": 17, "mode": "lockdown"},  # Also matches
        ]
        mock_settings_svc.get = AsyncMock(return_value=time_policies)

        gov = Governor(mock_db, settings_service=mock_settings_svc)

        with patch("src.services.governor.datetime") as mock_dt:
            mock_dt.now.return_value = datetime(2026, 3, 16, 14, 0, tzinfo=timezone.utc)
            mock_dt.side_effect = lambda *args, **kwargs: datetime(*args, **kwargs)

            override = await gov._get_time_based_policy_override(TEST_USER_ID)
            assert override == "full_auto"  # First policy wins


class TestPolicyModeIntegration:
    @pytest.mark.asyncio
    async def test_time_override_takes_precedence_over_default(self, mock_db, mock_settings_svc):
        """Time-based override should take precedence over default policy mode."""
        time_policies = [
            {"start_hour": 9, "end_hour": 17, "mode": "full_auto"},
        ]
        mock_settings_svc.get = AsyncMock(return_value=time_policies)
        mock_settings_svc.get_policy_mode = AsyncMock(return_value="approval_required")

        gov = Governor(mock_db, settings_service=mock_settings_svc)
        plan = _make_plan(decision="send_email", risk="low")

        # During work hours (14:00) - should use time override (full_auto)
        with patch("src.services.governor.datetime") as mock_dt:
            mock_dt.now.return_value = datetime(2026, 3, 16, 14, 0, tzinfo=timezone.utc)
            mock_dt.side_effect = lambda *args, **kwargs: datetime(*args, **kwargs)

            result = await gov._apply_policy(plan, TEST_USER_ID)
            assert result == "auto_execute"

    @pytest.mark.asyncio
    async def test_falls_back_to_default_when_no_time_override(self, mock_db, mock_settings_svc):
        """Should fall back to default policy mode when no time override matches."""
        time_policies = [
            {"start_hour": 9, "end_hour": 17, "mode": "full_auto"},
        ]
        mock_settings_svc.get = AsyncMock(return_value=time_policies)
        mock_settings_svc.get_policy_mode = AsyncMock(return_value="approval_required")

        gov = Governor(mock_db, settings_service=mock_settings_svc)
        plan = _make_plan(decision="send_email", risk="low")

        # Outside work hours (20:00) - should use default (approval_required)
        with patch("src.services.governor.datetime") as mock_dt:
            mock_dt.now.return_value = datetime(2026, 3, 16, 20, 0, tzinfo=timezone.utc)
            mock_dt.side_effect = lambda *args, **kwargs: datetime(*args, **kwargs)

            result = await gov._apply_policy(plan, TEST_USER_ID)
            assert result == "approval_required"
