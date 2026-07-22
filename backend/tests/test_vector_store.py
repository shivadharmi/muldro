"""Tests for VectorStore service."""

import pytest

from src.services.vector_store import VectorStore
from tests.conftest import make_mock_settings


@pytest.fixture
def settings_no_qdrant():
    """Settings without Qdrant configured."""
    return make_mock_settings(qdrant_url="", qdrant_api_key="")


@pytest.fixture
def store_no_qdrant(settings_no_qdrant):
    return VectorStore(settings=settings_no_qdrant)


@pytest.mark.asyncio
async def test_get_client_returns_none_when_not_configured(store_no_qdrant):
    """Test _get_client returns None when qdrant_url not set."""
    client = await store_no_qdrant._get_client()
    assert client is None


@pytest.mark.asyncio
async def test_ensure_collections_no_op_without_client(store_no_qdrant):
    """Test ensure_collections is no-op when client not available."""
    # Should not raise
    await store_no_qdrant.ensure_collections()


@pytest.mark.asyncio
async def test_upsert_no_op_without_client(store_no_qdrant):
    """Test upsert is no-op when client not available."""
    # Should not raise
    await store_no_qdrant.upsert(
        collection="memories",
        id="mem_001",
        vector=[0.1] * 768,
        payload={"text": "test"},
        user_id="user_001",
    )


@pytest.mark.asyncio
async def test_search_returns_empty_without_client(store_no_qdrant):
    """Test search returns empty list when client not available."""
    results = await store_no_qdrant.search(
        collection="memories",
        query_vector=[0.1] * 768,
        user_id="user_001",
        limit=10,
    )
    assert results == []


@pytest.mark.asyncio
async def test_delete_no_op_without_client(store_no_qdrant):
    """Test delete is no-op when client not available."""
    # Should not raise
    await store_no_qdrant.delete(collection="memories", id="mem_001")


@pytest.mark.asyncio
async def test_hybrid_search_returns_empty_without_client(store_no_qdrant):
    """Test hybrid_search returns empty when client not available."""
    results = await store_no_qdrant.hybrid_search(
        user_id="user_001",
        query_vector=[0.1] * 768,
        collections=["memories", "entities"],
        limit=20,
    )
    assert results == []


@pytest.mark.asyncio
async def test_hybrid_search_merges_results():
    """Test hybrid_search merges results from multiple collections."""
    from unittest.mock import AsyncMock

    settings = make_mock_settings(qdrant_url="http://localhost:6333")
    store = VectorStore(settings=settings)

    # Mock the search method to return different results per collection
    async def mock_search(collection, query_vector, user_id, limit=10):
        if collection == "memories":
            return [
                {"id": "mem_001", "score": 0.9, "payload": {"text": "memory 1"}},
                {"id": "mem_002", "score": 0.7, "payload": {"text": "memory 2"}},
            ]
        elif collection == "entities":
            return [
                {"id": "ent_001", "score": 0.95, "payload": {"name": "entity 1"}},
                {"id": "ent_002", "score": 0.6, "payload": {"name": "entity 2"}},
            ]
        return []

    store.search = AsyncMock(side_effect=mock_search)

    results = await store.hybrid_search(
        user_id="user_001",
        query_vector=[0.1] * 768,
        collections=["memories", "entities"],
        limit=20,
    )

    # Should have results from both collections
    assert len(results) == 4

    # Should be sorted by score descending
    assert results[0]["score"] == 0.95
    assert results[1]["score"] == 0.9
    assert results[2]["score"] == 0.7
    assert results[3]["score"] == 0.6

    # Each result should have collection tag
    assert results[0]["collection"] == "entities"
    assert results[1]["collection"] == "memories"


@pytest.mark.asyncio
async def test_hybrid_search_respects_limit():
    """Test hybrid_search respects the limit parameter."""
    from unittest.mock import AsyncMock

    settings = make_mock_settings(qdrant_url="http://localhost:6333")
    store = VectorStore(settings=settings)

    async def mock_search(collection, query_vector, user_id, limit=10):
        return [
            {"id": f"{collection}_001", "score": 0.9, "payload": {}},
            {"id": f"{collection}_002", "score": 0.8, "payload": {}},
            {"id": f"{collection}_003", "score": 0.7, "payload": {}},
        ]

    store.search = AsyncMock(side_effect=mock_search)

    results = await store.hybrid_search(
        user_id="user_001",
        query_vector=[0.1] * 768,
        collections=["memories", "entities", "events"],
        limit=5,  # Should return only top 5
    )

    assert len(results) == 5
