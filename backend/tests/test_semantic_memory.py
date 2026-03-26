"""Tests for semantic memory — embedding-based retrieval and preference extraction."""

import json
from unittest.mock import AsyncMock, MagicMock, patch

import pytest

from src.services.memory_service import MemoryService
from tests.conftest import TEST_USER_ID, make_mock_settings


@pytest.fixture
def settings():
    s = make_mock_settings()
    s.embedding_model = "amazon.titan-embed-text-v2:0"
    s.bedrock_region = "ap-south-1"
    return s


@pytest.fixture
def mock_db():
    db = MagicMock()
    db.add = MagicMock()
    db.flush = AsyncMock()
    db.execute = AsyncMock()
    return db


@patch("src.services.memory_service.EmbeddingService")
@patch("src.services.memory_service.get_anthropic_client")
@pytest.mark.asyncio
async def test_extract_stores_with_embedding(mock_get_client, mock_embed_cls, settings, mock_db):
    """Should store memories with embeddings when available."""
    mock_client = MagicMock()
    extraction = {
        "memories": [
            {
                "memory_type": "semantic",
                "scope": "general",
                "fact_text": "Alice is CFO at Acme Corp",
                "confidence": 0.9,
                "ttl_days": None,
            }
        ]
    }
    response = MagicMock()
    response.content = [MagicMock(text=json.dumps(extraction))]
    mock_client.messages.create = AsyncMock(return_value=response)
    mock_get_client.return_value = mock_client

    fake_embedding = [0.1] * 1024
    mock_embedder = MagicMock()
    mock_embedder.embed_text = AsyncMock(return_value=fake_embedding)
    mock_embed_cls.return_value = mock_embedder

    # No duplicates
    no_result = MagicMock()
    no_result.scalar_one_or_none.return_value = None
    mock_db.execute = AsyncMock(return_value=no_result)

    service = MemoryService(settings=settings, db=mock_db)
    ids = await service.extract_and_store(TEST_USER_ID, "Alice is CFO at Acme", ["evt_001"])

    assert len(ids) == 1
    assert ids[0].startswith("mem_")
    # Verify Memory object was added to DB (embedding now in Qdrant, not model)
    add_call = mock_db.add.call_args[0][0]
    assert add_call.fact_text == "Alice is CFO at Acme Corp"


@patch("src.services.memory_service.EmbeddingService")
@patch("src.services.memory_service.get_anthropic_client")
@pytest.mark.asyncio
async def test_semantic_retrieve_with_embedding(mock_get_client, mock_embed_cls, settings, mock_db):
    """Should use Qdrant semantic search when query embedding succeeds."""
    mock_get_client.return_value = MagicMock()

    fake_embedding = [0.2] * 1024
    mock_embedder = MagicMock()
    mock_embedder.embed_text = AsyncMock(return_value=fake_embedding)
    mock_embed_cls.return_value = mock_embedder

    # Mock Qdrant vector_store.search results
    mock_vector_store = AsyncMock()
    mock_vector_store.search = AsyncMock(
        return_value=[
            {
                "id": "mem_001",
                "score": 0.95,
                "payload": {
                    "_original_id": "mem_001",
                    "fact_text": "Alice is CFO at Acme Corp",
                },
            }
        ]
    )

    # Mock Postgres batch-fetch of Memory rows
    from datetime import datetime, timezone

    mock_mem = MagicMock()
    mock_mem.memory_id = "mem_001"
    mock_mem.memory_type = "semantic"
    mock_mem.fact_text = "Alice is CFO at Acme Corp"
    mock_mem.confidence = 0.9
    mock_mem.scope = "general"
    mock_mem.entity_ids = None
    mock_mem.stability_score = 0.0
    mock_mem.last_accessed_at = None
    mock_mem.created_at = datetime.now(timezone.utc)

    mock_result = MagicMock()
    mock_result.scalars.return_value.all.return_value = [mock_mem]
    mock_db.execute = AsyncMock(return_value=mock_result)

    service = MemoryService(settings=settings, db=mock_db, vector_store=mock_vector_store)
    results = await service.retrieve(TEST_USER_ID, "Who is Alice?")

    assert len(results) == 1
    assert results[0]["memory_id"] == "mem_001"
    assert results[0]["relevance"] == 0.95


@patch("src.services.memory_service.EmbeddingService")
@patch("src.services.memory_service.get_anthropic_client")
@pytest.mark.asyncio
async def test_text_fallback_when_embedding_fails(
    mock_get_client, mock_embed_cls, settings, mock_db
):
    """Should fall back to ILIKE search when embedding fails."""
    mock_get_client.return_value = MagicMock()

    mock_embedder = MagicMock()
    mock_embedder.embed_text = AsyncMock(return_value=None)  # Embedding fails
    mock_embed_cls.return_value = mock_embedder

    mock_memory = MagicMock()
    mock_memory.memory_id = "mem_002"
    mock_memory.memory_type = "semantic"
    mock_memory.fact_text = "Bob works at Startup Inc"
    mock_memory.confidence = 0.8
    mock_memory.scope = "general"

    mock_result = MagicMock()
    mock_result.scalars.return_value.all.return_value = [mock_memory]
    mock_db.execute = AsyncMock(return_value=mock_result)

    service = MemoryService(settings=settings, db=mock_db)
    results = await service.retrieve(TEST_USER_ID, "Bob")

    assert len(results) == 1
    assert results[0]["memory_id"] == "mem_002"
    assert "similarity" not in results[0]


@patch("src.services.memory_service.EmbeddingService")
@patch("src.services.memory_service.get_anthropic_client")
@pytest.mark.asyncio
async def test_extract_preferences(mock_get_client, mock_embed_cls, settings, mock_db):
    """Should extract and store user preferences."""
    mock_client = MagicMock()
    extraction = {
        "preferences": [
            {
                "category": "briefing",
                "fact_text": "User prefers concise morning briefings",
                "confidence": 0.85,
                "strength": "strong",
            }
        ]
    }
    response = MagicMock()
    response.content = [MagicMock(text=json.dumps(extraction))]
    mock_client.messages.create = AsyncMock(return_value=response)
    mock_get_client.return_value = mock_client

    mock_embedder = MagicMock()
    mock_embedder.embed_text = AsyncMock(return_value=[0.1] * 1024)
    mock_embed_cls.return_value = mock_embedder

    no_result = MagicMock()
    no_result.scalar_one_or_none.return_value = None
    mock_db.execute = AsyncMock(return_value=no_result)

    service = MemoryService(settings=settings, db=mock_db)
    ids = await service.extract_preferences(
        TEST_USER_ID, "I like short briefings in the morning", ["evt_001"]
    )

    assert len(ids) == 1
    add_call = mock_db.add.call_args[0][0]
    assert add_call.memory_type == "preference"
    assert add_call.scope == "briefing"
    assert add_call.provenance["strength"] == "strong"


@patch("src.services.memory_service.EmbeddingService")
@patch("src.services.memory_service.get_anthropic_client")
@pytest.mark.asyncio
async def test_get_user_preferences(mock_get_client, mock_embed_cls, settings, mock_db):
    """Should retrieve user preferences filtered by category."""
    mock_get_client.return_value = MagicMock()
    mock_embed_cls.return_value = MagicMock()

    mock_pref = MagicMock()
    mock_pref.memory_id = "mem_pref_001"
    mock_pref.scope = "communication"
    mock_pref.fact_text = "User prefers professional tone"
    mock_pref.confidence = 0.9
    mock_pref.provenance = {"strength": "strong"}

    mock_result = MagicMock()
    mock_result.scalars.return_value.all.return_value = [mock_pref]
    mock_db.execute = AsyncMock(return_value=mock_result)

    service = MemoryService(settings=settings, db=mock_db)
    prefs = await service.get_user_preferences(TEST_USER_ID, category="communication")

    assert len(prefs) == 1
    assert prefs[0]["category"] == "communication"
    assert prefs[0]["strength"] == "strong"
