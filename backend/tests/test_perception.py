"""Tests for perception layer — circuit breaker, adaptive backoff, workspace passthrough."""

from datetime import datetime, timedelta, timezone
from unittest.mock import AsyncMock, MagicMock, patch

import pytest

from src.orchestrator.perception import PerceptionCoordinator


@pytest.fixture
def mock_orchestrator():
    orch = MagicMock()
    orch.run_perception_cycle = AsyncMock(
        return_value={"status": "completed", "source": "gmail", "events": 2}
    )
    orch._publish_event = AsyncMock()
    orch._db_factory = MagicMock()
    return orch


@pytest.fixture
def coordinator(mock_orchestrator):
    coord = PerceptionCoordinator(
        mock_orchestrator, user_id="usr_test", workspace_id="ws_test"
    )
    coord.enable_source("gmail")
    coord.enable_source("github")
    return coord


class TestWorkspacePassthrough:
    @pytest.mark.asyncio
    async def test_run_due_cycles_passes_workspace_id(self, coordinator, mock_orchestrator):
        """run_due_cycles should pass workspace_id to run_perception_cycle."""
        with patch.object(coordinator, "refresh_enabled_sources", new=AsyncMock()):
            await coordinator.run_due_cycles()

        for call in mock_orchestrator.run_perception_cycle.call_args_list:
            assert call.kwargs.get("workspace_id") == "ws_test"


class TestCircuitBreaker:
    @pytest.mark.asyncio
    async def test_circuit_opens_after_consecutive_failures(self, coordinator, mock_orchestrator):
        """After 3 consecutive failures, circuit should open and skip polling."""
        mock_orchestrator.run_perception_cycle = AsyncMock(
            return_value={"status": "error", "source": "gmail", "error": "timeout"}
        )

        with patch.object(coordinator, "refresh_enabled_sources", new=AsyncMock()):
            # Run 3 cycles — all fail
            for _ in range(3):
                coordinator._last_run.clear()
                await coordinator.run_due_cycles()

            # 4th cycle — circuit should be open for gmail
            coordinator._last_run.clear()
            results = await coordinator.run_due_cycles()

        gmail_results = [r for r in results if r.get("source") == "gmail"]
        assert any(r["status"] == "circuit_open" for r in gmail_results)

    @pytest.mark.asyncio
    async def test_circuit_closes_after_success(self, coordinator, mock_orchestrator):
        """A success should reset the circuit breaker."""
        # Record failures to open circuit
        for _ in range(3):
            coordinator._circuit_breaker.record_failure("gmail")

        # Record success to close it
        coordinator._circuit_breaker.record_success("gmail")

        assert coordinator._circuit_breaker.is_available("gmail") is True

    @pytest.mark.asyncio
    async def test_exception_records_failure(self, coordinator, mock_orchestrator):
        """Exceptions from run_perception_cycle should record a failure."""
        mock_orchestrator.run_perception_cycle = AsyncMock(
            side_effect=RuntimeError("connection lost")
        )

        with patch.object(coordinator, "refresh_enabled_sources", new=AsyncMock()):
            results = await coordinator.run_due_cycles()

        gmail_results = [r for r in results if r.get("source") == "gmail"]
        assert gmail_results[0]["status"] == "error"
        assert coordinator._consecutive_failures.get("gmail") == 1


class TestAdaptiveBackoff:
    def test_no_backoff_on_zero_failures(self, coordinator):
        """Sources with no failures should use normal interval."""
        coordinator._consecutive_failures["gmail"] = 0
        due = coordinator.get_due_sources()
        assert "gmail" in due

    def test_backoff_doubles_per_failure(self, coordinator):
        """After failures, the effective interval should double each time."""
        now = datetime.now(timezone.utc)
        # Set last_run to just over the base interval (300s) ago
        coordinator._last_run["gmail"] = now - timedelta(seconds=310)

        # With 0 failures, gmail should be due
        coordinator._consecutive_failures["gmail"] = 0
        assert "gmail" in coordinator.get_due_sources()

        # With 1 failure, interval doubles to 600s — 310s ago is not enough
        coordinator._consecutive_failures["gmail"] = 1
        assert "gmail" not in coordinator.get_due_sources()

        # With 1 failure, need 600s+ — set last_run further back
        coordinator._last_run["gmail"] = now - timedelta(seconds=610)
        assert "gmail" in coordinator.get_due_sources()

    def test_backoff_capped_at_8x(self, coordinator):
        """Backoff should not exceed 8x the base interval."""
        now = datetime.now(timezone.utc)
        # 10 failures should still cap at 8x (300 * 8 = 2400s)
        coordinator._consecutive_failures["gmail"] = 10
        coordinator._last_run["gmail"] = now - timedelta(seconds=2410)
        assert "gmail" in coordinator.get_due_sources()

        coordinator._last_run["gmail"] = now - timedelta(seconds=2390)
        assert "gmail" not in coordinator.get_due_sources()

    @pytest.mark.asyncio
    async def test_success_resets_failure_count(self, coordinator, mock_orchestrator):
        """A successful cycle should reset consecutive failure count."""
        coordinator._consecutive_failures["gmail"] = 3

        with patch.object(coordinator, "refresh_enabled_sources", new=AsyncMock()):
            await coordinator.run_due_cycles()

        assert coordinator._consecutive_failures.get("gmail") == 0
