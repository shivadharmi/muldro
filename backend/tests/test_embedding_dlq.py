"""Tests for MemoryService dead_letter parameter and _enqueue_failed_embedding."""

import os
from unittest.mock import AsyncMock, MagicMock, patch

import pytest

os.environ.setdefault("PYTEST_CURRENT_TEST", "test_embedding_dlq.py")

from tests.conftest import TEST_USER_ID, TEST_WORKSPACE_ID, make_mock_settings  # noqa: E402


def _make_memory_service(dead_letter=None):
    """Construct a MemoryService with mocked dependencies."""
    settings = make_mock_settings()
    db = AsyncMock()
    from src.services.memory_service import MemoryService

    return MemoryService(settings=settings, db=db, dead_letter=dead_letter)


def test_accepts_dead_letter_param():
    """MemoryService must store the dead_letter dependency on _dead_letter."""
    dl = AsyncMock()
    svc = _make_memory_service(dead_letter=dl)
    assert svc._dead_letter is dl


def test_no_dead_letter_stored_when_none():
    """When dead_letter is omitted, _dead_letter is None."""
    svc = _make_memory_service()
    assert svc._dead_letter is None


@pytest.mark.asyncio
async def test_enqueues_on_call():
    """_enqueue_failed_embedding calls dead_letter.enqueue with correct args."""
    dl = AsyncMock()
    dl.enqueue = AsyncMock(return_value="dlq_test_id")
    svc = _make_memory_service(dead_letter=dl)

    await svc._enqueue_failed_embedding(
        record_id="mem_abc123", user_id=TEST_USER_ID, collection="memories"
    )

    dl.enqueue.assert_awaited_once()
    call_kwargs = dl.enqueue.call_args.kwargs
    assert call_kwargs["user_id"] == TEST_USER_ID
    assert call_kwargs["operation_type"] == "failed_embedding"
    assert call_kwargs["error_type"] == "EmbeddingFailure"
    assert "mem_abc123" in call_kwargs["error_message"]
    assert call_kwargs["payload"]["record_id"] == "mem_abc123"
    assert call_kwargs["payload"]["collection"] == "memories"
    assert call_kwargs["payload"]["record_type"] == "memory"


@pytest.mark.asyncio
async def test_no_error_without_dead_letter():
    """_enqueue_failed_embedding must not raise when dead_letter is None."""
    svc = _make_memory_service(dead_letter=None)
    # Should complete silently without error
    await svc._enqueue_failed_embedding(record_id="mem_xyz", user_id=TEST_USER_ID)


@pytest.mark.asyncio
async def test_handles_enqueue_failure():
    """_enqueue_failed_embedding must not propagate exceptions from enqueue."""
    dl = AsyncMock()
    dl.enqueue = AsyncMock(side_effect=RuntimeError("db is down"))
    svc = _make_memory_service(dead_letter=dl)

    # Must not raise even when enqueue fails
    await svc._enqueue_failed_embedding(record_id="mem_fail", user_id=TEST_USER_ID)


@pytest.mark.asyncio
async def test_enqueue_uses_custom_collection():
    """_enqueue_failed_embedding passes the correct collection name."""
    dl = AsyncMock()
    dl.enqueue = AsyncMock(return_value="dlq_test_id")
    svc = _make_memory_service(dead_letter=dl)

    await svc._enqueue_failed_embedding(
        record_id="ent_001", user_id=TEST_USER_ID, collection="entities"
    )

    call_kwargs = dl.enqueue.call_args.kwargs
    assert call_kwargs["payload"]["collection"] == "entities"
    assert "entities:ent_001" in call_kwargs["error_message"]


def _make_memory_service_full(dead_letter=None, vector_store=None, embedder_return=None):
    """Construct MemoryService with controllable vector_store and embedder."""
    settings = make_mock_settings()
    db = AsyncMock()
    db.add = MagicMock()
    db.flush = AsyncMock()
    mock_embedder = AsyncMock()
    mock_embedder.embed_text = AsyncMock(return_value=embedder_return)
    with patch("src.services.memory_service._base.EmbeddingService", return_value=mock_embedder):
        from src.services.memory_service import MemoryService

        svc = MemoryService(
            settings=settings,
            db=db,
            dead_letter=dead_letter,
            vector_store=vector_store,
        )
    # Patch embedder on the constructed instance so embed_text returns our value
    svc._embedder = mock_embedder
    return svc


class TestStoreMemoryDLQ:
    @pytest.mark.asyncio
    async def test_enqueues_on_qdrant_failure(self):
        """store_memory should DLQ when vector_store.upsert raises."""
        dl = AsyncMock()
        dl.enqueue = AsyncMock(return_value="dlq_id")

        vector_store = AsyncMock()
        vector_store.upsert = AsyncMock(side_effect=RuntimeError("Qdrant down"))

        svc = _make_memory_service_full(
            dead_letter=dl,
            vector_store=vector_store,
            embedder_return=[0.1, 0.2, 0.3],
        )

        await svc.store_memory(
            user_id=TEST_USER_ID,
            fact_text="Test fact",
            workspace_id=TEST_WORKSPACE_ID,
        )

        dl.enqueue.assert_awaited_once()
        call_kwargs = dl.enqueue.call_args.kwargs
        assert call_kwargs["operation_type"] == "failed_embedding"
        assert call_kwargs["user_id"] == TEST_USER_ID

    @pytest.mark.asyncio
    async def test_enqueues_when_embedding_none(self):
        """store_memory should DLQ when embed_text returns None."""
        dl = AsyncMock()
        dl.enqueue = AsyncMock(return_value="dlq_id")

        vector_store = AsyncMock()
        vector_store.upsert = AsyncMock()

        svc = _make_memory_service_full(
            dead_letter=dl,
            vector_store=vector_store,
            embedder_return=None,
        )

        await svc.store_memory(
            user_id=TEST_USER_ID,
            fact_text="Test fact with no embedding",
            workspace_id=TEST_WORKSPACE_ID,
        )

        dl.enqueue.assert_awaited_once()
        call_kwargs = dl.enqueue.call_args.kwargs
        assert call_kwargs["operation_type"] == "failed_embedding"
        assert call_kwargs["user_id"] == TEST_USER_ID
        # vector_store.upsert should NOT be called since embedding is None
        vector_store.upsert.assert_not_awaited()
