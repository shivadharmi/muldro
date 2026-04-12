"""Tests for health() and get_metrics() on GraphEngine and VectorStore."""

from unittest.mock import AsyncMock, MagicMock, patch

import pytest

from tests.conftest import make_mock_settings


# ---------------------------------------------------------------------------
# Fixtures
# ---------------------------------------------------------------------------


@pytest.fixture
def neo4j_settings():
    s = make_mock_settings()
    s.neo4j_url = "bolt://localhost:7687"
    s.neo4j_user = "neo4j"
    s.neo4j_password = "test"
    return s


@pytest.fixture
def neo4j_settings_disabled():
    s = make_mock_settings()
    s.neo4j_url = ""
    return s


@pytest.fixture
def qdrant_settings():
    s = make_mock_settings()
    s.qdrant_url = "http://localhost:6333"
    s.qdrant_api_key = ""
    return s


@pytest.fixture
def qdrant_settings_disabled():
    s = make_mock_settings()
    s.qdrant_url = ""
    s.qdrant_api_key = ""
    return s


def _make_mock_session(run_result=None):
    session = AsyncMock()
    session.__aenter__ = AsyncMock(return_value=session)
    session.__aexit__ = AsyncMock(return_value=False)
    if run_result is not None:
        session.run = AsyncMock(return_value=run_result)
    return session


def _make_mock_driver(session):
    driver = MagicMock()
    driver.session.return_value = session
    return driver


# ---------------------------------------------------------------------------
# GraphEngine health()
# ---------------------------------------------------------------------------


class TestGraphEngineHealth:
    async def test_health_disabled_when_no_url(self, neo4j_settings_disabled):
        from src.services.graph_engine import GraphEngine

        engine = GraphEngine(neo4j_settings_disabled)
        result = await engine.health()
        assert result["status"] == "disabled"
        assert result["configured"] is False

    async def test_health_healthy(self, neo4j_settings):
        from src.services.graph_engine import GraphEngine

        engine = GraphEngine(neo4j_settings)
        session = _make_mock_session()
        engine._driver = _make_mock_driver(session)

        result = await engine.health()

        assert result["status"] == "healthy"
        assert result["configured"] is True
        assert "circuit_state" in result
        session.run.assert_called_once_with("RETURN 1")

    async def test_health_unreachable_when_driver_raises(self, neo4j_settings):
        from src.services.graph_engine import GraphEngine

        engine = GraphEngine(neo4j_settings)
        session = _make_mock_session()
        session.run = AsyncMock(side_effect=Exception("connection refused"))
        engine._driver = _make_mock_driver(session)

        result = await engine.health()

        assert result["status"] == "unreachable"
        assert result["configured"] is True
        assert "connection refused" in result["error"]

    async def test_health_unreachable_when_no_driver(self, neo4j_settings):
        from src.services.graph_engine import GraphEngine

        engine = GraphEngine(neo4j_settings)
        # _driver stays None and no url → _get_driver returns None
        # Patch _get_driver to simulate driver unavailable
        engine._get_driver = AsyncMock(return_value=None)

        result = await engine.health()

        assert result["status"] == "unreachable"
        assert result["configured"] is True


# ---------------------------------------------------------------------------
# GraphEngine get_metrics()
# ---------------------------------------------------------------------------


class TestGraphEngineMetrics:
    async def test_initial_metrics_zero(self, neo4j_settings):
        from src.services.graph_engine import GraphEngine

        engine = GraphEngine(neo4j_settings)
        metrics = engine.get_metrics()

        assert metrics["sync_success"] == 0
        assert metrics["sync_failure"] == 0
        assert metrics["last_failure_at"] is None
        assert metrics["last_failure_error"] is None
        assert "circuit_state" in metrics

    async def test_sync_entity_success_increments_counter(self, neo4j_settings):
        from src.services.graph_engine import GraphEngine

        engine = GraphEngine(neo4j_settings)
        session = _make_mock_session()
        engine._driver = _make_mock_driver(session)

        await engine.sync_entity(
            entity_id="ent_001",
            entity_type="person",
            name="Alice",
            user_id="usr_001",
        )

        metrics = engine.get_metrics()
        assert metrics["sync_success"] == 1
        assert metrics["sync_failure"] == 0

    async def test_sync_entity_failure_increments_counter(self, neo4j_settings):
        from src.services.graph_engine import GraphEngine

        engine = GraphEngine(neo4j_settings)
        session = _make_mock_session()
        session.run = AsyncMock(side_effect=Exception("timeout"))
        engine._driver = _make_mock_driver(session)

        await engine.sync_entity(
            entity_id="ent_001",
            entity_type="person",
            name="Alice",
            user_id="usr_001",
        )

        metrics = engine.get_metrics()
        assert metrics["sync_failure"] == 1
        assert metrics["sync_success"] == 0
        assert metrics["last_failure_at"] is not None
        assert "timeout" in metrics["last_failure_error"]

    async def test_sync_relationship_success_increments_counter(self, neo4j_settings):
        from src.services.graph_engine import GraphEngine

        engine = GraphEngine(neo4j_settings)
        session = _make_mock_session()
        engine._driver = _make_mock_driver(session)

        await engine.sync_relationship(
            relation_id="rel_001",
            from_entity_id="ent_001",
            to_entity_id="ent_002",
            relation_type="works_on",
            user_id="usr_001",
        )

        metrics = engine.get_metrics()
        assert metrics["sync_success"] == 1
        assert metrics["sync_failure"] == 0

    async def test_sync_relationship_failure_increments_counter(self, neo4j_settings):
        from src.services.graph_engine import GraphEngine

        engine = GraphEngine(neo4j_settings)
        session = _make_mock_session()
        session.run = AsyncMock(side_effect=Exception("neo4j down"))
        engine._driver = _make_mock_driver(session)

        await engine.sync_relationship(
            relation_id="rel_001",
            from_entity_id="ent_001",
            to_entity_id="ent_002",
            relation_type="works_on",
            user_id="usr_001",
        )

        metrics = engine.get_metrics()
        assert metrics["sync_failure"] == 1
        assert "neo4j down" in metrics["last_failure_error"]

    async def test_circuit_state_in_metrics(self, neo4j_settings):
        from src.services.graph_engine import GraphEngine

        engine = GraphEngine(neo4j_settings)
        metrics = engine.get_metrics()
        assert metrics["circuit_state"] == "closed"

    async def test_metrics_accumulate_across_calls(self, neo4j_settings):
        from src.services.graph_engine import GraphEngine

        engine = GraphEngine(neo4j_settings)
        session = _make_mock_session()
        engine._driver = _make_mock_driver(session)

        # Two successes
        for _ in range(2):
            await engine.sync_entity("ent_001", "person", "Alice", "usr_001")

        # One failure
        session.run = AsyncMock(side_effect=Exception("boom"))
        await engine.sync_entity("ent_002", "person", "Bob", "usr_001")

        metrics = engine.get_metrics()
        assert metrics["sync_success"] == 2
        assert metrics["sync_failure"] == 1


# ---------------------------------------------------------------------------
# VectorStore health()
# ---------------------------------------------------------------------------


class TestVectorStoreHealth:
    async def test_health_disabled_when_no_url(self, qdrant_settings_disabled):
        from src.services.vector_store import VectorStore

        store = VectorStore(qdrant_settings_disabled)
        result = await store.health()
        assert result["status"] == "disabled"
        assert result["configured"] is False

    async def test_health_healthy(self, qdrant_settings):
        from src.services.vector_store import VectorStore

        store = VectorStore(qdrant_settings)

        mock_collections = MagicMock()
        mock_collections.collections = ["memories", "entities", "events"]
        mock_client = AsyncMock()
        mock_client.get_collections = AsyncMock(return_value=mock_collections)
        store._client = mock_client

        result = await store.health()

        assert result["status"] == "healthy"
        assert result["configured"] is True
        assert result["collections"] == 3

    async def test_health_unreachable_when_client_raises(self, qdrant_settings):
        from src.services.vector_store import VectorStore

        store = VectorStore(qdrant_settings)
        mock_client = AsyncMock()
        mock_client.get_collections = AsyncMock(side_effect=Exception("refused"))
        # Override _get_client so health() gets the mock without health-check reconnect
        store._get_client = AsyncMock(return_value=mock_client)

        result = await store.health()

        assert result["status"] == "unreachable"
        assert result["configured"] is True
        assert "refused" in result["error"]

    async def test_health_unreachable_when_no_client(self, qdrant_settings):
        from src.services.vector_store import VectorStore

        store = VectorStore(qdrant_settings)
        store._get_client = AsyncMock(return_value=None)

        result = await store.health()

        assert result["status"] == "unreachable"
        assert result["configured"] is True


# ---------------------------------------------------------------------------
# VectorStore get_metrics()
# ---------------------------------------------------------------------------


class TestVectorStoreMetrics:
    async def test_initial_metrics_zero(self, qdrant_settings):
        from src.services.vector_store import VectorStore

        store = VectorStore(qdrant_settings)
        metrics = store.get_metrics()

        assert metrics["upsert_success"] == 0
        assert metrics["upsert_failure"] == 0
        assert metrics["delete_success"] == 0
        assert metrics["delete_failure"] == 0

    async def test_upsert_success_increments_counter(self, qdrant_settings):
        from src.services.vector_store import VectorStore

        store = VectorStore(qdrant_settings)
        mock_client = AsyncMock()
        store._client = mock_client
        # Patch _get_client to avoid health-check reconnect loop
        store._get_client = AsyncMock(return_value=mock_client)

        await store.upsert(
            collection="memories",
            id="mem_001",
            vector=[0.1] * 1024,
            payload={"text": "hello"},
            user_id="usr_001",
        )

        metrics = store.get_metrics()
        assert metrics["upsert_success"] == 1
        assert metrics["upsert_failure"] == 0

    async def test_upsert_failure_increments_counter(self, qdrant_settings):
        from src.services.vector_store import VectorStore

        store = VectorStore(qdrant_settings)
        mock_client = AsyncMock()
        mock_client.upsert = AsyncMock(side_effect=Exception("qdrant down"))
        store._get_client = AsyncMock(return_value=mock_client)

        with pytest.raises(Exception, match="qdrant down"):
            await store.upsert(
                collection="memories",
                id="mem_001",
                vector=[0.1] * 1024,
                payload={"text": "hello"},
                user_id="usr_001",
            )

        metrics = store.get_metrics()
        assert metrics["upsert_failure"] == 1
        assert metrics["upsert_success"] == 0

    async def test_delete_success_increments_counter(self, qdrant_settings):
        from src.services.vector_store import VectorStore

        store = VectorStore(qdrant_settings)
        mock_client = AsyncMock()
        store._get_client = AsyncMock(return_value=mock_client)

        await store.delete(collection="memories", id="mem_001")

        metrics = store.get_metrics()
        assert metrics["delete_success"] == 1
        assert metrics["delete_failure"] == 0

    async def test_delete_failure_increments_counter(self, qdrant_settings):
        from src.services.vector_store import VectorStore

        store = VectorStore(qdrant_settings)
        mock_client = AsyncMock()
        mock_client.delete = AsyncMock(side_effect=Exception("delete failed"))
        store._get_client = AsyncMock(return_value=mock_client)

        with pytest.raises(Exception, match="delete failed"):
            await store.delete(collection="memories", id="mem_001")

        metrics = store.get_metrics()
        assert metrics["delete_failure"] == 1
        assert metrics["delete_success"] == 0

    async def test_metrics_return_copy(self, qdrant_settings):
        """get_metrics() returns a copy — mutations don't affect internal state."""
        from src.services.vector_store import VectorStore

        store = VectorStore(qdrant_settings)
        metrics = store.get_metrics()
        metrics["upsert_success"] = 999

        assert store.get_metrics()["upsert_success"] == 0
