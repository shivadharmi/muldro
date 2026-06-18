"""Tests for trust feedback loop — record_approval_decision integration."""

from unittest.mock import AsyncMock, MagicMock

import pytest

from src.services.risk_assessor import record_approval_decision


@pytest.fixture
def mock_db():
    db = AsyncMock()
    db.add = MagicMock()
    db.flush = AsyncMock()
    return db


class TestRecordApprovalDecision:
    async def test_approved_increments_count(self, mock_db):
        state = MagicMock()
        state.approved_count = 0
        state.rejected_count = 0
        state.modified_count = 0
        state.trust_level = "first_use"
        state.cooldown_until = None

        result_mock = MagicMock()
        result_mock.scalar_one_or_none.return_value = state
        mock_db.execute = AsyncMock(return_value=result_mock)

        await record_approval_decision(mock_db, "ws_test", "email.send", "low", "approved")
        assert state.approved_count == 1
        assert state.last_decision_at is not None

    async def test_rejected_applies_demotion(self, mock_db):
        state = MagicMock()
        state.approved_count = 10
        state.rejected_count = 0
        state.modified_count = 0
        state.trust_level = "trusted"
        state.cooldown_until = None

        result_mock = MagicMock()
        result_mock.scalar_one_or_none.return_value = state
        mock_db.execute = AsyncMock(return_value=result_mock)

        await record_approval_decision(mock_db, "ws_test", "email.send", "low", "rejected")
        assert state.rejected_count == 1
        assert state.trust_level == "learning"
        assert state.cooldown_until is not None

    async def test_modified_increments_both(self, mock_db):
        state = MagicMock()
        state.approved_count = 5
        state.rejected_count = 0
        state.modified_count = 0
        state.trust_level = "learning"
        state.cooldown_until = None

        result_mock = MagicMock()
        result_mock.scalar_one_or_none.return_value = state
        mock_db.execute = AsyncMock(return_value=result_mock)

        await record_approval_decision(mock_db, "ws_test", "email.send", "low", "modified")
        assert state.modified_count == 1
        assert state.approved_count == 6

    async def test_graduation_after_three_approvals(self, mock_db):
        state = MagicMock()
        state.approved_count = 2
        state.rejected_count = 0
        state.modified_count = 0
        state.trust_level = "first_use"
        state.cooldown_until = None

        result_mock = MagicMock()
        result_mock.scalar_one_or_none.return_value = state
        mock_db.execute = AsyncMock(return_value=result_mock)

        await record_approval_decision(mock_db, "ws_test", "email.send", "low", "approved")
        assert state.trust_level == "learning"


class TestAutoExecutionTrustFeedback:
    """Successful auto-executed steps must reinforce trust (graduate), not only
    explicit user approvals — closes the autonomous outcome→trust loop."""

    async def test_auto_execution_success_records_approved(self):
        from unittest.mock import AsyncMock, MagicMock, patch

        from src.services.graph_executor import GraphExecutor

        executor = GraphExecutor(MagicMock(), AsyncMock())
        with patch(
            "src.services.risk_assessor.record_approval_decision", new=AsyncMock()
        ) as rec:
            await executor._record_auto_execution_outcome("email.send", "low", "ws_test")

        rec.assert_awaited_once()
        args = rec.await_args.args
        assert args[2] == "email.send"  # capability
        assert args[3] == "low"  # risk_level
        assert args[4] == "approved"  # decision

    async def test_empty_capability_skips(self):
        from unittest.mock import AsyncMock, MagicMock, patch

        from src.services.graph_executor import GraphExecutor

        executor = GraphExecutor(MagicMock(), AsyncMock())
        with patch(
            "src.services.risk_assessor.record_approval_decision", new=AsyncMock()
        ) as rec:
            await executor._record_auto_execution_outcome("", "low", "ws_test")

        rec.assert_not_awaited()
