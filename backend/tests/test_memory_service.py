"""Tests for MemoryService — extraction, storage, retrieval."""

import json
from unittest.mock import AsyncMock, MagicMock, patch

import pytest

from src.services.memory_service import MemoryService
from tests.conftest import TEST_USER_ID, make_mock_settings


@pytest.fixture
def settings():
    return make_mock_settings()


@pytest.fixture
def mock_db():
    db = MagicMock()
    db.add = MagicMock()
    db.flush = AsyncMock()

    # Default: no duplicate found
    no_result = MagicMock()
    no_result.scalar_one_or_none.return_value = None
    db.execute = AsyncMock(return_value=no_result)
    return db


@patch("src.services.memory_service.EmbeddingService")
@patch("src.services.memory_service.get_anthropic_client")
@pytest.mark.asyncio
async def test_extract_stores_memories(mock_get_client, mock_embed_cls, settings, mock_db):
    """Should extract and store memories from text."""
    extraction = {
        "memories": [
            {
                "memory_type": "semantic",
                "scope": "general",
                "fact_text": "Alice is CFO at Acme Corp",
                "confidence": 0.9,
                "ttl_days": None,
            },
            {
                "memory_type": "preference",
                "scope": "presentation",
                "fact_text": "User prefers concise updates",
                "confidence": 0.85,
                "ttl_days": 180,
            },
        ]
    }

    mock_client = MagicMock()
    response = MagicMock()
    response.content = [MagicMock(text=json.dumps(extraction))]
    mock_client.messages.create = AsyncMock(return_value=response)
    mock_get_client.return_value = mock_client

    mock_embedder = MagicMock()
    mock_embedder.embed_text = AsyncMock(return_value=[0.1] * 1024)
    mock_embed_cls.return_value = mock_embedder

    service = MemoryService(settings=settings, db=mock_db)
    memory_ids = await service.extract_and_store(
        TEST_USER_ID, "Alice (CFO at Acme) prefers concise updates.", ["evt_001"]
    )

    assert len(memory_ids) == 2
    assert all(mid.startswith("mem_") for mid in memory_ids)
    assert mock_db.add.call_count == 2


@patch("src.services.memory_service.EmbeddingService")
@patch("src.services.memory_service.get_anthropic_client")
@pytest.mark.asyncio
async def test_extract_skips_duplicates(mock_get_client, mock_embed_cls, settings, mock_db):
    """Should not store duplicate memories."""
    extraction = {
        "memories": [
            {
                "memory_type": "semantic",
                "fact_text": "Already known fact",
                "confidence": 0.9,
            },
        ]
    }

    mock_client = MagicMock()
    response = MagicMock()
    response.content = [MagicMock(text=json.dumps(extraction))]
    mock_client.messages.create = AsyncMock(return_value=response)
    mock_get_client.return_value = mock_client

    mock_embedder = MagicMock()
    mock_embedder.embed_text = AsyncMock(return_value=[0.1] * 1024)
    mock_embed_cls.return_value = mock_embedder

    # First execute: extraction call, second: duplicate check returns existing
    no_result = MagicMock()
    no_result.scalar_one_or_none.return_value = "mem_existing"
    mock_db.execute = AsyncMock(return_value=no_result)

    service = MemoryService(settings=settings, db=mock_db)
    memory_ids = await service.extract_and_store(TEST_USER_ID, "Already known fact", ["evt_002"])

    assert len(memory_ids) == 0


@patch("src.services.memory_service.EmbeddingService")
@patch("src.services.memory_service.get_anthropic_client")
@pytest.mark.asyncio
async def test_retrieve_returns_matching(mock_get_client, mock_embed_cls, settings, mock_db):
    """Should return memories matching the query."""
    mock_memory = MagicMock()
    mock_memory.memory_id = "mem_001"
    mock_memory.memory_type = "semantic"
    mock_memory.fact_text = "Alice is CFO"
    mock_memory.confidence = 0.9
    mock_memory.scope = "general"

    result_mock = MagicMock()
    result_mock.scalars.return_value.all.return_value = [mock_memory]
    mock_db.execute = AsyncMock(return_value=result_mock)

    mock_get_client.return_value = MagicMock()
    mock_embedder = MagicMock()
    # Return None to use text-based fallback (matching the mock_db setup)
    mock_embedder.embed_text = AsyncMock(return_value=None)
    mock_embed_cls.return_value = mock_embedder

    service = MemoryService(settings=settings, db=mock_db)
    results = await service.retrieve(TEST_USER_ID, "Alice")

    assert len(results) == 1
    assert results[0]["fact_text"] == "Alice is CFO"


@patch("src.services.memory_service.EmbeddingService")
@patch("src.services.memory_service.get_anthropic_client")
@pytest.mark.asyncio
async def test_extract_auto_checks_contradictions(
    mock_get_client, mock_embed_cls, settings, mock_db
):
    """extract_and_store should defer contradiction checks via event bus."""
    extraction = {
        "memories": [
            {
                "memory_type": "semantic",
                "scope": "general",
                "fact_text": "Alice resigned as CFO",
                "confidence": 0.9,
                "ttl_days": None,
            },
        ]
    }

    mock_client = MagicMock()
    response = MagicMock()
    response.content = [MagicMock(text=json.dumps(extraction))]
    mock_client.messages.create = AsyncMock(return_value=response)
    mock_get_client.return_value = mock_client

    mock_embedder = MagicMock()
    mock_embedder.embed_text = AsyncMock(return_value=[0.1] * 1024)
    mock_embed_cls.return_value = mock_embedder

    event_bus = AsyncMock()
    event_bus.event_stream = MagicMock(return_value=f"jarvis:events:{TEST_USER_ID}")
    event_bus.publish = AsyncMock()

    service = MemoryService(settings=settings, db=mock_db, event_bus=event_bus)

    # check_contradictions should NOT be called synchronously
    service.check_contradictions = AsyncMock(return_value=[])

    memory_ids = await service.extract_and_store(
        TEST_USER_ID, "Alice has resigned as CFO.", ["evt_001"]
    )

    assert len(memory_ids) == 1
    # Contradiction check is deferred: synchronous call must NOT happen
    service.check_contradictions.assert_not_called()
    # Instead, event bus should receive a contradiction_check_requested event
    event_bus.publish.assert_called()
    # Find the contradiction_check_requested call among all publish calls
    contradiction_calls = [
        c for c in event_bus.publish.call_args_list if c[0][1] == "contradiction_check_requested"
    ]
    assert len(contradiction_calls) == 1
    assert contradiction_calls[0][0][2]["fact_text"] == "Alice resigned as CFO"
    assert contradiction_calls[0][0][2]["memory_id"] == memory_ids[0]


@patch("src.services.memory_service.get_anthropic_client")
@pytest.mark.asyncio
async def test_memory_upsert_includes_enriched_payload(mock_get_client):
    """Memory Qdrant payloads should include confidence, stability, entity_ids, scope."""
    settings = make_mock_settings()
    mock_client = MagicMock()
    mock_get_client.return_value = mock_client

    mock_db = MagicMock()
    mock_db.add = MagicMock()
    mock_db.flush = AsyncMock()
    result_mock = MagicMock()
    result_mock.scalar_one_or_none.return_value = None
    mock_db.execute = AsyncMock(return_value=result_mock)

    mock_vector_store = AsyncMock()

    svc = MemoryService(
        settings=settings,
        db=mock_db,
        vector_store=mock_vector_store,
    )
    svc._embedder = AsyncMock()
    svc._embedder.embed_text = AsyncMock(return_value=[0.1] * 1024)

    await svc.store_memory(
        user_id="usr_test",
        fact_text="Test fact",
        memory_type="semantic",
        scope="general",
        entity_ids=["ent_abc"],
        workspace_id="ws_test",
    )

    mock_vector_store.upsert.assert_called_once()
    # Get the payload from the call - it's the 4th positional arg
    call_args = mock_vector_store.upsert.call_args
    payload = call_args[0][3] if len(call_args[0]) > 3 else call_args.kwargs.get("payload")

    assert payload["memory_type"] == "semantic"
    assert payload["fact_text"] == "Test fact"
    assert payload["confidence"] == 0.8
    assert payload["stability_score"] == 0.0
    assert payload["entity_ids"] == ["ent_abc"]
    assert payload["scope"] == "general"
    assert "created_at" in payload


@patch("src.services.memory_service.EmbeddingService")
@patch("src.services.memory_service.get_anthropic_client")
@pytest.mark.asyncio
async def test_contradiction_failure_does_not_block_storage(
    mock_get_client, mock_embed_cls, settings, mock_db
):
    """If check_contradictions fails, memory should still be stored."""
    extraction = {
        "memories": [
            {
                "memory_type": "semantic",
                "scope": "general",
                "fact_text": "Bob is the new CTO",
                "confidence": 0.85,
                "ttl_days": None,
            },
        ]
    }

    mock_client = MagicMock()
    response = MagicMock()
    response.content = [MagicMock(text=json.dumps(extraction))]
    mock_client.messages.create = AsyncMock(return_value=response)
    mock_get_client.return_value = mock_client

    mock_embedder = MagicMock()
    mock_embedder.embed_text = AsyncMock(return_value=[0.1] * 1024)
    mock_embed_cls.return_value = mock_embedder

    service = MemoryService(settings=settings, db=mock_db)

    # Make check_contradictions blow up
    service.check_contradictions = AsyncMock(side_effect=RuntimeError("API down"))

    memory_ids = await service.extract_and_store(TEST_USER_ID, "Bob is the new CTO.", ["evt_002"])

    # Memory should still be stored despite contradiction check failure
    assert len(memory_ids) == 1
    assert mock_db.add.call_count >= 1
