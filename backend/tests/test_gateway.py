"""Tests for MCPGateway circuit breaker and tool management."""

import time

from src.integrations.gateway import (
    CB_FAILURE_THRESHOLD,
    CB_RECOVERY_SECONDS,
    CircuitBreaker,
    CircuitState,
    MCPGateway,
    ServerConnection,
)


class TestCircuitBreaker:
    def test_initial_state(self):
        cb = CircuitBreaker()
        assert cb.state == CircuitState.CLOSED
        assert cb.failure_count == 0
        assert cb.is_available()

    def test_record_success_resets(self):
        cb = CircuitBreaker(failure_count=2)
        cb.record_success()
        assert cb.failure_count == 0
        assert cb.state == CircuitState.CLOSED

    def test_opens_after_threshold(self):
        cb = CircuitBreaker()
        for _ in range(CB_FAILURE_THRESHOLD):
            cb.record_failure()
        assert cb.state == CircuitState.OPEN
        assert not cb.is_available()

    def test_half_open_after_recovery(self):
        cb = CircuitBreaker()
        for _ in range(CB_FAILURE_THRESHOLD):
            cb.record_failure()
        assert cb.state == CircuitState.OPEN
        # Simulate time passing
        cb.last_failure_time = time.monotonic() - CB_RECOVERY_SECONDS - 1
        assert cb.is_available()
        assert cb.state == CircuitState.HALF_OPEN

    def test_stays_closed_below_threshold(self):
        cb = CircuitBreaker()
        for _ in range(CB_FAILURE_THRESHOLD - 1):
            cb.record_failure()
        assert cb.state == CircuitState.CLOSED
        assert cb.is_available()


class TestServerConnection:
    def test_create_connection(self):
        conn = ServerConnection(server_name="test-server")
        assert conn.server_name == "test-server"
        assert conn.tools == []
        assert conn.connected is False
        assert conn.circuit.state == CircuitState.CLOSED


class TestMCPGateway:
    def test_empty_gateway(self):
        gw = MCPGateway()
        assert gw.list_tools() == []
        assert gw.get_server_health() == {}
        assert not gw.is_gateway_tool("some_tool")

    def test_get_server_for_tool_unknown(self):
        gw = MCPGateway()
        assert gw.get_server_for_tool("unknown_tool") is None

    def test_normalize_tool_name(self):
        gw = MCPGateway()
        assert gw.normalize_tool_name("gmail_send") == "email.send"
        assert gw.normalize_tool_name("unknown_xyz") == "unknown_xyz"
