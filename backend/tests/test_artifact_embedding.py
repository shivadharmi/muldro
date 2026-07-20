"""Test artifact embedding into Qdrant on create."""

from unittest.mock import AsyncMock

import pytest

from tests.conftest import TEST_USER_ID


@pytest.mark.asyncio
async def test_embed_artifact_on_create():
    """Creating an artifact should embed title+description into Qdrant."""
    from src.api.routes_artifacts import _embed_artifact

    mock_vector_store = AsyncMock()
    mock_embedding_service = AsyncMock()
    mock_embedding_service.embed_text = AsyncMock(return_value=[0.1] * 768)

    await _embed_artifact(
        artifact_id="art_test123",
        title="Q1 Report",
        description="Quarterly financial summary",
        artifact_type="document",
        mime_type="application/pdf",
        user_id=TEST_USER_ID,
        embedding_service=mock_embedding_service,
        vector_store=mock_vector_store,
    )

    mock_vector_store.upsert.assert_called_once()
    call_kwargs = mock_vector_store.upsert.call_args.kwargs
    assert call_kwargs["collection"] == "artifacts"
    assert call_kwargs["id"] == "art_test123"
    payload = call_kwargs["payload"]
    assert payload["artifact_type"] == "document"
    assert payload["mime_type"] == "application/pdf"


@pytest.mark.asyncio
async def test_embed_artifact_skips_empty_text():
    """Artifacts with no title or description should not be embedded."""
    from src.api.routes_artifacts import _embed_artifact

    mock_vector_store = AsyncMock()
    mock_embedding_service = AsyncMock()

    await _embed_artifact(
        artifact_id="art_test",
        title=None,
        description=None,
        artifact_type="document",
        mime_type="application/pdf",
        user_id=TEST_USER_ID,
        embedding_service=mock_embedding_service,
        vector_store=mock_vector_store,
    )

    mock_embedding_service.embed_text.assert_not_called()
    mock_vector_store.upsert.assert_not_called()
