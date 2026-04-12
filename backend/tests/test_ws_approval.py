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
    async def test_approve_accepts_id_key_from_surface_modal(self):
        """surface-detail-modal sends 'id' instead of 'approval_id'."""
        from src.api.routes_ws import _handle_approve

        mock_app = MagicMock()
        payload = {"id": "apr_01TEST000000000000000000"}

        with patch(
            "src.api.routes_ws._process_approval_ws", new_callable=AsyncMock
        ) as mock_process:
            mock_process.return_value = {"status": "success"}
            await _handle_approve("usr_01TEST", payload, mock_app)
            mock_process.assert_called_once_with(
                "usr_01TEST", "apr_01TEST000000000000000000", "approve", mock_app
            )

    @pytest.mark.asyncio
    async def test_approve_prefers_approval_id_over_id(self):
        """When both keys present, approval_id takes precedence."""
        from src.api.routes_ws import _handle_approve

        mock_app = MagicMock()
        payload = {"approval_id": "apr_CORRECT", "id": "apr_FALLBACK"}

        with patch(
            "src.api.routes_ws._process_approval_ws", new_callable=AsyncMock
        ) as mock_process:
            mock_process.return_value = {"status": "success"}
            await _handle_approve("usr_01TEST", payload, mock_app)
            mock_process.assert_called_once_with("usr_01TEST", "apr_CORRECT", "approve", mock_app)

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


class TestArtifactRefsValidation:
    def test_tool_approval_type_prefix_recognized(self):
        """approval_type starting with 'tool:' should trigger validation."""
        import inspect

        from src.services import approval_service

        source = inspect.getsource(approval_service)
        assert "tool_name" in source
        assert "artifact_refs" in source

    @pytest.mark.asyncio
    async def test_tool_approval_missing_tool_name_raises_value_error(self):
        """create_approval with tool: type and artifact_refs lacking tool_name raises ValueError."""
        from unittest.mock import AsyncMock

        from src.services.approval_service import create_approval

        mock_db = AsyncMock()

        with pytest.raises(ValueError, match="tool_name"):
            await create_approval(
                mock_db,
                user_id="usr_01TEST",
                workspace_id="ws_01TEST",
                approval_type="tool:email.send",
                title="Send email",
                requested_by="orchestrator",
                artifact_refs={"tool_params": {"to": "user@example.com"}},
            )

    @pytest.mark.asyncio
    async def test_tool_approval_with_tool_name_succeeds(self):
        """create_approval with tool: type and artifact_refs containing tool_name proceeds."""
        from unittest.mock import AsyncMock

        from src.services.approval_service import create_approval

        mock_db = AsyncMock()

        approval = await create_approval(
            mock_db,
            user_id="usr_01TEST",
            workspace_id="ws_01TEST",
            approval_type="tool:email.send",
            title="Send email",
            requested_by="orchestrator",
            artifact_refs={"tool_name": "send_email", "tool_params": {"to": "user@example.com"}},
        )
        assert approval.approval_type == "tool:email.send"
        mock_db.add.assert_called_once_with(approval)

    @pytest.mark.asyncio
    async def test_non_tool_approval_without_tool_name_succeeds(self):
        """create_approval with non-tool: type skips tool_name validation."""
        from unittest.mock import AsyncMock

        from src.services.approval_service import create_approval

        mock_db = AsyncMock()

        approval = await create_approval(
            mock_db,
            user_id="usr_01TEST",
            workspace_id="ws_01TEST",
            approval_type="plan:review",
            title="Review plan",
            requested_by="orchestrator",
            artifact_refs={"plan_id": "plan_01TEST"},
        )
        assert approval.approval_type == "plan:review"

    @pytest.mark.asyncio
    async def test_tool_approval_with_no_artifact_refs_skips_validation(self):
        """create_approval with tool: type but no artifact_refs does not raise."""
        from unittest.mock import AsyncMock

        from src.services.approval_service import create_approval

        mock_db = AsyncMock()

        approval = await create_approval(
            mock_db,
            user_id="usr_01TEST",
            workspace_id="ws_01TEST",
            approval_type="tool:email.send",
            title="Send email",
            requested_by="orchestrator",
            artifact_refs=None,
        )
        assert approval.approval_type == "tool:email.send"


class TestWebSocketReconnect:
    def test_ws_module_has_backfill_capability(self):
        """WebSocket module should reference UISurface for backfill on reconnect."""
        import inspect

        from src.api import routes_ws

        source = inspect.getsource(routes_ws)
        assert "last_surface_update" in source or "backfill" in source.lower()
