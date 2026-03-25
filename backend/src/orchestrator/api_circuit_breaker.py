"""Circuit breaker for the Anthropic Claude API.

Tracks failures across all agent calls and opens the circuit when
the API is experiencing sustained outages. Prevents cascade failures
and allows the system to degrade gracefully.

Pattern mirrors mcp_resilience.py but tuned for LLM API characteristics:
- Higher failure threshold (5 vs 3) — transient errors are common
- Shorter cooldown (120s vs 300s) — API outages resolve faster than MCP servers
- Model-aware tracking — separate circuit per model tier
"""

import logging
import time
from dataclasses import dataclass, field
from enum import Enum

logger = logging.getLogger(__name__)


class CircuitState(str, Enum):
    CLOSED = "closed"
    OPEN = "open"
    HALF_OPEN = "half_open"


@dataclass
class ModelCircuit:
    state: CircuitState = CircuitState.CLOSED
    failure_count: int = 0
    last_failure_at: float = 0.0
    last_success_at: float = 0.0
    opened_at: float = 0.0
    total_calls: int = 0
    total_failures: int = 0


@dataclass
class AnthropicCircuitBreaker:
    """Per-model circuit breaker for Claude API calls."""

    failure_threshold: int = 5
    cooldown_seconds: float = 120.0
    _circuits: dict[str, ModelCircuit] = field(default_factory=dict)

    def _get_circuit(self, model: str) -> ModelCircuit:
        if model not in self._circuits:
            self._circuits[model] = ModelCircuit()
        return self._circuits[model]

    def get_state(self, model: str) -> CircuitState:
        circuit = self._get_circuit(model)
        if circuit.state == CircuitState.OPEN:
            if time.monotonic() - circuit.opened_at >= self.cooldown_seconds:
                circuit.state = CircuitState.HALF_OPEN
                logger.info("api_circuit_half_open", extra={"model": model})
        return circuit.state

    def is_available(self, model: str) -> bool:
        state = self.get_state(model)
        return state in (CircuitState.CLOSED, CircuitState.HALF_OPEN)

    def record_success(self, model: str) -> None:
        circuit = self._get_circuit(model)
        if circuit.state == CircuitState.HALF_OPEN:
            logger.info("api_circuit_closed_after_recovery", extra={"model": model})
        circuit.state = CircuitState.CLOSED
        circuit.failure_count = 0
        circuit.last_success_at = time.monotonic()
        circuit.total_calls += 1

    def record_failure(self, model: str) -> None:
        circuit = self._get_circuit(model)
        circuit.failure_count += 1
        circuit.total_failures += 1
        circuit.total_calls += 1
        circuit.last_failure_at = time.monotonic()

        if circuit.failure_count >= self.failure_threshold:
            if circuit.state != CircuitState.OPEN:
                circuit.state = CircuitState.OPEN
                circuit.opened_at = time.monotonic()
                logger.warning(
                    "api_circuit_opened",
                    extra={
                        "model": model,
                        "failure_count": circuit.failure_count,
                        "total_failures": circuit.total_failures,
                    },
                )

    def get_status(self) -> dict[str, dict]:
        return {
            model: {
                "state": c.state.value,
                "failure_count": c.failure_count,
                "total_calls": c.total_calls,
                "total_failures": c.total_failures,
            }
            for model, c in self._circuits.items()
        }
