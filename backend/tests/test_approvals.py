"""Tests for approval logic — _get_approval helper and route integration."""

from unittest.mock import AsyncMock, MagicMock

import pytest
from fastapi import HTTPException

from src.api.routes_approvals import _get_approval
from tests.conftest import TEST_USER_ID, TEST_WORKSPACE_ID


@pytest.fixture
def mock_db():
    return MagicMock()


@pytest.mark.asyncio
async def test_get_approval_not_found(mock_db):
    """Should raise 404 when approval doesn't exist."""
    no_result = MagicMock()
    no_result.scalar_one_or_none.return_value = None
    mock_db.execute = AsyncMock(return_value=no_result)

    with pytest.raises(HTTPException) as exc_info:
        await _get_approval(mock_db, "apr_missing", TEST_USER_ID, TEST_WORKSPACE_ID)
    assert exc_info.value.status_code == 404


@pytest.mark.asyncio
async def test_get_approval_already_decided(mock_db):
    """Should raise 400 when approval is already decided."""
    approval = MagicMock()
    approval.status = "approved"
    result_mock = MagicMock()
    result_mock.scalar_one_or_none.return_value = approval
    mock_db.execute = AsyncMock(return_value=result_mock)

    with pytest.raises(HTTPException) as exc_info:
        await _get_approval(mock_db, "apr_001", TEST_USER_ID, TEST_WORKSPACE_ID)
    assert exc_info.value.status_code == 400


@pytest.mark.asyncio
async def test_get_approval_success(mock_db):
    """Should return pending approval successfully."""
    approval = MagicMock()
    approval.status = "pending"
    result_mock = MagicMock()
    result_mock.scalar_one_or_none.return_value = approval
    mock_db.execute = AsyncMock(return_value=result_mock)

    result = await _get_approval(mock_db, "apr_001", TEST_USER_ID, TEST_WORKSPACE_ID)
    assert result.status == "pending"
