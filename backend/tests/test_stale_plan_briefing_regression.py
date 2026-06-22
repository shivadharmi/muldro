"""Regression tests for the "stuck critical plan pollutes every briefing" bug.

Root cause chain (confirmed against a live DB):
  1. A perception-triggered Plan was created (status="created") with a
     background TaskRun to execute it.
  2. The run failed verification, but the parent Plan was never moved out of
     "created" — the executor never reconciled plan status with run status.
  3. The HeartbeatService reaper that invalidates stale "created" plans after
     ``plan_ttl_hours`` was never scheduled (no "heartbeat" entry in
     DEFAULT_SCHEDULES), so it never ran.
  4. The briefing's active-plans query had no recency bound, so the 60-day-old
     "created" plan was injected into every daily briefing as a critical item,
     producing "1 critical security alert requires immediate attention —
     no other activity" every single day.

These tests lock in the three independent defenses against that chain.
"""

from unittest.mock import AsyncMock, MagicMock

import pytest

from src.services.graph_executor import GraphExecutor
from src.services.presenter import Presenter
from src.services.schedule_seeder import (
    DEFAULT_SCHEDULES,
    WORKSPACE_CREATION_SCHEDULES,
)
from tests.conftest import TEST_USER_ID, make_mock_settings


# ---------------------------------------------------------------------------
# Fix 1 — the maintenance reaper must actually be scheduled
# ---------------------------------------------------------------------------
class TestHeartbeatScheduled:
    def test_default_schedules_include_heartbeat(self):
        """DEFAULT_SCHEDULES must seed a 'heartbeat' action so the stale-plan
        reaper (HeartbeatService) runs in normal operation, not only via the
        manual /v1/system/heartbeat endpoint."""
        action_types = {s["action_type"] for s in DEFAULT_SCHEDULES}
        assert "heartbeat" in action_types

    def test_heartbeat_enabled_at_workspace_creation(self):
        """Heartbeat is connector-independent housekeeping, so it must be
        enabled at workspace creation — not gated on OAuth like observe_*."""
        hb = next(s for s in DEFAULT_SCHEDULES if s["action_type"] == "heartbeat")
        assert hb["name"] in WORKSPACE_CREATION_SCHEDULES


# ---------------------------------------------------------------------------
# Fix 2 — a terminal run must reconcile its parent Plan's status
# ---------------------------------------------------------------------------
class TestPlanStatusReconciliation:
    def _executor_with_plan(self, plan):
        db = MagicMock()
        result = MagicMock()
        result.scalar_one_or_none.return_value = plan
        db.execute = AsyncMock(return_value=result)
        return GraphExecutor(make_mock_settings(), db)

    @pytest.mark.asyncio
    async def test_failed_run_marks_plan_failed(self):
        plan = MagicMock()
        plan.status = "created"
        executor = self._executor_with_plan(plan)
        run = MagicMock(status="failed", plan_id="plan_x")

        await executor._reconcile_plan_status(run)

        assert plan.status == "failed"

    @pytest.mark.asyncio
    async def test_completed_run_marks_plan_completed(self):
        plan = MagicMock()
        plan.status = "executing"
        executor = self._executor_with_plan(plan)
        run = MagicMock(status="completed", plan_id="plan_x")

        await executor._reconcile_plan_status(run)

        assert plan.status == "completed"

    @pytest.mark.asyncio
    async def test_already_terminal_plan_not_overwritten(self):
        """A plan already in a terminal state must not be flipped (e.g. a
        cancelled run should not resurrect a completed plan)."""
        plan = MagicMock()
        plan.status = "completed"
        executor = self._executor_with_plan(plan)
        run = MagicMock(status="cancelled", plan_id="plan_x")

        await executor._reconcile_plan_status(run)

        assert plan.status == "completed"


# ---------------------------------------------------------------------------
# Fix 3 — the briefing must not surface plans older than the plan TTL
# ---------------------------------------------------------------------------
class TestBriefingActivePlansAreTimeBounded:
    @pytest.mark.asyncio
    async def test_active_plans_query_bounds_by_created_at(self):
        """_get_active_plans must filter on created_at so a stale plan the
        reaper has not yet collected can never reach the briefing."""
        db = MagicMock()
        no_result = MagicMock()
        no_result.scalars.return_value.all.return_value = []
        db.execute = AsyncMock(return_value=no_result)

        presenter = Presenter(settings=make_mock_settings(), db=db)
        await presenter._get_active_plans(TEST_USER_ID, workspace_id="ws_x")

        stmt = db.execute.call_args[0][0]
        # Inspect the WHERE clause only — created_at appears in ORDER BY
        # regardless, so asserting on the full statement would be a false
        # positive. The recency bound must live in the WHERE clause.
        where_sql = str(stmt.whereclause)
        assert "created_at" in where_sql
        # status filter must remain — we still only want non-terminal plans
        assert "status" in where_sql
