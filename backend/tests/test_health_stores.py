"""Tests for GET /v1/health/stores endpoint via _build_store_health helper."""

from unittest.mock import AsyncMock, MagicMock

from src.api.routes_health import _build_store_health
from tests.conftest import make_mock_settings


class TestBuildStoreHealth:
    async def test_all_disabled(self):
        """All URLs empty → all services report disabled, no degradation."""
        settings = make_mock_settings(neo4j_url="", qdrant_url="", redis_url="")
        db = AsyncMock()
        db.execute = AsyncMock(return_value=MagicMock(scalar=MagicMock(return_value=0)))

        result = await _build_store_health(
            settings=settings,
            graph_engine=None,
            vector_store=None,
            redis=None,
            db=db,
        )

        assert result["neo4j"] == {"status": "disabled", "configured": False}
        assert result["qdrant"] == {"status": "disabled", "configured": False}
        assert result["postgres"]["status"] == "healthy"
        assert result["redis"] == {"status": "disabled"}
        assert result["degraded_services"] == []

    async def test_healthy_graph_engine(self):
        """Mock graph_engine with health() + get_metrics() → healthy with sync_stats."""
        settings = make_mock_settings(
            neo4j_url="bolt://localhost:7687", qdrant_url="", redis_url=""
        )

        graph_engine = MagicMock()
        graph_engine.health = AsyncMock(
            return_value={"status": "healthy", "configured": True, "latency_ms": 12}
        )
        graph_engine.get_metrics = MagicMock(return_value={"nodes_synced": 42, "edges_synced": 100})

        db = AsyncMock()
        db.execute = AsyncMock(return_value=MagicMock(scalar=MagicMock(return_value=3)))

        result = await _build_store_health(
            settings=settings,
            graph_engine=graph_engine,
            vector_store=None,
            redis=None,
            db=db,
        )

        assert result["neo4j"]["status"] == "healthy"
        assert result["neo4j"]["sync_stats"] == {"nodes_synced": 42, "edges_synced": 100}
        assert result["postgres"]["status"] == "healthy"
        assert result["postgres"]["pending_dlq"] == 3
        assert result["degraded_services"] == []

    async def test_healthy_vector_store(self):
        """Mock vector_store with health() + get_metrics() → healthy with metrics."""
        settings = make_mock_settings(
            neo4j_url="", qdrant_url="http://localhost:6333", redis_url=""
        )

        vector_store = MagicMock()
        vector_store.health = AsyncMock(
            return_value={"status": "healthy", "configured": True, "collections": 6}
        )
        vector_store.get_metrics = MagicMock(
            return_value={"vectors_indexed": 1500, "search_latency_ms": 8}
        )

        db = AsyncMock()
        db.execute = AsyncMock(return_value=MagicMock(scalar=MagicMock(return_value=0)))

        result = await _build_store_health(
            settings=settings,
            graph_engine=None,
            vector_store=vector_store,
            redis=None,
            db=db,
        )

        assert result["qdrant"]["status"] == "healthy"
        assert result["qdrant"]["metrics"] == {"vectors_indexed": 1500, "search_latency_ms": 8}
        assert result["degraded_services"] == []

    async def test_configured_but_unreachable(self):
        """URLs set but services None → unreachable status and listed in degraded_services."""
        settings = make_mock_settings(
            neo4j_url="bolt://localhost:7687",
            qdrant_url="http://localhost:6333",
            redis_url="redis://localhost:6379",
        )

        db = AsyncMock()
        db.execute = AsyncMock(return_value=MagicMock(scalar=MagicMock(return_value=0)))

        result = await _build_store_health(
            settings=settings,
            graph_engine=None,
            vector_store=None,
            redis=None,
            db=db,
        )

        assert result["neo4j"]["status"] == "unreachable"
        assert result["neo4j"]["configured"] is True
        assert result["qdrant"]["status"] == "unreachable"
        assert result["qdrant"]["configured"] is True
        assert result["redis"]["status"] == "unreachable"
        assert set(result["degraded_services"]) == {"neo4j", "qdrant"}

    async def test_healthy_redis(self):
        """Mock redis.ping() → healthy redis status."""
        settings = make_mock_settings(
            neo4j_url="", qdrant_url="", redis_url="redis://localhost:6379"
        )

        redis = AsyncMock()
        redis.ping = AsyncMock(return_value=True)

        db = AsyncMock()
        db.execute = AsyncMock(return_value=MagicMock(scalar=MagicMock(return_value=0)))

        result = await _build_store_health(
            settings=settings,
            graph_engine=None,
            vector_store=None,
            redis=redis,
            db=db,
        )

        assert result["redis"] == {"status": "healthy"}
        assert result["degraded_services"] == []

    async def test_redis_ping_fails(self):
        """Redis present but ping() raises → unreachable."""
        settings = make_mock_settings(
            neo4j_url="", qdrant_url="", redis_url="redis://localhost:6379"
        )

        redis = AsyncMock()
        redis.ping = AsyncMock(side_effect=ConnectionError("refused"))

        db = AsyncMock()
        db.execute = AsyncMock(return_value=MagicMock(scalar=MagicMock(return_value=0)))

        result = await _build_store_health(
            settings=settings,
            graph_engine=None,
            vector_store=None,
            redis=redis,
            db=db,
        )

        assert result["redis"] == {"status": "unreachable"}

    async def test_postgres_unreachable_on_db_error(self):
        """DB execute raises → postgres shows unreachable."""
        settings = make_mock_settings(neo4j_url="", qdrant_url="", redis_url="")

        db = AsyncMock()
        db.execute = AsyncMock(side_effect=Exception("connection lost"))

        result = await _build_store_health(
            settings=settings,
            graph_engine=None,
            vector_store=None,
            redis=None,
            db=db,
        )

        assert result["postgres"] == {"status": "unreachable"}

    # --- deep_runtime checkpointer health ---

    async def test_deep_runtime_degraded(self):
        """runtime=deep + degraded flag → status=degraded, deep_checkpointer listed."""
        settings = make_mock_settings(neo4j_url="", qdrant_url="", redis_url="", runtime="deep")
        db = AsyncMock()
        db.execute = AsyncMock(return_value=MagicMock(scalar=MagicMock(return_value=0)))

        result = await _build_store_health(
            settings=settings,
            graph_engine=None,
            vector_store=None,
            redis=None,
            db=db,
            deep_checkpointer_degraded=True,
        )

        assert result["deep_runtime"]["status"] == "degraded"
        assert result["deep_runtime"]["durable"] is False
        assert "deep_checkpointer" in result["degraded_services"]

    async def test_deep_runtime_healthy(self):
        """runtime=deep + not degraded → deep_runtime.status=healthy, NOT in degraded_services."""
        settings = make_mock_settings(neo4j_url="", qdrant_url="", redis_url="", runtime="deep")
        db = AsyncMock()
        db.execute = AsyncMock(return_value=MagicMock(scalar=MagicMock(return_value=0)))

        result = await _build_store_health(
            settings=settings,
            graph_engine=None,
            vector_store=None,
            redis=None,
            db=db,
            deep_checkpointer_degraded=False,
        )

        assert result["deep_runtime"]["status"] == "healthy"
        assert result["deep_runtime"]["durable"] is True
        assert "deep_checkpointer" not in result["degraded_services"]

    async def test_deep_runtime_disabled_on_legacy(self):
        """runtime=legacy (default) → deep_runtime.status=disabled, NOT in degraded_services."""
        settings = make_mock_settings(neo4j_url="", qdrant_url="", redis_url="", runtime="legacy")
        db = AsyncMock()
        db.execute = AsyncMock(return_value=MagicMock(scalar=MagicMock(return_value=0)))

        result = await _build_store_health(
            settings=settings,
            graph_engine=None,
            vector_store=None,
            redis=None,
            db=db,
        )

        assert result["deep_runtime"]["status"] == "disabled"
        assert "deep_checkpointer" not in result["degraded_services"]
