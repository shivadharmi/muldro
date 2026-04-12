"""Tests for approval decision embedding into Qdrant."""

from unittest.mock import AsyncMock

import pytest

from tests.conftest import TEST_USER_ID


@pytest.mark.asyncio
async def test_approve_embeds_to_qdrant():
    """Approving an action should embed the decision into Qdrant."""
    from src.api.routes_approvals import _embed_approval_decision

    mock_vector_store = AsyncMock()
    mock_embedding_service = AsyncMock()
    mock_embedding_service.embed_text = AsyncMock(return_value=[0.1] * 1024)

    await _embed_approval_decision(
        approval_id="apr_test123",
        approval_type="email.send",
        summary="Send email to investor",
        risk_level="medium",
        outcome="approved",
        user_id=TEST_USER_ID,
        embedding_service=mock_embedding_service,
        vector_store=mock_vector_store,
    )

    mock_embedding_service.embed_text.assert_called_once()
    mock_vector_store.upsert.assert_called_once()
    call_kwargs = mock_vector_store.upsert.call_args.kwargs
    assert call_kwargs["collection"] == "approvals"
    assert call_kwargs["id"] == "apr_test123"
    payload = call_kwargs["payload"]
    assert payload["outcome"] == "approved"
    assert payload["capability"] == "email.send"
    assert payload["risk_level"] == "medium"


@pytest.mark.asyncio
async def test_embed_approval_graceful_on_failure():
    """Embedding failure should not crash the approval flow."""
    from src.api.routes_approvals import _embed_approval_decision

    mock_embedding_service = AsyncMock()
    mock_embedding_service.embed_text = AsyncMock(return_value=None)
    mock_vector_store = AsyncMock()

    await _embed_approval_decision(
        approval_id="apr_test",
        approval_type="email.send",
        summary="Test",
        risk_level="low",
        outcome="rejected",
        user_id=TEST_USER_ID,
        embedding_service=mock_embedding_service,
        vector_store=mock_vector_store,
    )

    mock_vector_store.upsert.assert_not_called()
