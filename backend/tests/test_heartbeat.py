"""Tests for Heartbeat Service — memory expiry and plan escalation."""

from datetime import datetime, timedelta, timezone
from unittest.mock import AsyncMock, MagicMock

import pytest

from src.services.heartbeat import HeartbeatService
from tests.conftest import TEST_USER_ID, make_mock_settings


@pytest.fixture
def settings():
    return make_mock_settings()


@pytest.fixture
def mock_db():
    db = MagicMock()
    db.flush = AsyncMock()
    db.execute = AsyncMock()
    return db


@pytest.mark.asyncio
async def test_expire_stale_memories(settings, mock_db):
    """Should expire memories past their TTL."""
    old_memory = MagicMock()
    old_memory.created_at = datetime.now(timezone.utc) - timedelta(days=100)
    old_memory.ttl_days = 30
    old_memory.status = "active"

    fresh_memory = MagicMock()
    fresh_memory.created_at = datetime.now(timezone.utc) - timedelta(days=5)
    fresh_memory.ttl_days = 30
    fresh_memory.status = "active"

    empty = MagicMock(scalars=MagicMock(return_value=MagicMock(all=MagicMock(return_value=[]))))

    mem_result = MagicMock()
    mem_result.scalars.return_value.all.return_value = [old_memory, fresh_memory]

    # 5 original calls + 3 from _reflect_on_schedules
    mock_db.execute = AsyncMock(
        side_effect=[mem_result, empty, empty, empty, empty, empty, empty, empty]
    )

    service = HeartbeatService(settings=settings, db=mock_db)
    result = await service.run(TEST_USER_ID)

    assert result["expired_memories"] == 1
    assert old_memory.status == "expired"
    assert fresh_memory.status == "active"


@pytest.mark.asyncio
async def test_escalate_stale_plans(settings, mock_db):
    """Should escalate plans that have been sitting too long."""
    stale_plan = MagicMock()
    stale_plan.plan_id = "plan_001"
    stale_plan.priority = "low"
    stale_plan.status = "created"
    stale_plan.created_at = datetime.now(timezone.utc) - timedelta(hours=48)

    empty = MagicMock(scalars=MagicMock(return_value=MagicMock(all=MagicMock(return_value=[]))))

    plan_result = MagicMock()
    plan_result.scalars.return_value.all.return_value = [stale_plan]

    # 5 original calls + 3 from _reflect_on_schedules
    mock_db.execute = AsyncMock(
        side_effect=[empty, plan_result, empty, empty, empty, empty, empty, empty]
    )

    service = HeartbeatService(settings=settings, db=mock_db)
    result = await service.run(TEST_USER_ID)

    assert result["stale_plans_found"] == 1
    assert result["plans_escalated"] == 1
    assert stale_plan.priority == "medium"


@pytest.mark.asyncio
async def test_heartbeat_no_action_needed(settings, mock_db):
    """Should handle case where nothing needs attention."""
    empty_result = MagicMock()
    empty_result.scalars.return_value.all.return_value = []

    mock_db.execute = AsyncMock(return_value=empty_result)

    service = HeartbeatService(settings=settings, db=mock_db)
    result = await service.run(TEST_USER_ID)

    assert result["expired_memories"] == 0
    assert result["stale_plans_found"] == 0
    assert result["plans_escalated"] == 0


@pytest.mark.asyncio
async def test_critical_plans_not_escalated(settings, mock_db):
    """Should not escalate plans already at critical priority."""
    critical_plan = MagicMock()
    critical_plan.plan_id = "plan_002"
    critical_plan.priority = "critical"
    critical_plan.status = "created"
    critical_plan.created_at = datetime.now(timezone.utc) - timedelta(hours=48)

    empty = MagicMock(scalars=MagicMock(return_value=MagicMock(all=MagicMock(return_value=[]))))

    plan_result = MagicMock()
    plan_result.scalars.return_value.all.return_value = [critical_plan]

    # 5 original calls + 3 from _reflect_on_schedules
    mock_db.execute = AsyncMock(
        side_effect=[empty, plan_result, empty, empty, empty, empty, empty, empty]
    )

    service = HeartbeatService(settings=settings, db=mock_db)
    result = await service.run(TEST_USER_ID)

    assert result["plans_escalated"] == 0
    assert critical_plan.priority == "critical"
