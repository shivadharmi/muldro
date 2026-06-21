"""Tests for TriSearch enriched payload scoring."""

from unittest.mock import AsyncMock

import pytest

from tests.conftest import make_mock_settings


@pytest.mark.asyncio
async def test_qdrant_memory_results_use_enriched_payload():
    """Qdrant memory results should use confidence/stability from payload, not defaults."""
    from src.services.tri_search import TriSearchService

    settings = make_mock_settings()

    mock_vector_store = AsyncMock()
    mock_vector_store.hybrid_search = AsyncMock(
        return_value=[
            {
                "id": "mem_001",
                "score": 0.92,
                "collection": "memories",
                "payload": {
                    "_original_id": "mem_001",
                    "fact_text": "User prefers concise briefings",
                    "memory_type": "preference",
                    "confidence": 0.95,
                    "stability_score": 0.8,
                    "entity_ids": ["ent_abc"],
                    "scope": "communication",
                    "preference_strength": "strong",
                    "created_at": "2026-04-10T10:00:00+00:00",
                },
            }
        ]
    )

    mock_embedder = AsyncMock()
    mock_embedder.embed_text = AsyncMock(return_value=[0.1] * 1024)

    svc = TriSearchService(
        settings=settings,
        vector_store=mock_vector_store,
        embedder=mock_embedder,
    )

    mock_db = AsyncMock()
    results = await svc.search(
        query="briefing preferences",
        user_id="usr_test",
        workspace_id="ws_test",
        db=mock_db,
        limit=10,
    )

    assert len(results) >= 1
    result = results[0]
    assert result["confidence"] == 0.95
    assert result["stability"] == 0.8
    assert result.get("preference_strength") == "strong"
    assert result.get("entity_ids") == ["ent_abc"]
    # Provenance: Qdrant-sourced results are tagged with source_db.
    assert result.get("source_db") == "qdrant"


@pytest.mark.asyncio
async def test_qdrant_search_includes_all_5_collections():
    """hybrid_search should be called with all 5 collections."""
    from src.services.tri_search import TriSearchService

    settings = make_mock_settings()

    mock_vector_store = AsyncMock()
    mock_vector_store.hybrid_search = AsyncMock(return_value=[])

    mock_embedder = AsyncMock()
    mock_embedder.embed_text = AsyncMock(return_value=[0.1] * 1024)

    svc = TriSearchService(
        settings=settings,
        vector_store=mock_vector_store,
        embedder=mock_embedder,
    )

    mock_db = AsyncMock()
    await svc.search(
        query="test",
        user_id="usr_test",
        workspace_id="ws_test",
        db=mock_db,
        limit=10,
    )

    call_args = mock_vector_store.hybrid_search.call_args
    collections = call_args.kwargs.get("collections") or call_args[1].get("collections")
    assert "memories" in collections
    assert "events" in collections
    assert "artifacts" in collections
    assert "conversations" in collections
    assert "approvals" in collections


def test_collection_to_type_includes_new_mappings():
    """_collection_to_type should map conversations and approvals."""
    from src.services.tri_search import _collection_to_type

    assert _collection_to_type("conversations") == "conversation"
    assert _collection_to_type("approvals") == "approval"
    assert _collection_to_type("memories") == "memory"


def test_search_result_exposes_provenance_fields():
    """SearchResult must serialize source_db and why_matched (not drop them).

    The /v1/search route constructs SearchResult from TriSearch dicts that
    carry source/provenance. With extra="ignore" these were silently stripped;
    they are now first-class fields so the UI can render source badges.
    """
    from src.api.schemas import SearchResult

    # Route-shaped construction (mirrors routes_search.search()).
    raw = {
        "result_type": "memory",
        "id": "mem_001",
        "title": "User prefers concise briefings",
        "text": "User prefers concise briefings",
        "final_score": 0.91,
        "source_db": "qdrant",
        "why_matched": "semantic match on 'briefings'",
    }
    result = SearchResult(
        type=raw.get("result_type", "unknown"),
        id=raw.get("id", ""),
        title=raw.get("title", ""),
        summary=raw.get("text", ""),
        score=raw.get("final_score") or raw.get("score"),
        source_db=raw.get("source_db"),
        why_matched=raw.get("why_matched"),
    )

    dumped = result.model_dump()
    assert dumped["source_db"] == "qdrant"
    assert dumped["why_matched"] == "semantic match on 'briefings'"
    # Existing fields remain intact.
    assert dumped["score"] == 0.91
    assert dumped["type"] == "memory"


def test_search_result_provenance_defaults_to_none():
    """source_db and why_matched are optional with safe None defaults."""
    from src.api.schemas import SearchResult

    result = SearchResult(type="entity", id="ent_1", title="Acme")
    dumped = result.model_dump()
    assert dumped["source_db"] is None
    assert dumped["why_matched"] is None


def test_compute_final_score_preference_boost():
    """Strong preferences should get a score boost, weak ones a penalty."""
    from src.services.tri_search import _compute_final_score

    base_result = {
        "score": 0.8,
        "confidence": 0.7,
        "stability": 0.5,
        "entity_overlap": 0.0,
        "created_at": "2026-04-10T10:00:00+00:00",
    }

    strong = {**base_result, "preference_strength": "strong"}
    weak = {**base_result, "preference_strength": "weak"}
    normal = {**base_result, "preference_strength": None}

    strong_score = _compute_final_score(strong)
    weak_score = _compute_final_score(weak)
    normal_score = _compute_final_score(normal)

    assert strong_score > normal_score
    assert weak_score < normal_score
    assert strong_score - normal_score == pytest.approx(0.05, abs=0.001)
    assert normal_score - weak_score == pytest.approx(0.03, abs=0.001)
