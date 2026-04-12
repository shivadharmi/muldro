"""Tests for background task row locking in SchedulerLoop.

Verifies that _tick_background_tasks uses FOR UPDATE SKIP LOCKED
to prevent concurrent scheduler ticks from picking up the same TaskRun.
"""

from unittest.mock import AsyncMock, MagicMock

import pytest
from sqlalchemy.dialects import postgresql

from src.services.scheduler import SchedulerLoop
from tests.conftest import TEST_USER_ID, make_mock_settings


@pytest.mark.asyncio
async def test_background_task_query_uses_for_update_skip_locked():
    """The pending-task SELECT must include FOR UPDATE SKIP LOCKED."""
    captured_queries: list[str] = []

    async def capturing_execute(stmt, *args, **kwargs):
        compiled = stmt.compile(
            dialect=postgresql.dialect(),
            compile_kwargs={"literal_binds": True},
        )
        captured_queries.append(str(compiled))
        result = MagicMock()
        result.scalars.return_value.all.return_value = []
        return result

    mock_db = AsyncMock()
    mock_db.execute = AsyncMock(side_effect=capturing_execute)
    mock_db.commit = AsyncMock()

    mock_cm = AsyncMock()
    mock_cm.__aenter__ = AsyncMock(return_value=mock_db)
    mock_cm.__aexit__ = AsyncMock(return_value=False)

    factory = MagicMock(return_value=mock_cm)

    scheduler = SchedulerLoop.__new__(SchedulerLoop)
    scheduler._settings = make_mock_settings()
    scheduler._user_ids = [TEST_USER_ID]
    scheduler._orchestrator = MagicMock()
    scheduler._running = True

    await scheduler._tick_background_tasks(factory)

    assert len(captured_queries) == 1, "Expected exactly one query execution"
    query = captured_queries[0].lower()
    assert "for update" in query, f"Query missing FOR UPDATE: {captured_queries[0]}"
    assert "skip locked" in query, f"Query missing SKIP LOCKED: {captured_queries[0]}"
