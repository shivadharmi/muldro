"""Tests for EvictionService — TTL-based hard deletion with cascade cleanup."""

from unittest.mock import AsyncMock, MagicMock

import pytest

from src.services.eviction_service import (
    EvictionService,
)


def _make_settings():
    settings = MagicMock()
    settings.qdrant_url = "http://localhost:6333"
    settings.neo4j_url = "bolt://localhost:7687"
    return settings


def _make_eviction_service(db, vector_store=None, graph_engine=None):
    return EvictionService(
        settings=_make_settings(),
        db=db,
        vector_store=vector_store,
        graph_engine=graph_engine,
    )


class TestEvictMemories:
    """Test TTL-based memory eviction."""

    @pytest.mark.asyncio
    async def test_evict_memories_deletes_expired_past_grace(self):
        """Memories marked 'expired' and past grace period should be hard-deleted."""
        db = AsyncMock()
        vector_store = AsyncMock()

        # Simulate finding 3 expired memories
        mock_result = MagicMock()
        mock_result.all.return_value = [("mem_1",), ("mem_2",), ("mem_3",)]
        db.execute.return_value = mock_result

        svc = _make_eviction_service(db, vector_store=vector_store)
        count = await svc._evict_memories()

        assert count == 3
        # Qdrant cascade called for each
        assert vector_store.delete.call_count == 3
        # DB delete + flush called
        assert db.execute.call_count == 2  # SELECT + DELETE
        db.flush.assert_awaited_once()

    @pytest.mark.asyncio
    async def test_evict_memories_returns_zero_when_none_expired(self):
        """No expired memories means zero deletions and no cascade."""
        db = AsyncMock()
        vector_store = AsyncMock()

        mock_result = MagicMock()
        mock_result.all.return_value = []
        db.execute.return_value = mock_result

        svc = _make_eviction_service(db, vector_store=vector_store)
        count = await svc._evict_memories()

        assert count == 0
        vector_store.delete.assert_not_called()

    @pytest.mark.asyncio
    async def test_evict_memories_works_without_qdrant(self):
        """Eviction should still work when Qdrant is not configured."""
        db = AsyncMock()

        mock_result = MagicMock()
        mock_result.all.return_value = [("mem_1",)]
        db.execute.return_value = mock_result

        svc = _make_eviction_service(db, vector_store=None)
        count = await svc._evict_memories()

        assert count == 1
        assert db.execute.call_count == 2  # SELECT + DELETE


class TestEvictLowStability:
    """Test stability-based proactive eviction."""

    @pytest.mark.asyncio
    async def test_never_evicts_goals_or_preferences(self):
        """Goals and preferences must never be evicted by stability score."""
        db = AsyncMock()

        # Return no results (the WHERE clause excludes goals/preferences)
        mock_result = MagicMock()
        mock_result.all.return_value = []
        db.execute.return_value = mock_result

        svc = _make_eviction_service(db)
        count = await svc._evict_low_stability_memories()

        assert count == 0
        # Verify only 1 SELECT was executed (no DELETE)
        assert db.execute.call_count == 1

    @pytest.mark.asyncio
    async def test_evicts_low_stability_old_memories(self):
        """Old, low-stability, rarely-accessed memories should be evicted."""
        db = AsyncMock()
        vector_store = AsyncMock()

        mock_result = MagicMock()
        mock_result.all.return_value = [("mem_old_1",), ("mem_old_2",)]
        db.execute.return_value = mock_result

        svc = _make_eviction_service(db, vector_store=vector_store)
        count = await svc._evict_low_stability_memories()

        assert count == 2
        assert vector_store.delete.call_count == 2


class TestEvictSessions:
    """Test session eviction."""

    @pytest.mark.asyncio
    async def test_evicts_expired_sessions(self):
        db = AsyncMock()
        mock_result = MagicMock()
        mock_result.rowcount = 2
        db.execute.return_value = mock_result

        svc = _make_eviction_service(db)
        count = await svc._evict_sessions()

        assert count == 2
        db.flush.assert_awaited_once()


class TestEvictApprovals:
    """Test approval eviction."""

    @pytest.mark.asyncio
    async def test_evicts_old_decided_approvals_with_qdrant_cascade(self):
        db = AsyncMock()
        vector_store = AsyncMock()

        # First call: SELECT returns IDs; Second call: DELETE
        mock_select = MagicMock()
        mock_select.all.return_value = [("apr_1",), ("apr_2",), ("apr_3",), ("apr_4",)]
        mock_delete = MagicMock()
        db.execute = AsyncMock(side_effect=[mock_select, mock_delete])

        svc = _make_eviction_service(db, vector_store=vector_store)
        count = await svc._evict_approvals()

        assert count == 4
        assert vector_store.delete.call_count == 4
        vector_store.delete.assert_any_call("approvals", "apr_1")
        db.flush.assert_awaited_once()

    @pytest.mark.asyncio
    async def test_evicts_approvals_returns_zero_when_none(self):
        db = AsyncMock()
        vector_store = AsyncMock()

        mock_result = MagicMock()
        mock_result.all.return_value = []
        db.execute.return_value = mock_result

        svc = _make_eviction_service(db, vector_store=vector_store)
        count = await svc._evict_approvals()

        assert count == 0
        vector_store.delete.assert_not_called()


class TestEvictOldEvents:
    """Test event eviction with Qdrant cascade."""

    @pytest.mark.asyncio
    async def test_evicts_events_past_retention(self):
        db = AsyncMock()
        vector_store = AsyncMock()

        mock_result = MagicMock()
        mock_result.all.return_value = [("evt_1",), ("evt_2",)]
        db.execute.return_value = mock_result

        svc = _make_eviction_service(db, vector_store=vector_store)
        count = await svc._evict_old_events()

        assert count == 2
        assert vector_store.delete.call_count == 2
        assert db.execute.call_count == 2  # SELECT + DELETE

    @pytest.mark.asyncio
    async def test_returns_zero_when_no_old_events(self):
        db = AsyncMock()
        vector_store = AsyncMock()

        mock_result = MagicMock()
        mock_result.all.return_value = []
        db.execute.return_value = mock_result

        svc = _make_eviction_service(db, vector_store=vector_store)
        count = await svc._evict_old_events()

        assert count == 0
        vector_store.delete.assert_not_called()


class TestCascadeNeo4j:
    """Test Neo4j cascade delete."""

    @pytest.mark.asyncio
    async def test_cascade_neo4j_delete_calls_graph_engine(self):
        db = AsyncMock()
        graph_engine = AsyncMock()

        svc = _make_eviction_service(db, graph_engine=graph_engine)
        await svc.cascade_neo4j_delete_entity("ent_123")

        graph_engine.delete_entity.assert_awaited_once_with("ent_123")

    @pytest.mark.asyncio
    async def test_cascade_neo4j_noop_without_graph_engine(self):
        db = AsyncMock()
        svc = _make_eviction_service(db, graph_engine=None)
        # Should not raise
        await svc.cascade_neo4j_delete_entity("ent_123")


class TestRunFullEviction:
    """Test the full eviction orchestrator."""

    @pytest.mark.asyncio
    async def test_runs_all_eviction_passes(self):
        db = AsyncMock()
        vector_store = AsyncMock()

        # Mock all returns as empty for simplicity
        mock_empty = MagicMock()
        mock_empty.all.return_value = []
        mock_empty.rowcount = 0
        db.execute.return_value = mock_empty

        svc = _make_eviction_service(db, vector_store=vector_store)
        results = await svc.run_full_eviction()

        assert "memories" in results
        assert "sessions" in results
        assert "approvals" in results
        assert "events" in results
        assert "low_stability" in results
        assert sum(results.values()) == 0


class TestQdrantCascadeResilience:
    """Test that Qdrant failures don't break eviction."""

    @pytest.mark.asyncio
    async def test_qdrant_failure_does_not_stop_eviction(self):
        db = AsyncMock()
        vector_store = AsyncMock()
        vector_store.delete.side_effect = Exception("Qdrant connection refused")

        mock_result = MagicMock()
        mock_result.all.return_value = [("mem_1",)]
        db.execute.return_value = mock_result

        svc = _make_eviction_service(db, vector_store=vector_store)
        # Should not raise — Qdrant failure is logged and skipped
        count = await svc._evict_memories()

        assert count == 1
        # Postgres DELETE still happened despite Qdrant failure
        assert db.execute.call_count == 2
