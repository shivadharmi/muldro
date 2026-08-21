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
        await _get_approval(
            mock_db, "apr_001", TEST_USER_ID, TEST_WORKSPACE_ID, intended_action="reject"
        )
    assert exc_info.value.status_code == 400


@pytest.mark.asyncio
async def test_get_approval_success(mock_db):
    """Should return pending approval successfully."""
    approval = MagicMock()
    approval.status = "pending"
    approval.expires_at = None
    result_mock = MagicMock()
    result_mock.scalar_one_or_none.return_value = approval
    mock_db.execute = AsyncMock(return_value=result_mock)

    result = await _get_approval(mock_db, "apr_001", TEST_USER_ID, TEST_WORKSPACE_ID)
    assert result.status == "pending"


# ── Double-confirming a PREPARED action ─────────────────────────────────────────────────
#
# A prepared replay ends in status `executed`, not `approved`. `_get_approval`'s T6
# idempotency compares against the literal "approved", so the second click on the queue
# card returned 400 "Approval already executed" — for an action that HAD run, exactly once.
#
# That is not a legible failure. The frontend's `routeApprovalAction` catches the throw,
# toasts the message and does NOT call `onSuccess`, so the queue never refreshes: the row
# stays on screen looking unactioned, and every further click repeats the error forever.
#
# `executed` is what a successful approve of a prepared row LOOKS like — approved and then
# some — so it satisfies the approve intent. `failed` still 400s: a permanent refusal is
# information the founder needs, not a double-click to absorb.


def _row(status: str):
    approval = MagicMock()
    approval.status = status
    approval.expires_at = None
    return approval


def _db_returning(approval):
    db = MagicMock()
    result = MagicMock()
    result.scalar_one_or_none.return_value = approval
    db.execute = AsyncMock(return_value=result)
    db.flush = AsyncMock()
    return db


@pytest.mark.asyncio
async def test_re_approving_an_executed_action_is_idempotent_not_an_error():
    approval = _row("executed")

    got = await _get_approval(
        _db_returning(approval),
        "apr_001",
        TEST_USER_ID,
        TEST_WORKSPACE_ID,
        intended_action="approve",
    )

    assert got is approval


@pytest.mark.asyncio
async def test_rejecting_an_executed_action_is_still_an_error():
    """Teeth: you cannot reject what already ran."""
    with pytest.raises(HTTPException) as exc:
        await _get_approval(
            _db_returning(_row("executed")),
            "apr_001",
            TEST_USER_ID,
            TEST_WORKSPACE_ID,
            intended_action="reject",
        )
    assert exc.value.status_code == 400


@pytest.mark.asyncio
async def test_re_approving_a_failed_action_is_still_an_error():
    """Teeth: a permanent refusal is information, not a double-click to swallow."""
    with pytest.raises(HTTPException) as exc:
        await _get_approval(
            _db_returning(_row("failed")),
            "apr_001",
            TEST_USER_ID,
            TEST_WORKSPACE_ID,
            intended_action="approve",
        )
    assert exc.value.status_code == 400
