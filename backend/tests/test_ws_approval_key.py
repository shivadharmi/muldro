"""Tests for WebSocket approval handler payload key resolution."""

from unittest.mock import AsyncMock, patch

import pytest


@pytest.mark.asyncio
@patch("src.api.routes_ws._process_approval_ws", new_callable=AsyncMock)
async def test_handle_approve_uses_approval_id_key(mock_process):
    """payload with 'approval_id' key should pass that value through."""
    from src.api.routes_ws import _handle_approve

    mock_process.return_value = {"status": "ok"}
    app = object()

    await _handle_approve("user_1", {"approval_id": "apr_001"}, app)

    mock_process.assert_awaited_once_with("user_1", "apr_001", "approve", app)


@pytest.mark.asyncio
@patch("src.api.routes_ws._process_approval_ws", new_callable=AsyncMock)
async def test_handle_approve_falls_back_to_id_key(mock_process):
    """payload without 'approval_id' should fall back to 'id' key."""
    from src.api.routes_ws import _handle_approve

    mock_process.return_value = {"status": "ok"}
    app = object()

    await _handle_approve("user_1", {"id": "apr_002"}, app)

    mock_process.assert_awaited_once_with("user_1", "apr_002", "approve", app)


@pytest.mark.asyncio
@patch("src.api.routes_ws._process_approval_ws", new_callable=AsyncMock)
async def test_handle_reject_uses_approval_id_key(mock_process):
    """payload with 'approval_id' key should pass that value through for reject."""
    from src.api.routes_ws import _handle_reject

    mock_process.return_value = {"status": "ok"}
    app = object()

    await _handle_reject("user_1", {"approval_id": "apr_003"}, app)

    mock_process.assert_awaited_once_with("user_1", "apr_003", "reject", app)
