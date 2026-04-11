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
