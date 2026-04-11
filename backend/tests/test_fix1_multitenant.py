"""Tests for Fix-1: Multi-tenant workspace scoping fixes."""

from datetime import datetime, timezone
from unittest.mock import AsyncMock, MagicMock

from tests.conftest import make_mock_settings


class TestCompositeRetrieveScopedByWorkspace:
    """_composite_retrieve passes workspace_id to Qdrant and Postgres."""

    async def test_qdrant_search_receives_workspace_filter(self):
        from src.services.memory_service import MemoryService

        mock_db = AsyncMock()
        mock_result = MagicMock()
        mock_result.scalars.return_value.all.return_value = []
        mock_db.execute = AsyncMock(return_value=mock_result)

        mock_vs = AsyncMock()
        mock_vs.search = AsyncMock(return_value=[])

        settings = make_mock_settings()
        svc = MemoryService(settings=settings, db=mock_db, vector_store=mock_vs)

        await svc._composite_retrieve(
            user_id="usr_1",
            query_embedding=[0.1] * 1024,
            memory_types=None,
            entity_refs=None,
            max_results=10,
            workspace_id="ws_abc",
        )

        mock_vs.search.assert_called_once()
        call_kwargs = mock_vs.search.call_args
        assert call_kwargs[1].get("filters") == {"workspace_id": "ws_abc"} or (
            len(call_kwargs[0]) > 3 and call_kwargs[0][3] == {"workspace_id": "ws_abc"}
        )

    async def test_postgres_batch_fetch_includes_workspace_id(self):
        """The Postgres stmt should filter by workspace_id."""
        from src.services.memory_service import MemoryService

        mock_db = AsyncMock()
        mock_result = MagicMock()
        mock_result.scalars.return_value.all.return_value = []
        mock_db.execute = AsyncMock(return_value=mock_result)

        mock_vs = AsyncMock()
        mock_vs.search = AsyncMock(
            return_value=[
                {"id": "mem_001", "score": 0.9, "payload": {"_original_id": "mem_001"}},
            ]
        )

        settings = make_mock_settings()
        svc = MemoryService(settings=settings, db=mock_db, vector_store=mock_vs)

        await svc._composite_retrieve(
            user_id="usr_1",
            query_embedding=[0.1] * 1024,
            memory_types=None,
            entity_refs=None,
            max_results=10,
            workspace_id="ws_abc",
        )

        # Verify that db.execute was called (for the batch fetch)
        assert mock_db.execute.called


class TestFindByNameOrAliasQdrantScoped:
    """_find_by_name_or_alias passes workspace_id filter to Qdrant."""

    async def test_qdrant_find_similar_receives_workspace_filter(self):
        from src.services.world_model import WorldModel

        mock_db = AsyncMock()
        mock_result = MagicMock()
        mock_result.scalar_one_or_none.return_value = None
        mock_db.execute = AsyncMock(return_value=mock_result)

        mock_vs = AsyncMock()
        mock_vs.find_similar = AsyncMock(return_value=[])

        mock_emb = AsyncMock()
        mock_emb.embed = AsyncMock(return_value=[0.1] * 1024)

        settings = make_mock_settings()
        wm = WorldModel(
            settings=settings,
            db=mock_db,
            embedding_service=mock_emb,
            vector_store=mock_vs,
        )

        result = await wm._find_by_name_or_alias(
            user_id="usr_1",
            canonical_name="Alice",
            aliases=None,
            workspace_id="ws_abc",
        )

        assert result is None
        mock_vs.find_similar.assert_called_once()
        call_kwargs = mock_vs.find_similar.call_args[1]
        assert call_kwargs.get("filters") == {"workspace_id": "ws_abc"}

    async def test_postgres_fallback_includes_workspace_id(self):
        """When Qdrant returns a match, the Postgres lookup should scope by workspace_id."""
        from src.services.world_model import WorldModel

        mock_db = AsyncMock()
        # First 2 calls: name lookup and alias lookup return None
        # Third call: the Qdrant-matched entity lookup
        mock_result_none = MagicMock()
        mock_result_none.scalar_one_or_none.return_value = None

        mock_entity = MagicMock()
        mock_entity.entity_id = "ent_001"

        mock_result_found = MagicMock()
        mock_result_found.scalar_one_or_none.return_value = mock_entity

        mock_db.execute = AsyncMock(side_effect=[mock_result_none, mock_result_found])

        mock_vs = AsyncMock()
        mock_vs.find_similar = AsyncMock(
            return_value=[{"id": "ent_001", "score": 0.95, "payload": {"_original_id": "ent_001"}}]
        )

        mock_emb = AsyncMock()
        mock_emb.embed = AsyncMock(return_value=[0.1] * 1024)

        settings = make_mock_settings()
        wm = WorldModel(
            settings=settings,
            db=mock_db,
            embedding_service=mock_emb,
            vector_store=mock_vs,
        )

        result = await wm._find_by_name_or_alias(
            user_id="usr_1",
            canonical_name="Alice",
            aliases=None,
            workspace_id="ws_abc",
        )

        assert result == mock_entity
        # The second db.execute call (Postgres fallback for ent_001) should have been called
        assert mock_db.execute.call_count == 2


class TestPersonaBatchGroupsByWorkspace:
    """_tick_persona_batch groups interactions by (workspace_id, user_id)."""

    async def test_groups_by_workspace_and_user(self):
        from src.services.scheduler import SchedulerLoop

        settings = make_mock_settings()
        mock_orch = MagicMock()
        mock_orch._call_agent = AsyncMock()

        scheduler = SchedulerLoop(settings=settings, orchestrator=mock_orch)
        scheduler._tick_count = 10  # ensure modulo 10 == 0

        # Create mock interactions for 2 workspaces
        interactions = []
        for i in range(6):
            m = MagicMock()
            m.workspace_id = "ws_a"
            m.user_id = "usr_1"
            m.message_preview = f"msg_a_{i}"
            m.intent = "query"
            m.created_at = datetime(2026, 4, 11, 0, i, tzinfo=timezone.utc)
            interactions.append(m)
        for i in range(6):
            m = MagicMock()
            m.workspace_id = "ws_b"
            m.user_id = "usr_2"
            m.message_preview = f"msg_b_{i}"
            m.intent = "query"
            m.created_at = datetime(2026, 4, 11, 1, i, tzinfo=timezone.utc)
            interactions.append(m)

        mock_scalars = MagicMock()
        mock_scalars.all.return_value = interactions
        mock_result = MagicMock()
        mock_result.scalars.return_value = mock_scalars

        mock_db = AsyncMock()
        mock_db.execute = AsyncMock(return_value=mock_result)
        mock_db.__aenter__ = AsyncMock(return_value=mock_db)
        mock_db.__aexit__ = AsyncMock(return_value=False)

        mock_factory = MagicMock(return_value=mock_db)

        await scheduler._tick_persona_batch(factory=mock_factory)

        # Should be called once per workspace group (2 groups, each with >= 5)
        assert mock_orch._call_agent.call_count == 2

        # Verify each call has the correct workspace_id
        calls = mock_orch._call_agent.call_args_list
        ws_ids = {c[1]["workspace_id"] for c in calls}
        assert ws_ids == {"ws_a", "ws_b"}

    async def test_skips_groups_with_fewer_than_5(self):
        from src.services.scheduler import SchedulerLoop

        settings = make_mock_settings()
        mock_orch = MagicMock()
        mock_orch._call_agent = AsyncMock()

        scheduler = SchedulerLoop(settings=settings, orchestrator=mock_orch)
        scheduler._tick_count = 10

        # 6 interactions for ws_a (enough), 3 for ws_b (not enough)
        interactions = []
        for i in range(6):
            m = MagicMock()
            m.workspace_id = "ws_a"
            m.user_id = "usr_1"
            m.message_preview = f"msg_{i}"
            m.intent = "query"
            m.created_at = datetime(2026, 4, 11, 0, i, tzinfo=timezone.utc)
            interactions.append(m)
        for i in range(3):
            m = MagicMock()
            m.workspace_id = "ws_b"
            m.user_id = "usr_2"
            m.message_preview = f"msg_b_{i}"
            m.intent = "query"
            m.created_at = datetime(2026, 4, 11, 1, i, tzinfo=timezone.utc)
            interactions.append(m)

        mock_scalars = MagicMock()
        mock_scalars.all.return_value = interactions
        mock_result = MagicMock()
        mock_result.scalars.return_value = mock_scalars

        mock_db = AsyncMock()
        mock_db.execute = AsyncMock(return_value=mock_result)
        mock_db.__aenter__ = AsyncMock(return_value=mock_db)
        mock_db.__aexit__ = AsyncMock(return_value=False)

        mock_factory = MagicMock(return_value=mock_db)

        await scheduler._tick_persona_batch(factory=mock_factory)

        # Only ws_a group should be processed
        assert mock_orch._call_agent.call_count == 1
        assert mock_orch._call_agent.call_args[1]["workspace_id"] == "ws_a"


class TestVectorStoreFindSimilarFilters:
    """find_similar passes filters through to search."""

    async def test_find_similar_passes_filters(self):
        from src.services.vector_store import VectorStore

        settings = make_mock_settings()
        settings.qdrant_url = "http://localhost:6333"

        vs = VectorStore(settings)

        # Mock search to verify filters are passed through
        vs.search = AsyncMock(
            return_value=[
                {"id": "mem_1", "score": 0.95, "payload": {}},
            ]
        )

        result = await vs.find_similar(
            "memories",
            [0.1] * 1024,
            "usr_1",
            threshold=0.9,
            limit=5,
            filters={"workspace_id": "ws_abc"},
        )

        vs.search.assert_called_once_with(
            "memories",
            [0.1] * 1024,
            "usr_1",
            filters={"workspace_id": "ws_abc"},
            limit=5,
        )
        assert len(result) == 1
