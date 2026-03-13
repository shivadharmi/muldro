"""Tests for heartbeat hardening — approval expiry and plan invalidation."""

from datetime import datetime, timedelta, timezone
from unittest.mock import AsyncMock, MagicMock

import pytest

from src.services.heartbeat import HeartbeatService
from tests.conftest import make_mock_settings


@pytest.fixture
def settings():
    return make_mock_settings(plan_ttl_hours=72, approval_ttl_hours=24)


@pytest.fixture
def mock_db():
    db = MagicMock()
    db.execute = AsyncMock()
    db.flush = AsyncMock()
    return db


@pytest.mark.asyncio
async def test_expire_approvals(settings, mock_db):
    """Should expire pending approvals past their deadline."""
    now = datetime.now(timezone.utc)

    expired_approval = MagicMock()
    expired_approval.status = "pending"
    expired_approval.expires_at = now - timedelta(hours=2)
    expired_approval.execution_id = "exec_001"

    # First execute: approval query
    approval_result = MagicMock()
    approval_result.scalars.return_value.all.return_value = [expired_approval]

    # Second execute: execution query
    execution = MagicMock()
    execution.status = "awaiting_approval"
    exec_result = MagicMock()
    exec_result.scalar_one_or_none.return_value = execution

    mock_db.execute = AsyncMock(
        side_effect=[
            # _expire_stale_memories → empty
            MagicMock(scalars=MagicMock(return_value=MagicMock(all=MagicMock(return_value=[])))),
            # _find_stale_plans → empty
            MagicMock(scalars=MagicMock(return_value=MagicMock(all=MagicMock(return_value=[])))),
            # _expire_approvals → expired_approval
            approval_result,
            # execution lookup
            exec_result,
            # _invalidate_old_plans → empty
            MagicMock(scalars=MagicMock(return_value=MagicMock(all=MagicMock(return_value=[])))),
        ]
    )

    service = HeartbeatService(settings=settings, db=mock_db)
    result = await service.run("usr_default")

    assert result["expired_approvals"] == 1
    assert expired_approval.status == "expired"
    assert execution.status == "cancelled"


@pytest.mark.asyncio
async def test_invalidate_old_plans(settings, mock_db):
    """Should invalidate plans older than TTL."""
    old_plan = MagicMock()
    old_plan.plan_id = "plan_old"
    old_plan.status = "created"
    old_plan.created_at = datetime.now(timezone.utc) - timedelta(hours=100)

    mock_db.execute = AsyncMock(
        side_effect=[
            # _expire_stale_memories → empty
            MagicMock(scalars=MagicMock(return_value=MagicMock(all=MagicMock(return_value=[])))),
            # _find_stale_plans → empty (24h stale)
            MagicMock(scalars=MagicMock(return_value=MagicMock(all=MagicMock(return_value=[])))),
            # _expire_approvals → empty
            MagicMock(scalars=MagicMock(return_value=MagicMock(all=MagicMock(return_value=[])))),
            # _invalidate_old_plans → old_plan
            MagicMock(
                scalars=MagicMock(return_value=MagicMock(all=MagicMock(return_value=[old_plan])))
            ),
        ]
    )

    service = HeartbeatService(settings=settings, db=mock_db)
    result = await service.run("usr_default")

    assert result["invalidated_plans"] == 1
    assert old_plan.status == "failed"


@pytest.mark.asyncio
async def test_heartbeat_no_work(settings, mock_db):
    """Should return zero counts when nothing needs cleanup."""
    empty_result = MagicMock(
        scalars=MagicMock(return_value=MagicMock(all=MagicMock(return_value=[])))
    )
    mock_db.execute = AsyncMock(return_value=empty_result)

    service = HeartbeatService(settings=settings, db=mock_db)
    result = await service.run("usr_default")

    assert result["expired_memories"] == 0
    assert result["stale_plans_found"] == 0
    assert result["plans_escalated"] == 0
    assert result["expired_approvals"] == 0
    assert result["invalidated_plans"] == 0
