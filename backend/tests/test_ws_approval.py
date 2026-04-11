"""Tests for WebSocket approval action handlers."""

from unittest.mock import AsyncMock, MagicMock, patch

import pytest


class TestHandleApprove:
    """WebSocket approve handler extracts approval_id correctly."""

    @pytest.mark.asyncio
    async def test_approve_extracts_approval_id_from_payload(self):
        from src.api.routes_ws import _handle_approve

        mock_app = MagicMock()
        payload = {"approval_id": "apr_01TEST000000000000000000"}

        with patch(
            "src.api.routes_ws._process_approval_ws", new_callable=AsyncMock
        ) as mock_process:
            mock_process.return_value = {"status": "success"}
            await _handle_approve("usr_01TEST", payload, mock_app)
            mock_process.assert_called_once_with(
                "usr_01TEST", "apr_01TEST000000000000000000", "approve", mock_app
            )

    @pytest.mark.asyncio
    async def test_approve_empty_payload_passes_empty_string(self):
        from src.api.routes_ws import _handle_approve

        mock_app = MagicMock()

        with patch(
            "src.api.routes_ws._process_approval_ws", new_callable=AsyncMock
        ) as mock_process:
            mock_process.return_value = {"status": "error"}
            await _handle_approve("usr_01TEST", {}, mock_app)
            mock_process.assert_called_once_with("usr_01TEST", "", "approve", mock_app)


class TestHandleReject:
    @pytest.mark.asyncio
    async def test_reject_extracts_approval_id_from_payload(self):
        from src.api.routes_ws import _handle_reject

        mock_app = MagicMock()
        payload = {"approval_id": "apr_01TEST000000000000000000"}

        with patch(
            "src.api.routes_ws._process_approval_ws", new_callable=AsyncMock
        ) as mock_process:
            mock_process.return_value = {"status": "success"}
            await _handle_reject("usr_01TEST", payload, mock_app)
            mock_process.assert_called_once_with(
                "usr_01TEST", "apr_01TEST000000000000000000", "reject", mock_app
            )


class TestHandleEditBeforeApprove:
    @pytest.mark.asyncio
    async def test_edit_before_approve_in_action_handlers(self):
        from src.api.routes_ws import ACTION_HANDLERS

        assert "edit_before_approve" in ACTION_HANDLERS

    @pytest.mark.asyncio
    async def test_edit_before_approve_delegates_to_process_fn(self):
        from src.api.routes_ws import _handle_edit_before_approve

        mock_app = MagicMock()
        payload = {"approval_id": "apr_01TEST", "title": "Updated title"}

        with patch(
            "src.api.routes_ws._process_edit_approval_ws", new_callable=AsyncMock
        ) as mock_edit:
            mock_edit.return_value = {"status": "success"}
            result = await _handle_edit_before_approve("usr_01TEST", payload, mock_app)
            mock_edit.assert_called_once_with("usr_01TEST", payload, mock_app)
            assert result["status"] == "success"


class TestApprovalHardening:
    """Approval endpoint hardening: expiry check, step locking, idempotency."""

    def test_get_approval_function_has_intended_action_param(self):
        import inspect

        from src.api.routes_approvals import _get_approval

        sig = inspect.signature(_get_approval)
        assert "intended_action" in sig.parameters

    def test_intended_action_defaults_to_approve(self):
        import inspect

        from src.api.routes_approvals import _get_approval

        sig = inspect.signature(_get_approval)
        assert sig.parameters["intended_action"].default == "approve"

    @pytest.mark.asyncio
    async def test_expired_approval_raises_410(self):
        """An expired pending approval should raise HTTP 410 Gone."""
        from datetime import datetime, timezone
        from unittest.mock import AsyncMock, MagicMock

        from fastapi import HTTPException

        from src.api.routes_approvals import _get_approval

        past = datetime(2020, 1, 1, tzinfo=timezone.utc)
        mock_approval = MagicMock()
        mock_approval.status = "pending"
        mock_approval.expires_at = past

        mock_result = MagicMock()
        mock_result.scalar_one_or_none.return_value = mock_approval

        mock_db = AsyncMock()
        mock_db.execute = AsyncMock(return_value=mock_result)
        mock_db.flush = AsyncMock()

        with pytest.raises(HTTPException) as exc_info:
            await _get_approval(mock_db, "apr_01TEST", "usr_01TEST", "ws_01TEST")

        assert exc_info.value.status_code == 410
        assert "expired" in exc_info.value.detail.lower()
        assert mock_approval.status == "expired"

    @pytest.mark.asyncio
    async def test_already_approved_returns_idempotently(self):
        """Calling approve on an already-approved approval returns it without 400."""
        from unittest.mock import AsyncMock, MagicMock

        from src.api.routes_approvals import _get_approval

        mock_approval = MagicMock()
        mock_approval.status = "approved"
        mock_approval.expires_at = None

        mock_result = MagicMock()
        mock_result.scalar_one_or_none.return_value = mock_approval

        mock_db = AsyncMock()
        mock_db.execute = AsyncMock(return_value=mock_result)

        result = await _get_approval(
            mock_db, "apr_01TEST", "usr_01TEST", "ws_01TEST", intended_action="approve"
        )
        assert result is mock_approval

    @pytest.mark.asyncio
    async def test_already_rejected_when_approving_raises_400(self):
        """Calling approve on a rejected approval still raises 400 (conflicting state)."""
        from unittest.mock import AsyncMock, MagicMock

        from fastapi import HTTPException

        from src.api.routes_approvals import _get_approval

        mock_approval = MagicMock()
        mock_approval.status = "rejected"
        mock_approval.expires_at = None

        mock_result = MagicMock()
        mock_result.scalar_one_or_none.return_value = mock_approval

        mock_db = AsyncMock()
        mock_db.execute = AsyncMock(return_value=mock_result)

        with pytest.raises(HTTPException) as exc_info:
            await _get_approval(
                mock_db, "apr_01TEST", "usr_01TEST", "ws_01TEST", intended_action="approve"
            )
        assert exc_info.value.status_code == 400

    @pytest.mark.asyncio
    async def test_already_rejected_returns_idempotently(self):
        """Calling reject on an already-rejected approval returns it without 400."""
        from unittest.mock import AsyncMock, MagicMock

        from src.api.routes_approvals import _get_approval

        mock_approval = MagicMock()
        mock_approval.status = "rejected"
        mock_approval.expires_at = None

        mock_result = MagicMock()
        mock_result.scalar_one_or_none.return_value = mock_approval

        mock_db = AsyncMock()
        mock_db.execute = AsyncMock(return_value=mock_result)

        result = await _get_approval(
            mock_db, "apr_01TEST", "usr_01TEST", "ws_01TEST", intended_action="reject"
        )
        assert result is mock_approval
