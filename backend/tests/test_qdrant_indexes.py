"""Tests for VectorStore.set_payload and ensure_indexes covering all 6 collections."""

import uuid
from unittest.mock import AsyncMock, MagicMock

import pytest

from tests.conftest import make_mock_settings

# Namespace must match the one in vector_store.py
_QDRANT_NS = uuid.UUID("a3f1b2c4-d5e6-4f78-9a0b-1c2d3e4f5a6b")


def _expected_uuid(s: str) -> str:
    return str(uuid.uuid5(_QDRANT_NS, s))


# ---------------------------------------------------------------------------
# 1. set_payload
# ---------------------------------------------------------------------------


@pytest.mark.asyncio
async def test_set_payload_calls_client():
    """set_payload should call client.set_payload with the correct UUID and payload."""
    from qdrant_client.models import PointIdsList

    from src.services.vector_store import VectorStore

    settings = make_mock_settings()
    settings.qdrant_url = "http://localhost:6333"
    settings.qdrant_api_key = None

    mock_client = AsyncMock()
    mock_client.get_collections = AsyncMock(return_value=MagicMock())
    mock_client.set_payload = AsyncMock()

    vs = VectorStore(settings=settings)
    vs._client = mock_client

    point_id = "mem_01ABC"
    payload = {"stability": 0.85, "last_accessed": "2026-04-12"}

    await vs.set_payload("memories", point_id, payload)

    mock_client.set_payload.assert_called_once()
    call_kwargs = mock_client.set_payload.call_args

    assert call_kwargs.kwargs["collection_name"] == "memories"
    assert call_kwargs.kwargs["payload"] == payload

    points_arg: PointIdsList = call_kwargs.kwargs["points"]
    assert points_arg.points == [_expected_uuid(point_id)]


@pytest.mark.asyncio
async def test_set_payload_noop_without_client():
    """set_payload should not raise when Qdrant is not configured."""
    from src.services.vector_store import VectorStore

    settings = make_mock_settings()
    settings.qdrant_url = None  # no Qdrant configured

    vs = VectorStore(settings=settings)

    # Should complete without error and without calling anything
    await vs.set_payload("memories", "mem_test", {"field": "value"})
    # No exception means pass


# ---------------------------------------------------------------------------
# 2. ensure_indexes covers all 6 collections
# ---------------------------------------------------------------------------


@pytest.mark.asyncio
async def test_ensure_indexes_covers_all_collections():
    """ensure_indexes should create payload indexes for all 6 Qdrant collections."""
    from src.services.vector_store import (
        COLLECTION_APPROVALS,
        COLLECTION_ARTIFACTS,
        COLLECTION_CONVERSATIONS,
        COLLECTION_ENTITIES,
        COLLECTION_EVENTS,
        COLLECTION_MEMORIES,
        VectorStore,
    )

    settings = make_mock_settings()
    settings.qdrant_url = "http://localhost:6333"
    settings.qdrant_api_key = None

    mock_client = AsyncMock()
    mock_client.get_collections = AsyncMock(return_value=MagicMock())
    mock_client.create_payload_index = AsyncMock()

    vs = VectorStore(settings=settings)
    vs._client = mock_client

    await vs.ensure_indexes()

    # Collect every (collection_name, field_name) pair that was indexed
    indexed = {
        (c.kwargs["collection_name"], c.kwargs["field_name"])
        for c in mock_client.create_payload_index.call_args_list
    }

    # memories
    assert (COLLECTION_MEMORIES, "memory_type") in indexed
    assert (COLLECTION_MEMORIES, "confidence") in indexed

    # entities
    assert (COLLECTION_ENTITIES, "entity_type") in indexed

    # events
    assert (COLLECTION_EVENTS, "source") in indexed
    assert (COLLECTION_EVENTS, "event_type") in indexed
    assert (COLLECTION_EVENTS, "importance_score") in indexed

    # approvals (new)
    assert (COLLECTION_APPROVALS, "capability") in indexed
    assert (COLLECTION_APPROVALS, "outcome") in indexed

    # conversations (new)
    assert (COLLECTION_CONVERSATIONS, "conversation_id") in indexed

    # artifacts (new)
    assert (COLLECTION_ARTIFACTS, "artifact_type") in indexed
    assert (COLLECTION_ARTIFACTS, "mime_type") in indexed
