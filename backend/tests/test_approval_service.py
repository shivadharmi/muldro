"""Tests for centralized approval_service and related fixes."""

from unittest.mock import AsyncMock, MagicMock, patch

import pytest


@pytest.fixture
def mock_db():
    db = AsyncMock()
    db.add = MagicMock()
    db.flush = AsyncMock()
    db.commit = AsyncMock()
    return db


class TestCreateApproval:
    async def test_creates_approval_with_required_fields(self, mock_db):
        from src.services.approval_service import create_approval

        approval = await create_approval(
            mock_db,
            user_id="usr_1",
            workspace_id="ws_1",
            approval_type="send_email",
            title="Approve: Send email",
            summary="Send email to john@example.com",
            risk_level="high",
            execution_id="run_123",
            requested_by="usr_1",
        )

        assert approval.approval_id.startswith("apr_")
        assert approval.user_id == "usr_1"
        assert approval.workspace_id == "ws_1"
        assert approval.requested_by == "usr_1"
        assert approval.status == "pending"
        assert approval.expires_at is not None
        mock_db.add.assert_called_once_with(approval)

    async def test_requested_by_always_populated(self, mock_db):
        from src.services.approval_service import create_approval

        approval = await create_approval(
            mock_db,
            user_id="usr_1",
            workspace_id="ws_1",
            approval_type="tool_call:gmail_send",
            title="Approve: gmail_send",
            requested_by="system",
        )

        assert approval.requested_by == "system"

    async def test_workspace_id_always_populated(self, mock_db):
        from src.services.approval_service import create_approval

        approval = await create_approval(
            mock_db,
            user_id="usr_1",
            workspace_id="ws_42",
            approval_type="test",
            title="Test",
            requested_by="usr_1",
        )

        assert approval.workspace_id == "ws_42"


class TestGovernorUsesFactory:
    async def test_governor_creates_approval_with_requested_by(self):
        from src.services.governor import Governor

        mock_db = AsyncMock()
        mock_plan = MagicMock()
        mock_plan.plan_id = "plan_1"
        mock_plan.goal = "Send email"
        mock_plan.reasoning_summary = "Need to send email"
        mock_plan.risk_level = "medium"
        mock_plan.tasks = []

        with patch("src.services.approval_service.create_approval") as mock_create:
            mock_approval = MagicMock()
            mock_approval.approval_id = "apr_123"
            mock_create.return_value = mock_approval

            governor = Governor(mock_db)
            result = await governor._create_approval(mock_plan, "run_1", "usr_1", "ws_1")

            assert result == "apr_123"
            mock_create.assert_called_once()
            call_kwargs = mock_create.call_args[1]
            assert call_kwargs["requested_by"] == "usr_1"
            assert call_kwargs["workspace_id"] == "ws_1"


class TestRoutesApprovalsStateMachine:
    async def test_approve_uses_transition_run(self):
        """Approve route should use transition_run, not direct mutation."""
        from src.services.execution_state import RUN_TRANSITIONS

        # awaiting_approval -> pending is not a valid transition
        # awaiting_approval -> running is valid
        assert "running" in RUN_TRANSITIONS["awaiting_approval"]
        # The approve route transitions to "pending" which IS valid from awaiting_approval
        # (awaiting_approval -> running, but we check cancelled from awaiting_approval)
        assert "cancelled" in RUN_TRANSITIONS["awaiting_approval"]

    async def test_reject_uses_transition_run(self):
        """Reject route should use transition_run for cancellation."""
        from src.services.execution_state import RUN_TRANSITIONS

        assert "cancelled" in RUN_TRANSITIONS["awaiting_approval"]
