"""Tests for _Neo4jCircuit and GraphEngine circuit-breaker integration."""

import time
from unittest.mock import AsyncMock, MagicMock

import pytest

from src.services.graph_engine import GraphEngine, _Neo4jCircuit

# ---------------------------------------------------------------------------
# _Neo4jCircuit unit tests
# ---------------------------------------------------------------------------


class TestNeo4jCircuit:
    def test_starts_closed(self):
        circuit = _Neo4jCircuit()
        assert circuit._state == "closed"
        assert circuit._failures == 0
        assert circuit.allow_request() is True

    def test_opens_after_failure_threshold(self):
        circuit = _Neo4jCircuit()
        for i in range(_Neo4jCircuit.FAILURE_THRESHOLD - 1):
            circuit.record_failure()
            assert circuit._state == "closed", f"Should stay closed after {i + 1} failures"

        # 5th failure — must open
        circuit.record_failure()
        assert circuit._state == "open"
        assert circuit.allow_request() is False

    def test_still_open_before_cooldown_expires(self):
        circuit = _Neo4jCircuit()
        for _ in range(_Neo4jCircuit.FAILURE_THRESHOLD):
            circuit.record_failure()
        assert circuit._state == "open"
        # Simulate only 1 second elapsed (well within 120s cooldown)
        circuit._opened_at = time.monotonic() - 1
        assert circuit.allow_request() is False

    def test_transitions_to_half_open_after_cooldown(self):
        circuit = _Neo4jCircuit()
        for _ in range(_Neo4jCircuit.FAILURE_THRESHOLD):
            circuit.record_failure()
        assert circuit._state == "open"

        # Fake that cooldown has elapsed
        circuit._opened_at = time.monotonic() - (_Neo4jCircuit.COOLDOWN_SECONDS + 1)
        assert circuit.allow_request() is True
        assert circuit._state == "half_open"

    def test_success_closes_circuit(self):
        circuit = _Neo4jCircuit()
        for _ in range(_Neo4jCircuit.FAILURE_THRESHOLD):
            circuit.record_failure()
        circuit._opened_at = time.monotonic() - (_Neo4jCircuit.COOLDOWN_SECONDS + 1)
        circuit.allow_request()  # transitions to half_open
        assert circuit._state == "half_open"

        circuit.record_success()
        assert circuit._state == "closed"
        assert circuit._failures == 0
        assert circuit.allow_request() is True

    def test_failure_in_half_open_reopens_circuit(self):
        circuit = _Neo4jCircuit()
        for _ in range(_Neo4jCircuit.FAILURE_THRESHOLD):
            circuit.record_failure()
        circuit._opened_at = time.monotonic() - (_Neo4jCircuit.COOLDOWN_SECONDS + 1)
        circuit.allow_request()  # transitions to half_open
        assert circuit._state == "half_open"

        # One more failure — failures counter goes above threshold again → re-open
        circuit.record_failure()
        assert circuit._state == "open"
        assert circuit.allow_request() is False

    def test_success_resets_failure_count(self):
        circuit = _Neo4jCircuit()
        # Accumulate some (sub-threshold) failures then succeed
        circuit.record_failure()
        circuit.record_failure()
        circuit.record_success()
        assert circuit._failures == 0
        assert circuit._state == "closed"

    def test_half_open_allows_probe(self):
        circuit = _Neo4jCircuit()
        circuit._state = "half_open"
        assert circuit.allow_request() is True


# ---------------------------------------------------------------------------
# GraphEngine integration tests
# ---------------------------------------------------------------------------


def _make_engine_with_mock_driver():
    """Return a (GraphEngine, mock_session) pair wired for testing."""
    settings = MagicMock()
    settings.neo4j_url = "bolt://localhost:7687"
    settings.neo4j_user = "neo4j"
    setattr(settings, "neo4j_password", "test")

    engine = GraphEngine(settings)

    # Build mock session / result chain
    mock_result = AsyncMock()
    mock_result.single = AsyncMock(return_value=None)
    mock_result.data = AsyncMock(return_value=[])

    mock_session = AsyncMock()
    mock_session.__aenter__ = AsyncMock(return_value=mock_session)
    mock_session.__aexit__ = AsyncMock(return_value=False)
    mock_session.run = AsyncMock(return_value=mock_result)

    mock_driver = MagicMock()
    mock_driver.session = MagicMock(return_value=mock_session)

    # Bypass lazy init
    engine._driver = mock_driver

    return engine, mock_session


@pytest.mark.asyncio
async def test_sync_entity_skips_when_circuit_open():
    """sync_entity must return immediately without calling the driver when circuit is open."""
    engine, mock_session = _make_engine_with_mock_driver()

    # Force circuit open
    for _ in range(_Neo4jCircuit.FAILURE_THRESHOLD):
        engine._circuit.record_failure()
    assert engine._circuit._state == "open"

    await engine.sync_entity(
        entity_id="ent_001",
        entity_type="person",
        name="Alice",
        user_id="usr_test",
    )

    mock_session.run.assert_not_called()


@pytest.mark.asyncio
async def test_sync_entity_records_success_on_good_write():
    """sync_entity must call record_success after a successful Neo4j write."""
    engine, mock_session = _make_engine_with_mock_driver()

    assert engine._circuit._state == "closed"
    assert engine._circuit._failures == 0

    await engine.sync_entity(
        entity_id="ent_002",
        entity_type="company",
        name="Acme",
        user_id="usr_test",
    )

    mock_session.run.assert_called_once()
    assert engine._circuit._state == "closed"
    assert engine._circuit._failures == 0


@pytest.mark.asyncio
async def test_sync_entity_records_failure_on_exception():
    """sync_entity must call record_failure when the Neo4j session raises."""
    engine, mock_session = _make_engine_with_mock_driver()
    mock_session.run = AsyncMock(side_effect=RuntimeError("connection refused"))

    await engine.sync_entity(
        entity_id="ent_003",
        entity_type="person",
        name="Bob",
        user_id="usr_test",
    )

    assert engine._circuit._failures == 1


@pytest.mark.asyncio
async def test_circuit_opens_after_five_consecutive_failures():
    """Five failing sync_entity calls must open the circuit."""
    engine, mock_session = _make_engine_with_mock_driver()
    mock_session.run = AsyncMock(side_effect=RuntimeError("neo4j down"))

    for _ in range(_Neo4jCircuit.FAILURE_THRESHOLD):
        await engine.sync_entity(
            entity_id="ent_x",
            entity_type="person",
            name="X",
            user_id="usr_test",
        )

    assert engine._circuit._state == "open"
    # Subsequent call must not reach the driver
    mock_session.run.reset_mock()
    await engine.sync_entity(
        entity_id="ent_x",
        entity_type="person",
        name="X",
        user_id="usr_test",
    )
    mock_session.run.assert_not_called()


@pytest.mark.asyncio
async def test_delete_entity_skips_when_circuit_open():
    """delete_entity must be a no-op when the circuit is open."""
    engine, mock_session = _make_engine_with_mock_driver()
    for _ in range(_Neo4jCircuit.FAILURE_THRESHOLD):
        engine._circuit.record_failure()

    await engine.delete_entity("ent_del_001")
    mock_session.run.assert_not_called()


@pytest.mark.asyncio
async def test_traverse_returns_empty_when_circuit_open():
    """traverse must return empty dict when circuit is open."""
    engine, mock_session = _make_engine_with_mock_driver()
    for _ in range(_Neo4jCircuit.FAILURE_THRESHOLD):
        engine._circuit.record_failure()

    result = await engine.traverse("ent_001", "usr_test")
    assert result == {"nodes": [], "edges": []}
    mock_session.run.assert_not_called()


@pytest.mark.asyncio
async def test_traverse_weighted_returns_empty_when_circuit_open():
    engine, mock_session = _make_engine_with_mock_driver()
    for _ in range(_Neo4jCircuit.FAILURE_THRESHOLD):
        engine._circuit.record_failure()

    result = await engine.traverse_weighted("ent_001", "usr_test")
    assert result == []
    mock_session.run.assert_not_called()


@pytest.mark.asyncio
async def test_circuit_closes_after_successful_probe():
    """After cooldown, a successful probe must close the circuit."""
    engine, mock_session = _make_engine_with_mock_driver()

    # Open the circuit
    for _ in range(_Neo4jCircuit.FAILURE_THRESHOLD):
        engine._circuit.record_failure()

    # Simulate cooldown elapsed
    engine._circuit._opened_at = time.monotonic() - (_Neo4jCircuit.COOLDOWN_SECONDS + 1)

    # Successful call → circuit should close
    await engine.sync_entity(
        entity_id="ent_probe",
        entity_type="person",
        name="Probe",
        user_id="usr_test",
    )

    mock_session.run.assert_called_once()
    assert engine._circuit._state == "closed"
