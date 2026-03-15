"""Tests for SearchService."""

import pytest

from src.services.search_service import SearchService
from tests.conftest import make_mock_settings


@pytest.fixture
def settings_no_backends():
    """Settings without ES or Qdrant."""
    return make_mock_settings(elasticsearch_url="", qdrant_url="")


@pytest.fixture
def search_service_no_backends(settings_no_backends):
    return SearchService(settings=settings_no_backends, vector_store=None)


@pytest.mark.asyncio
async def test_get_es_returns_none_when_not_configured(search_service_no_backends):
    """Test _get_es returns None when elasticsearch_url not set."""
    es = await search_service_no_backends._get_es()
    assert es is None


@pytest.mark.asyncio
async def test_ensure_indices_no_op_without_es(search_service_no_backends):
    """Test ensure_indices is no-op when ES not available."""
    # Should not raise
    await search_service_no_backends.ensure_indices()


@pytest.mark.asyncio
async def test_index_event_no_op_without_backends(search_service_no_backends):
    """Test index_event is no-op when no backends configured."""
    # Should not raise
    await search_service_no_backends.index_event(
        event_id="evt_001",
        user_id="user_001",
        data={"title": "Test", "summary": "Test event"},
    )


@pytest.mark.asyncio
async def test_index_entity_no_op_without_backends(search_service_no_backends):
    """Test index_entity is no-op when no backends configured."""
    # Should not raise
    await search_service_no_backends.index_entity(
        entity_id="ent_001",
        user_id="user_001",
        data={"canonical_name": "Test Entity", "entity_type": "person"},
    )


@pytest.mark.asyncio
async def test_index_memory_no_op_without_backends(search_service_no_backends):
    """Test index_memory is no-op when no backends configured."""
    # Should not raise
    await search_service_no_backends.index_memory(
        memory_id="mem_001",
        user_id="user_001",
        data={"fact_text": "Test fact", "confidence": 0.8},
    )


@pytest.mark.asyncio
async def test_search_returns_empty_without_backends(search_service_no_backends):
    """Test search returns empty when no backends configured."""
    results = await search_service_no_backends.search(
        user_id="user_001",
        query="test query",
        scopes=["events", "entities"],
        limit=10,
    )
    assert results == []


def test_rrf_merge_static_method():
    """Test reciprocal rank fusion merges results correctly."""
    semantic = [
        {"id": "doc1", "score": 0.9, "payload": {"text": "semantic result 1"}},
        {"id": "doc2", "score": 0.8, "payload": {"text": "semantic result 2"}},
        {"id": "doc3", "score": 0.7, "payload": {"text": "semantic result 3"}},
    ]

    fulltext = [
        {"id": "doc2", "score": 10.5, "source": {"text": "fulltext result 2"}},
        {"id": "doc4", "score": 9.0, "source": {"text": "fulltext result 4"}},
        {"id": "doc1", "score": 8.5, "source": {"text": "fulltext result 1"}},
    ]

    merged = SearchService._rrf_merge(semantic, fulltext, limit=10, k=60)

    # Should have unique docs
    ids = [r["id"] for r in merged]
    assert len(ids) == 4
    assert len(set(ids)) == 4

    # Documents in both lists should rank higher
    # doc1 and doc2 appear in both, so they should be at the top
    top_two = {merged[0]["id"], merged[1]["id"]}
    assert "doc1" in top_two
    assert "doc2" in top_two

    # All results should have rrf_score
    for result in merged:
        assert "rrf_score" in result
        assert result["rrf_score"] > 0


def test_rrf_merge_respects_limit():
    """Test RRF merge respects the limit parameter."""
    semantic = [{"id": f"doc{i}", "score": 0.9} for i in range(10)]
    fulltext = [{"id": f"doc{i}", "score": 10.0} for i in range(10, 20)]

    merged = SearchService._rrf_merge(semantic, fulltext, limit=5)

    assert len(merged) == 5


def test_rrf_merge_with_empty_lists():
    """Test RRF merge handles empty input lists."""
    result = SearchService._rrf_merge([], [], limit=10)
    assert result == []

    semantic = [{"id": "doc1", "score": 0.9}]
    result = SearchService._rrf_merge(semantic, [], limit=10)
    assert len(result) == 1
    assert result[0]["id"] == "doc1"

    fulltext = [{"id": "doc2", "score": 10.0}]
    result = SearchService._rrf_merge([], fulltext, limit=10)
    assert len(result) == 1
    assert result[0]["id"] == "doc2"


def test_rrf_merge_k_parameter():
    """Test RRF k parameter affects ranking."""
    semantic = [{"id": "doc1", "score": 0.9}]
    fulltext = [{"id": "doc2", "score": 10.0}]

    # With k=60 (default)
    result1 = SearchService._rrf_merge(semantic, fulltext, limit=10, k=60)
    score1 = result1[0]["rrf_score"]

    # With k=10 (higher influence for top ranks)
    result2 = SearchService._rrf_merge(semantic, fulltext, limit=10, k=10)
    score2 = result2[0]["rrf_score"]

    # Scores should be different
    assert score1 != score2
