"""Tests for Qdrant cascade delete on memory supersede and merge."""

import json
from unittest.mock import AsyncMock, MagicMock, patch

import pytest

from src.services.memory_service import MemoryService
from tests.conftest import TEST_USER_ID, TEST_WORKSPACE_ID, make_mock_settings

SUPERSEDED_ID = "mem_01SUPERSEDED0000000000000"
NEW_MEM_ID = "mem_01NEWMEMORY00000000000000"
DUPLICATE_ID = "mem_01DUPLICATE000000000000000"
KEEPER_ID = "mem_01KEEPER00000000000000000"


@pytest.fixture
def settings():
    return make_mock_settings()


@pytest.fixture
def mock_db():
    db = MagicMock()
    db.add = MagicMock()
    db.flush = AsyncMock()
    result = MagicMock()
    result.scalar_one_or_none.return_value = None
    db.execute = AsyncMock(return_value=result)
    return db


@pytest.fixture
def mock_vector_store():
    vs = MagicMock()
    vs.find_similar = AsyncMock(return_value=[])
    vs.delete = AsyncMock()
    return vs


# ---------------------------------------------------------------------------
# check_contradictions — supersede path
# ---------------------------------------------------------------------------


@patch("src.services.memory_service.contradictions.complete_text")
@patch("src.services.memory_service._base.EmbeddingService")
@pytest.mark.asyncio
async def test_superseded_memory_deleted_from_qdrant(
    mock_embed_cls, mock_complete, settings, mock_db, mock_vector_store
):
    """When a contradiction is detected, the superseded memory must be deleted from Qdrant."""
    # Stub embedder
    mock_embedder = MagicMock()
    mock_embedder.embed_text = AsyncMock(return_value=[0.1] * 1024)
    mock_embed_cls.return_value = mock_embedder

    # find_similar returns the candidate that will be superseded
    mock_vector_store.find_similar = AsyncMock(
        return_value=[
            {
                "id": SUPERSEDED_ID,
                "score": 0.82,
                "payload": {"_original_id": SUPERSEDED_ID, "fact_text": "Old contradicting fact"},
            }
        ]
    )

    # Claude says it's a contradiction
    mock_complete.return_value = json.dumps({"contradicts": True})

    service = MemoryService(
        settings=settings,
        db=mock_db,
        vector_store=mock_vector_store,
    )

    superseded = await service.check_contradictions(
        user_id=TEST_USER_ID,
        new_fact="New fact that replaces old one",
        new_memory_id=NEW_MEM_ID,
        workspace_id=TEST_WORKSPACE_ID,
    )

    assert SUPERSEDED_ID in superseded
    mock_vector_store.delete.assert_awaited_once_with("memories", SUPERSEDED_ID)


@patch("src.services.memory_service.contradictions.complete_text")
@patch("src.services.memory_service._base.EmbeddingService")
@pytest.mark.asyncio
async def test_no_qdrant_delete_when_no_contradiction(
    mock_embed_cls, mock_complete, settings, mock_db, mock_vector_store
):
    """When no contradiction is found, Qdrant delete must NOT be called."""
    mock_embedder = MagicMock()
    mock_embedder.embed_text = AsyncMock(return_value=[0.1] * 1024)
    mock_embed_cls.return_value = mock_embedder

    mock_vector_store.find_similar = AsyncMock(
        return_value=[
            {
                "id": SUPERSEDED_ID,
                "score": 0.75,
                "payload": {"_original_id": SUPERSEDED_ID, "fact_text": "Agreeing fact"},
            }
        ]
    )

    mock_complete.return_value = json.dumps({"contradicts": False})

    service = MemoryService(
        settings=settings,
        db=mock_db,
        vector_store=mock_vector_store,
    )

    superseded = await service.check_contradictions(
        user_id=TEST_USER_ID,
        new_fact="Compatible fact",
        new_memory_id=NEW_MEM_ID,
        workspace_id=TEST_WORKSPACE_ID,
    )

    assert superseded == []
    mock_vector_store.delete.assert_not_awaited()


# ---------------------------------------------------------------------------
# consolidate_memories — merge path
# ---------------------------------------------------------------------------


@patch("src.services.memory_service._base.EmbeddingService")
@pytest.mark.asyncio
async def test_merged_memory_deleted_from_qdrant(
    mock_embed_cls, settings, mock_db, mock_vector_store
):
    """When a duplicate memory is merged, its vector must be deleted from Qdrant."""
    mock_embedder = MagicMock()
    mock_embedder.embed_text = AsyncMock(return_value=[0.1] * 1024)
    mock_embed_cls.return_value = mock_embedder

    # Build two in-memory Memory-like objects
    keeper_mem = MagicMock()
    keeper_mem.memory_id = KEEPER_ID
    keeper_mem.status = "active"
    keeper_mem.confidence = 0.9
    keeper_mem.stability_score = 0.5
    keeper_mem.fact_text = "Keeper fact"

    dup_mem = MagicMock()
    dup_mem.memory_id = DUPLICATE_ID
    dup_mem.status = "active"
    dup_mem.confidence = 0.7
    dup_mem.stability_score = 0.3
    dup_mem.fact_text = "Duplicate fact"

    # db.execute returns the two active memories on the first call,
    # then the duplicate row on subsequent find-by-id calls
    active_result = MagicMock()
    active_result.scalars.return_value.all.return_value = [keeper_mem, dup_mem]

    dup_row_result = MagicMock()
    dup_row_result.scalar_one_or_none.return_value = dup_mem

    mock_db.execute = AsyncMock(side_effect=[active_result, dup_row_result])

    # find_similar: keeper finds the duplicate at >0.95
    mock_vector_store.find_similar = AsyncMock(
        return_value=[
            {
                "id": DUPLICATE_ID,
                "score": 0.97,
                "payload": {"_original_id": DUPLICATE_ID},
            }
        ]
    )

    service = MemoryService(
        settings=settings,
        db=mock_db,
        vector_store=mock_vector_store,
    )

    merged = await service.consolidate_memories(
        user_id=TEST_USER_ID,
        workspace_id=TEST_WORKSPACE_ID,
    )

    assert merged == 1
    mock_vector_store.delete.assert_awaited_once_with("memories", DUPLICATE_ID)


# ---------------------------------------------------------------------------
# Graceful failure when Qdrant delete raises
# ---------------------------------------------------------------------------


@patch("src.services.memory_service.contradictions.complete_text")
@patch("src.services.memory_service._base.EmbeddingService")
@pytest.mark.asyncio
async def test_cascade_delete_graceful_on_qdrant_failure(
    mock_embed_cls, mock_complete, settings, mock_db, mock_vector_store
):
    """A Qdrant delete failure must not crash check_contradictions."""
    mock_embedder = MagicMock()
    mock_embedder.embed_text = AsyncMock(return_value=[0.1] * 1024)
    mock_embed_cls.return_value = mock_embedder

    mock_vector_store.find_similar = AsyncMock(
        return_value=[
            {
                "id": SUPERSEDED_ID,
                "score": 0.85,
                "payload": {"_original_id": SUPERSEDED_ID, "fact_text": "Old fact"},
            }
        ]
    )
    # Simulate a Qdrant connection error
    mock_vector_store.delete = AsyncMock(side_effect=RuntimeError("Qdrant unavailable"))

    mock_complete.return_value = json.dumps({"contradicts": True})

    service = MemoryService(
        settings=settings,
        db=mock_db,
        vector_store=mock_vector_store,
    )

    # Should not raise — graceful degradation
    superseded = await service.check_contradictions(
        user_id=TEST_USER_ID,
        new_fact="Newer fact",
        new_memory_id=NEW_MEM_ID,
        workspace_id=TEST_WORKSPACE_ID,
    )

    # Supersede still recorded in DB even though vector delete failed
    assert SUPERSEDED_ID in superseded
    mock_vector_store.delete.assert_awaited_once_with("memories", SUPERSEDED_ID)


@patch("src.services.memory_service._base.EmbeddingService")
@pytest.mark.asyncio
async def test_cascade_delete_graceful_on_merge_qdrant_failure(
    mock_embed_cls, settings, mock_db, mock_vector_store
):
    """A Qdrant delete failure during consolidation must not crash consolidate_memories."""
    mock_embedder = MagicMock()
    mock_embedder.embed_text = AsyncMock(return_value=[0.1] * 1024)
    mock_embed_cls.return_value = mock_embedder

    keeper_mem = MagicMock()
    keeper_mem.memory_id = KEEPER_ID
    keeper_mem.status = "active"
    keeper_mem.confidence = 0.9
    keeper_mem.stability_score = 0.5
    keeper_mem.fact_text = "Keeper fact"

    dup_mem = MagicMock()
    dup_mem.memory_id = DUPLICATE_ID
    dup_mem.status = "active"
    dup_mem.confidence = 0.7
    dup_mem.stability_score = 0.3
    dup_mem.fact_text = "Duplicate fact"

    active_result = MagicMock()
    active_result.scalars.return_value.all.return_value = [keeper_mem, dup_mem]

    dup_row_result = MagicMock()
    dup_row_result.scalar_one_or_none.return_value = dup_mem

    mock_db.execute = AsyncMock(side_effect=[active_result, dup_row_result])

    mock_vector_store.find_similar = AsyncMock(
        return_value=[
            {
                "id": DUPLICATE_ID,
                "score": 0.97,
                "payload": {"_original_id": DUPLICATE_ID},
            }
        ]
    )
    mock_vector_store.delete = AsyncMock(side_effect=RuntimeError("Qdrant timeout"))

    service = MemoryService(
        settings=settings,
        db=mock_db,
        vector_store=mock_vector_store,
    )

    # Should not raise — merge count still returned correctly
    merged = await service.consolidate_memories(
        user_id=TEST_USER_ID,
        workspace_id=TEST_WORKSPACE_ID,
    )

    assert merged == 1
    mock_vector_store.delete.assert_awaited_once_with("memories", DUPLICATE_ID)
