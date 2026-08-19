"""Tests for WebSocket approval action handlers."""

import json
from unittest.mock import AsyncMock, MagicMock, create_autospec, patch

import pytest

from src.orchestrator.muldro import MuldroOrchestrator


class TestHandleApprove:
    """WebSocket approve handler reads 'id' key from payload."""

    @pytest.mark.asyncio
    async def test_approve_extracts_id_from_payload(self):
        from src.api.routes_ws import _handle_approve

        mock_app = MagicMock()
        payload = {"id": "apr_01TEST000000000000000000"}

        with patch(
            "src.api.routes_ws._process_approval_ws", new_callable=AsyncMock
        ) as mock_process:
            mock_process.return_value = {"status": "success"}
            await _handle_approve("usr_01TEST", payload, mock_app)
            mock_process.assert_called_once_with(
                "usr_01TEST", "apr_01TEST000000000000000000", "approve", mock_app, ""
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
            mock_process.assert_called_once_with("usr_01TEST", "", "approve", mock_app, "")


class TestHandleReject:
    @pytest.mark.asyncio
    async def test_reject_extracts_id_from_payload(self):
        from src.api.routes_ws import _handle_reject

        mock_app = MagicMock()
        payload = {"id": "apr_01TEST000000000000000000"}

        with patch(
            "src.api.routes_ws._process_approval_ws", new_callable=AsyncMock
        ) as mock_process:
            mock_process.return_value = {"status": "success"}
            await _handle_reject("usr_01TEST", payload, mock_app)
            mock_process.assert_called_once_with(
                "usr_01TEST", "apr_01TEST000000000000000000", "reject", mock_app, ""
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
            mock_edit.assert_called_once_with("usr_01TEST", payload, mock_app, "")
            assert result["status"] == "success"


# A secret-looking internal string that must never reach a WS client frame.
WS_SECRET = "redis://:s3cr3t@cache.internal:6379/0"


class TestWsErrorFramesAreSanitized:
    """WS handlers must never return a raw exception string to the client.

    They use safe_error_event(...) which yields the WS-shaped envelope
    {status,code,message,correlation_id} and reuses the per-connection cid.
    """

    @pytest.mark.asyncio
    async def test_orchestrator_action_error_is_sanitized(self):
        from src.api.routes_ws import _handle_orchestrator_action

        mock_app = MagicMock()
        # Orchestrator present but process_message raises a leaky exception. autospec so the
        # handler's real call signature is still enforced — otherwise a signature break here
        # would masquerade as the very sanitization this test is asserting.
        orch = create_autospec(MuldroOrchestrator, instance=True)
        orch.process_message.side_effect = ValueError(f"boom {WS_SECRET}")
        mock_app.state.orchestrator = orch

        with patch("src.api.deps.resolve_workspace_id", new_callable=AsyncMock) as rw:
            rw.return_value = "ws_01TEST"
            with patch("src.models.database.get_session_factory") as gsf:
                gsf.return_value.return_value.__aenter__ = AsyncMock(return_value=MagicMock())
                gsf.return_value.return_value.__aexit__ = AsyncMock(return_value=False)
                result = await _handle_orchestrator_action(
                    "usr_01TEST", "do_thing", {}, mock_app, "ws_fixedcid"
                )

        assert result["status"] == "error"
        assert result["code"] == "internal_error"
        assert result["message"] == "Something went wrong. Please try again."
        assert result["correlation_id"] == "ws_fixedcid"
        # No leak of the raw exception text anywhere in the frame.
        assert WS_SECRET not in str(result)
        assert "boom" not in str(result)

    @pytest.mark.asyncio
    async def test_dispatch_catch_all_error_is_sanitized(self):
        """If a handler itself raises, the client frame is still sanitized."""
        from src.api.routes_ws import _handle_client_message

        raw = json.dumps({"type": "action", "payload": {"action": "approve"}})

        captured: list = []

        async def fake_broadcast(user_id, message):
            captured.append(message)

        with (
            patch(
                "src.api.routes_ws._dispatch_action",
                new_callable=AsyncMock,
                side_effect=RuntimeError(f"kaboom {WS_SECRET}"),
            ),
            patch("src.api.routes_ws._broadcast", side_effect=fake_broadcast),
        ):
            await _handle_client_message("usr_01TEST", raw, MagicMock(), "ws_conn123")

        assert captured, "expected an action_result broadcast"
        msg = captured[-1]
        assert msg["status"] == "error"
        assert msg["code"] == "internal_error"
        # The action_result frame carries the safe message under "error".
        assert msg["error"] == "Something went wrong. Please try again."
        assert msg["correlation_id"] == "ws_conn123"
        assert WS_SECRET not in str(msg)
        assert "kaboom" not in str(msg)

    def test_handlers_accept_cid_argument(self):
        """All registered action handlers + dispatch accept a cid parameter so
        the per-connection correlation id can be threaded through."""
        import inspect

        from src.api.routes_ws import (
            ACTION_HANDLERS,
            _dispatch_action,
            _handle_client_message,
            _handle_orchestrator_action,
        )

        for name, handler in ACTION_HANDLERS.items():
            assert "cid" in inspect.signature(handler).parameters, name
        assert "cid" in inspect.signature(_dispatch_action).parameters
        assert "cid" in inspect.signature(_handle_orchestrator_action).parameters
        assert "cid" in inspect.signature(_handle_client_message).parameters


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

    def test_execution_surface_replays_as_surface_update(self):
        from src.api.routes_ws import _backfill_message_for_surface

        s = MagicMock(
            surface_type="execution",
            payload={"last_surface_update": {"surface_id": "x", "phase": "executing"}},
        )
        msg = _backfill_message_for_surface(s)
        assert msg == {"type": "surface_update", "surface_id": "x", "phase": "executing"}

    def test_insight_surface_replays_as_surface_push(self):
        """proactive_insight surfaces must replay in the live push format so the
        client renders insights missed while offline (previously dropped)."""
        from src.api.routes_ws import _backfill_message_for_surface

        payload = {"surface_id": "surf_1", "kind": "proactive_insight"}
        s = MagicMock(surface_type="proactive_insight", payload=payload)
        msg = _backfill_message_for_surface(s)
        assert msg == {"type": "surface", "surface": payload}

    def test_empty_payload_returns_none(self):
        from src.api.routes_ws import _backfill_message_for_surface

        s = MagicMock(surface_type="proactive_insight", payload=None)
        assert _backfill_message_for_surface(s) is None

    def test_execution_surface_without_last_update_returns_none(self):
        from src.api.routes_ws import _backfill_message_for_surface

        s = MagicMock(surface_type="execution", payload={})
        assert _backfill_message_for_surface(s) is None
