"""Circuit breaker for MCP server resilience.

Tracks failures per MCP server and opens the circuit after consecutive failures,
preventing cascade failures and allowing recovery time.
"""

import logging
import time
from dataclasses import dataclass, field
from enum import Enum

logger = logging.getLogger(__name__)


class CircuitState(str, Enum):
    CLOSED = "closed"  # Normal operation
    OPEN = "open"  # Failing, reject calls
    HALF_OPEN = "half_open"  # Testing recovery


@dataclass
class CircuitStatus:
    state: CircuitState = CircuitState.CLOSED
    failure_count: int = 0
    last_failure_at: float = 0.0
    last_success_at: float = 0.0
    opened_at: float = 0.0


@dataclass
class MCPCircuitBreaker:
    """Per-MCP-server circuit breaker."""

    failure_threshold: int = 3
    cooldown_seconds: float = 300.0  # 5 minutes
    _circuits: dict[str, CircuitStatus] = field(default_factory=dict)

    def _get_circuit(self, server: str) -> CircuitStatus:
        if server not in self._circuits:
            self._circuits[server] = CircuitStatus()
        return self._circuits[server]

    def get_state(self, server: str) -> CircuitState:
        """Get the current circuit state for a server."""
        circuit = self._get_circuit(server)
        if circuit.state == CircuitState.OPEN:
            # Check if cooldown has elapsed → transition to half_open
            if time.monotonic() - circuit.opened_at >= self.cooldown_seconds:
                circuit.state = CircuitState.HALF_OPEN
                logger.info("circuit_half_open", extra={"server": server})
        return circuit.state

    def is_available(self, server: str) -> bool:
        """Check if a server is available for calls."""
        state = self.get_state(server)
        return state in (CircuitState.CLOSED, CircuitState.HALF_OPEN)

    def record_success(self, server: str) -> None:
        """Record a successful call — resets failure count, closes circuit."""
        circuit = self._get_circuit(server)
        if circuit.state == CircuitState.HALF_OPEN:
            logger.info("circuit_closed_after_recovery", extra={"server": server})
        circuit.state = CircuitState.CLOSED
        circuit.failure_count = 0
        circuit.last_success_at = time.monotonic()

    def record_failure(self, server: str) -> None:
        """Record a failed call — may open the circuit."""
        circuit = self._get_circuit(server)
        circuit.failure_count += 1
        circuit.last_failure_at = time.monotonic()

        if circuit.state == CircuitState.HALF_OPEN:
            # Failed during recovery probe → reopen
            circuit.state = CircuitState.OPEN
            circuit.opened_at = time.monotonic()
            logger.warning("circuit_reopened", extra={"server": server})
        elif circuit.failure_count >= self.failure_threshold:
            circuit.state = CircuitState.OPEN
            circuit.opened_at = time.monotonic()
            logger.warning(
                "circuit_opened",
                extra={
                    "server": server,
                    "failure_count": circuit.failure_count,
                    "cooldown_seconds": self.cooldown_seconds,
                },
            )

    def get_all_states(self) -> dict[str, str]:
        """Get circuit states for all tracked servers."""
        return {server: self.get_state(server).value for server in self._circuits}

    def reset(self, server: str) -> None:
        """Manually reset a circuit to closed state."""
        self._circuits.pop(server, None)
