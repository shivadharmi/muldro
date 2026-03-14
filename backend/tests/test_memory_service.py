"""Tests for MemoryService — extraction, storage, retrieval."""

import json
from unittest.mock import AsyncMock, MagicMock, patch

import pytest

from src.services.memory_service import MemoryService
from tests.conftest import make_mock_settings


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


@patch("src.services.memory_service.get_anthropic_client")
@pytest.mark.asyncio
async def test_extract_stores_memories(mock_get_client, settings, mock_db):
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

    service = MemoryService(settings=settings, db=mock_db)
    memory_ids = await service.extract_and_store(
        "usr_default", "Alice (CFO at Acme) prefers concise updates.", ["evt_001"]
    )

    assert len(memory_ids) == 2
    assert all(mid.startswith("mem_") for mid in memory_ids)
    assert mock_db.add.call_count == 2


@patch("src.services.memory_service.get_anthropic_client")
@pytest.mark.asyncio
async def test_extract_skips_duplicates(mock_get_client, settings, mock_db):
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

    # First execute: extraction call, second: duplicate check returns existing
    no_result = MagicMock()
    no_result.scalar_one_or_none.return_value = "mem_existing"
    mock_db.execute = AsyncMock(return_value=no_result)

    service = MemoryService(settings=settings, db=mock_db)
    memory_ids = await service.extract_and_store("usr_default", "Already known fact", ["evt_002"])

    assert len(memory_ids) == 0


@patch("src.services.memory_service.get_anthropic_client")
@pytest.mark.asyncio
async def test_retrieve_returns_matching(mock_get_client, settings, mock_db):
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
    service = MemoryService(settings=settings, db=mock_db)
    results = await service.retrieve("usr_default", "Alice")

    assert len(results) == 1
    assert results[0]["fact_text"] == "Alice is CFO"
