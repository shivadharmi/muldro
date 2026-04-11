"""Tests for VectorStore payload indexing and new collection constants."""

from unittest.mock import AsyncMock, patch

import pytest

from src.services.vector_store import (
    COLLECTION_APPROVALS,
    COLLECTION_ARTIFACTS,
    COLLECTION_CONVERSATIONS,
    COLLECTION_ENTITIES,
    COLLECTION_EVENTS,
    COLLECTION_MEMORIES,
    VectorStore,
)
from tests.conftest import make_mock_settings


def test_collection_constants_exist():
    assert COLLECTION_MEMORIES == "memories"
    assert COLLECTION_ENTITIES == "entities"
    assert COLLECTION_EVENTS == "events"
    assert COLLECTION_ARTIFACTS == "artifacts"
    assert COLLECTION_CONVERSATIONS == "conversations"
    assert COLLECTION_APPROVALS == "approvals"


@pytest.mark.asyncio
async def test_ensure_collections_creates_all_six():
    settings = make_mock_settings()
    store = VectorStore(settings)

    mock_client = AsyncMock()
    mock_client.get_collection.side_effect = Exception("not found")
    mock_client.create_collection = AsyncMock()

    with patch.object(store, "_get_client", return_value=mock_client):
        await store.ensure_collections()

    assert mock_client.create_collection.call_count == 6
    created = {
        call.kwargs["collection_name"] for call in mock_client.create_collection.call_args_list
    }
    assert created == {
        COLLECTION_MEMORIES,
        COLLECTION_ENTITIES,
        COLLECTION_EVENTS,
        COLLECTION_ARTIFACTS,
        COLLECTION_CONVERSATIONS,
        COLLECTION_APPROVALS,
    }


@pytest.mark.asyncio
async def test_ensure_indexes_creates_payload_indexes():
    settings = make_mock_settings()
    store = VectorStore(settings)

    mock_client = AsyncMock()
    mock_client.create_payload_index = AsyncMock()

    with patch.object(store, "_get_client", return_value=mock_client):
        await store.ensure_indexes()

    assert mock_client.create_payload_index.call_count >= 5

    calls = mock_client.create_payload_index.call_args_list
    pairs = {(c.kwargs["collection_name"], c.kwargs["field_name"]) for c in calls}

    assert (COLLECTION_MEMORIES, "memory_type") in pairs
    assert (COLLECTION_MEMORIES, "confidence") in pairs
    assert (COLLECTION_ENTITIES, "entity_type") in pairs
    assert (COLLECTION_EVENTS, "source") in pairs
    assert (COLLECTION_EVENTS, "event_type") in pairs
