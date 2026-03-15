"""Tests for MCP circuit breaker resilience."""

import time

from src.services.mcp_resilience import CircuitState, MCPCircuitBreaker


class TestCircuitBreaker:
    """Test circuit breaker state transitions."""

    def test_initial_state_is_closed(self):
        cb = MCPCircuitBreaker()
        assert cb.get_state("google-workspace") == CircuitState.CLOSED
        assert cb.is_available("google-workspace") is True

    def test_success_keeps_closed(self):
        cb = MCPCircuitBreaker()
        cb.record_success("google-workspace")
        assert cb.get_state("google-workspace") == CircuitState.CLOSED

    def test_single_failure_stays_closed(self):
        cb = MCPCircuitBreaker(failure_threshold=3)
        cb.record_failure("google-workspace")
        assert cb.get_state("google-workspace") == CircuitState.CLOSED
        assert cb.is_available("google-workspace") is True

    def test_threshold_failures_opens_circuit(self):
        cb = MCPCircuitBreaker(failure_threshold=3)
        cb.record_failure("google-workspace")
        cb.record_failure("google-workspace")
        cb.record_failure("google-workspace")
        assert cb.get_state("google-workspace") == CircuitState.OPEN
        assert cb.is_available("google-workspace") is False

    def test_cooldown_transitions_to_half_open(self):
        cb = MCPCircuitBreaker(failure_threshold=2, cooldown_seconds=0.01)
        cb.record_failure("slack")
        cb.record_failure("slack")
        assert cb.get_state("slack") == CircuitState.OPEN

        time.sleep(0.02)
        assert cb.get_state("slack") == CircuitState.HALF_OPEN
        assert cb.is_available("slack") is True

    def test_success_after_half_open_closes(self):
        cb = MCPCircuitBreaker(failure_threshold=2, cooldown_seconds=0.01)
        cb.record_failure("slack")
        cb.record_failure("slack")

        time.sleep(0.02)
        assert cb.get_state("slack") == CircuitState.HALF_OPEN

        cb.record_success("slack")
        assert cb.get_state("slack") == CircuitState.CLOSED

    def test_failure_during_half_open_reopens(self):
        cb = MCPCircuitBreaker(failure_threshold=2, cooldown_seconds=0.01)
        cb.record_failure("github")
        cb.record_failure("github")

        time.sleep(0.02)
        assert cb.get_state("github") == CircuitState.HALF_OPEN

        cb.record_failure("github")
        assert cb.get_state("github") == CircuitState.OPEN

    def test_independent_servers(self):
        cb = MCPCircuitBreaker(failure_threshold=2)
        cb.record_failure("google-workspace")
        cb.record_failure("google-workspace")

        assert cb.get_state("google-workspace") == CircuitState.OPEN
        assert cb.get_state("slack") == CircuitState.CLOSED

    def test_get_all_states(self):
        cb = MCPCircuitBreaker(failure_threshold=2)
        cb.record_success("google-workspace")
        cb.record_failure("slack")
        cb.record_failure("slack")

        states = cb.get_all_states()
        assert states["google-workspace"] == "closed"
        assert states["slack"] == "open"

    def test_reset_clears_circuit(self):
        cb = MCPCircuitBreaker(failure_threshold=2)
        cb.record_failure("github")
        cb.record_failure("github")
        assert cb.get_state("github") == CircuitState.OPEN

        cb.reset("github")
        assert cb.get_state("github") == CircuitState.CLOSED

    def test_success_resets_failure_count(self):
        cb = MCPCircuitBreaker(failure_threshold=3)
        cb.record_failure("slack")
        cb.record_failure("slack")
        cb.record_success("slack")
        # After success, count resets — next 2 failures shouldn't open
        cb.record_failure("slack")
        cb.record_failure("slack")
        assert cb.get_state("slack") == CircuitState.CLOSED
