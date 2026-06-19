"""Tests for Fix-9: Data Layer Resilience.

Covers Neo4j exception handlers, dead parameter fixes, Qdrant exception specificity,
conversation embedding completeness, and TriSearch collection mapping.
"""

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
    setattr(s, "neo4j_password", "neo4j-local")
    return s


@pytest.fixture
def mock_session():
    session = AsyncMock()
    session.__aenter__ = AsyncMock(return_value=session)
    session.__aexit__ = AsyncMock(return_value=False)
    return session


@pytest.fixture
def mock_driver(mock_session):
    driver = MagicMock()
    driver.session.return_value = mock_session
    return driver


# ---------------------------------------------------------------------------
# Phase 1: Neo4j Exception Handlers
# ---------------------------------------------------------------------------


class TestTraverseExceptionHandler:
    """Task 1.1: traverse wrapped in try/except."""

    async def test_traverse_returns_empty_on_neo4j_error(
        self, neo4j_settings, mock_driver, mock_session
    ):
        from src.services.graph_engine import GraphEngine

        mock_session.run = AsyncMock(side_effect=Exception("Neo4j unavailable"))
        engine = GraphEngine(neo4j_settings)
        engine._driver = mock_driver

        result = await engine.traverse(entity_id="ent_1", user_id="usr_1")
        assert result == {"nodes": [], "edges": []}

    async def test_traverse_no_exception_propagated(
        self, neo4j_settings, mock_driver, mock_session
    ):
        from src.services.graph_engine import GraphEngine

        mock_session.run = AsyncMock(side_effect=RuntimeError("connection reset"))
        engine = GraphEngine(neo4j_settings)
        engine._driver = mock_driver

        # Should not raise
        result = await engine.traverse(entity_id="ent_1", user_id="usr_1")
        assert isinstance(result, dict)


class TestFindPathExceptionHandler:
    """Task 1.2: find_path wrapped in try/except."""

    async def test_find_path_returns_empty_on_error(
        self, neo4j_settings, mock_driver, mock_session
    ):
        from src.services.graph_engine import GraphEngine

        mock_session.run = AsyncMock(side_effect=Exception("Neo4j down"))
        engine = GraphEngine(neo4j_settings)
        engine._driver = mock_driver

        result = await engine.find_path(
            from_entity_id="ent_1", to_entity_id="ent_2", user_id="usr_1"
        )
        assert result == []


class TestGetRelatedPeopleExceptionHandler:
    """Task 1.3: get_related_people wrapped in try/except."""

    async def test_get_related_people_returns_empty_on_error(
        self, neo4j_settings, mock_driver, mock_session
    ):
        from src.services.graph_engine import GraphEngine

        mock_session.run = AsyncMock(side_effect=Exception("Neo4j down"))
        engine = GraphEngine(neo4j_settings)
        engine._driver = mock_driver

        result = await engine.get_related_people(entity_id="ent_1", user_id="usr_1")
        assert result == []


class TestLogLevelFixes:
    """Task 1.4: traverse_weighted and traverse_temporal use logger.warning."""

    async def test_traverse_weighted_logs_warning(self, neo4j_settings, mock_driver, mock_session):
        from src.services.graph_engine import GraphEngine

        mock_session.run = AsyncMock(side_effect=Exception("fail"))
        engine = GraphEngine(neo4j_settings)
        engine._driver = mock_driver

        with patch("src.services.graph_engine.logger") as mock_logger:
            await engine.traverse_weighted(entity_id="ent_1", user_id="usr_1")
            mock_logger.warning.assert_called()

    async def test_traverse_temporal_logs_warning(self, neo4j_settings, mock_driver, mock_session):
        from src.services.graph_engine import GraphEngine

        mock_session.run = AsyncMock(side_effect=Exception("fail"))
        engine = GraphEngine(neo4j_settings)
        engine._driver = mock_driver

        with patch("src.services.graph_engine.logger") as mock_logger:
            await engine.traverse_temporal(entity_id="ent_1", user_id="usr_1")
            mock_logger.warning.assert_called()


# ---------------------------------------------------------------------------
# Phase 2: Dead Parameter and Unbounded Traversal
# ---------------------------------------------------------------------------


class TestStaleRelationshipsDaysParam:
    """Task 2.1: get_stale_relationships uses days parameter."""

    async def test_passes_cutoff_date_to_cypher(self, neo4j_settings, mock_driver, mock_session):
        from src.services.graph_engine import GraphEngine

        mock_result = AsyncMock()
        mock_result.data = AsyncMock(return_value=[])
        mock_session.run = AsyncMock(return_value=mock_result)

        engine = GraphEngine(neo4j_settings)
        engine._driver = mock_driver

        await engine.get_stale_relationships(user_id="usr_1", days=7)

        call_args = mock_session.run.call_args
        cypher = call_args[0][0]
        params = call_args[1]

        assert "$cutoff_date" in cypher
        assert "cutoff_date" in params

    async def test_returns_empty_on_error(self, neo4j_settings, mock_driver, mock_session):
        from src.services.graph_engine import GraphEngine

        mock_session.run = AsyncMock(side_effect=Exception("Neo4j down"))
        engine = GraphEngine(neo4j_settings)
        engine._driver = mock_driver

        result = await engine.get_stale_relationships(user_id="usr_1", days=7)
        assert result == []


class TestDetectCommunitiesBounded:
    """Task 2.2: detect_communities uses bounded traversal *1..3."""

    async def test_cypher_contains_bounded_depth(self, neo4j_settings, mock_driver, mock_session):
        from src.services.graph_engine import GraphEngine

        mock_result = AsyncMock()
        mock_result.data = AsyncMock(return_value=[])
        mock_session.run = AsyncMock(return_value=mock_result)

        engine = GraphEngine(neo4j_settings)
        engine._driver = mock_driver

        await engine.detect_communities(user_id="usr_1")

        call_args = mock_session.run.call_args
        cypher = call_args[0][0]

        assert "*1..3" in cypher, f"Expected *1..3 in cypher, got: {cypher}"

    async def test_returns_empty_on_error(self, neo4j_settings, mock_driver, mock_session):
        from src.services.graph_engine import GraphEngine

        mock_session.run = AsyncMock(side_effect=Exception("Neo4j down"))
        engine = GraphEngine(neo4j_settings)
        engine._driver = mock_driver

        result = await engine.detect_communities(user_id="usr_1")
        assert result == []


# ---------------------------------------------------------------------------
# Phase 3: Qdrant Exception Specificity
# ---------------------------------------------------------------------------


class TestEnsureCollectionsExceptionHandling:
    """Task 3.1: ensure_collections uses specific UnexpectedResponse."""

    async def test_generic_exception_logs_warning(self):
        from src.services.vector_store import VectorStore

        settings = make_mock_settings(qdrant_url="http://localhost:6333")
        store = VectorStore(settings=settings)

        mock_client = AsyncMock()
        mock_client.get_collection = AsyncMock(side_effect=RuntimeError("unexpected"))
        mock_client.get_collections = AsyncMock()
        store._client = mock_client

        with patch("src.services.vector_store.logger") as mock_logger:
            await store.ensure_collections()
            assert mock_logger.warning.called


class TestEnsureIndexesExceptionHandling:
    """Task 3.2: ensure_indexes uses specific UnexpectedResponse."""

    async def test_generic_exception_logs_warning(self):
        from src.services.vector_store import VectorStore

        settings = make_mock_settings(qdrant_url="http://localhost:6333")
        store = VectorStore(settings=settings)

        mock_client = AsyncMock()
        mock_client.create_payload_index = AsyncMock(side_effect=RuntimeError("unexpected error"))
        mock_client.get_collections = AsyncMock()
        store._client = mock_client

        with patch("src.services.vector_store.logger") as mock_logger:
            await store.ensure_indexes()
            assert mock_logger.warning.called

    async def test_unexpected_response_silently_handled(self):
        from qdrant_client.http.exceptions import UnexpectedResponse

        from src.services.vector_store import VectorStore

        settings = make_mock_settings(qdrant_url="http://localhost:6333")
        store = VectorStore(settings=settings)

        mock_client = AsyncMock()
        mock_client.create_payload_index = AsyncMock(
            side_effect=UnexpectedResponse(
                status_code=409, reason_phrase="Conflict", content=b"", headers={}
            )
        )
        mock_client.get_collections = AsyncMock()
        store._client = mock_client

        with patch("src.services.vector_store.logger") as mock_logger:
            await store.ensure_indexes()
            # Should NOT log warning for UnexpectedResponse
            mock_logger.warning.assert_not_called()


# ---------------------------------------------------------------------------
# Phase 4: Conversation Embedding Completeness
# ---------------------------------------------------------------------------


class TestConversationEmbedding:
    """Tasks 4.1-4.4: summarize_history returns summary text."""

    async def test_summary_returned(self):
        """Verify ContextAssembler._summarize_history returns the Claude summary text."""
        from src.orchestrator.context_assembler import ContextAssembler

        settings = make_mock_settings()
        mock_client = MagicMock()

        mock_response = MagicMock()
        mock_text_block = MagicMock()
        mock_text_block.type = "text"
        mock_text_block.text = "Discussion about project planning."
        mock_response.content = [mock_text_block]
        mock_client.messages = AsyncMock()
        mock_client.messages.create = AsyncMock(return_value=mock_response)

        ctx = ContextAssembler(
            settings=settings,
            services=None,
            db_factory_provider=MagicMock(),
            client=mock_client,
        )
        summary = await ctx._summarize_history(
            lines=["User: hello", "Assistant: hi"],
            conversation_id="conv_123",
            user_id="usr_456",
        )

        assert summary == "Discussion about project planning."


# ---------------------------------------------------------------------------
# Phase 5: TriSearch Collection Mapping (regression test)
# ---------------------------------------------------------------------------


class TestTriSearchCollectionMapping:
    """Task 5.1: _collection_to_type includes conversations and approvals."""

    def test_conversations_mapped(self):
        from src.services.tri_search import _collection_to_type

        assert _collection_to_type("conversations") == "conversation"

    def test_approvals_mapped(self):
        from src.services.tri_search import _collection_to_type

        assert _collection_to_type("approvals") == "approval"

    def test_unknown_collection_returns_name(self):
        from src.services.tri_search import _collection_to_type

        assert _collection_to_type("unknown_col") == "unknown_col"
