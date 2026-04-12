"""Tests for MemoryService dead_letter parameter and _enqueue_failed_embedding."""

import os
from unittest.mock import AsyncMock, MagicMock, patch

import pytest

os.environ.setdefault("PYTEST_CURRENT_TEST", "test_embedding_dlq.py")

from tests.conftest import TEST_USER_ID, make_mock_settings  # noqa: E402


def _make_memory_service(dead_letter=None):
    """Construct a MemoryService with mocked dependencies."""
    settings = make_mock_settings()
    db = AsyncMock()
    with patch("src.services.memory_service.get_anthropic_client", return_value=MagicMock()):
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
