"""Integration tests for cross-cutting concerns.

Tests startup recovery, circuit breaker state transitions,
budget degradation, and observation cursor rollover.
"""

from unittest.mock import AsyncMock, MagicMock

import pytest

from src.orchestrator.budget import BudgetStatus, BudgetTracker
from src.services.mcp_resilience import MCPCircuitBreaker


class TestStartupRecovery:
    """Test the startup recovery process."""

    @pytest.mark.asyncio
    async def test_recovery_marks_stale_plans(self):
        """Orphaned plans older than 1 hour get marked stale_on_recovery."""
        from src.orchestrator.recovery import run_startup_recovery

        stale_plan = MagicMock()
        stale_plan.plan_id = "plan_stale1"
        stale_plan.goal = "old goal"
        stale_plan.status = "planned"

        mock_result = MagicMock()
        mock_result.scalars.return_value.all.return_value = [stale_plan]

        empty = MagicMock(scalars=MagicMock(return_value=MagicMock(all=MagicMock(return_value=[]))))

        db = AsyncMock()
        # Three execute calls: plans, task_runs, approvals
        db.execute = AsyncMock(side_effect=[mock_result, empty, empty])
        db.commit = AsyncMock()

        summary = await run_startup_recovery(db)
        assert summary["orphaned_plans"] == 1
        assert stale_plan.status == "stale_on_recovery"

    @pytest.mark.asyncio
    async def test_recovery_marks_stale_task_runs(self):
        """Running task runs older than 15 min get marked failed."""
        from src.orchestrator.recovery import run_startup_recovery

        stale_run = MagicMock()
        stale_run.run_id = "run_stale1"
        stale_run.plan_id = "plan_001"
        stale_run.status = "running"

        empty = MagicMock(
            scalars=MagicMock(return_value=MagicMock(all=MagicMock(return_value=[])))
        )
        run_result = MagicMock()
        run_result.scalars.return_value.all.return_value = [stale_run]

        db = AsyncMock()
        # Three execute calls: plans, task_runs, approvals
        db.execute = AsyncMock(side_effect=[empty, run_result, empty])
        db.commit = AsyncMock()

        summary = await run_startup_recovery(db)
        assert summary["stale_task_runs"] == 1
        assert stale_run.status == "failed"
        assert stale_run.error == {"message": "stale_on_recovery"}

    @pytest.mark.asyncio
    async def test_recovery_expires_approvals(self):
        """Pending approvals past TTL get expired."""
        from src.orchestrator.recovery import run_startup_recovery

        expired_approval = MagicMock()
        expired_approval.approval_id = "apr_exp1"
        expired_approval.title = "Old approval"
        expired_approval.status = "pending"

        empty = MagicMock(scalars=MagicMock(return_value=MagicMock(all=MagicMock(return_value=[]))))
        apr_result = MagicMock()
        apr_result.scalars.return_value.all.return_value = [expired_approval]

        db = AsyncMock()
        # Three execute calls: plans, task_runs, approvals
        db.execute = AsyncMock(side_effect=[empty, empty, apr_result])
        db.commit = AsyncMock()

        summary = await run_startup_recovery(db)
        assert summary["expired_approvals"] == 1
        assert expired_approval.status == "expired"


class TestCircuitBreakerIntegration:
    """Test circuit breaker state transitions under load."""

    def test_circuit_opens_after_threshold_failures(self):
        cb = MCPCircuitBreaker(failure_threshold=3, cooldown_seconds=60.0)
        assert cb.is_available("test-server")

        for _ in range(3):
            cb.record_failure("test-server")

        assert not cb.is_available("test-server")

    def test_circuit_recovers_after_cooldown(self):
        cb = MCPCircuitBreaker(failure_threshold=2, cooldown_seconds=0.01)
        cb.record_failure("server")
        cb.record_failure("server")
        assert not cb.is_available("server")

        import time

        time.sleep(0.02)
        # After cooldown, should be half_open (available)
        assert cb.is_available("server")

    def test_independent_circuits_per_server(self):
        cb = MCPCircuitBreaker(failure_threshold=2)
        cb.record_failure("server-a")
        cb.record_failure("server-a")
        assert not cb.is_available("server-a")
        assert cb.is_available("server-b")

    def test_success_resets_failure_count(self):
        cb = MCPCircuitBreaker(failure_threshold=3)
        cb.record_failure("server")
        cb.record_failure("server")
        cb.record_success("server")
        # After success, failure count resets, so one more failure shouldn't open
        cb.record_failure("server")
        assert cb.is_available("server")

    def test_get_all_states(self):
        cb = MCPCircuitBreaker(failure_threshold=2)
        cb.record_failure("gmail")
        cb.record_failure("gmail")
        cb.record_success("slack")
        states = cb.get_all_states()
        assert states["gmail"] == "open"
        assert states["slack"] == "closed"


class TestBudgetDegradation:
    """Test budget tracking and graceful degradation."""

    def test_budget_normal_mode(self):
        tracker = BudgetTracker(daily_limit_usd=5.0)
        status = BudgetStatus(
            daily_spend_usd=1.0,
            daily_limit_usd=5.0,
            percent_used=20.0,
            budget_mode="normal",
            remaining_usd=4.0,
        )
        assert tracker.should_allow_perception(status)

    def test_budget_degraded_allows_perception_with_slower_interval(self):
        tracker = BudgetTracker(daily_limit_usd=5.0)
        status = BudgetStatus(
            daily_spend_usd=4.2,
            daily_limit_usd=5.0,
            percent_used=84.0,
            budget_mode="degraded",
            remaining_usd=0.8,
        )
        assert tracker.should_allow_perception(status)
        assert tracker.get_perception_interval_multiplier(status) == 3

    def test_budget_paused_mode_blocks_perception(self):
        tracker = BudgetTracker(daily_limit_usd=5.0)
        status = BudgetStatus(
            daily_spend_usd=4.9,
            daily_limit_usd=5.0,
            percent_used=98.0,
            budget_mode="paused",
            remaining_usd=0.1,
        )
        assert not tracker.should_allow_perception(status)

    def test_cycle_budget_check(self):
        tracker = BudgetTracker(daily_limit_usd=5.0)
        assert tracker.check_cycle_budget(10_000)
        assert not tracker.check_cycle_budget(60_000)
