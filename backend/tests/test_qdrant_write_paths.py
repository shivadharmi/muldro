"""Tests for Qdrant write paths — events, conversations, approvals, artifacts embedding."""

import json
from unittest.mock import AsyncMock, MagicMock, patch

import pytest

from tests.conftest import TEST_USER_ID, TEST_WORKSPACE_ID, make_mock_settings, make_raw_event

# ---------------------------------------------------------------------------
# 1. Events embedding
# ---------------------------------------------------------------------------


@patch("src.services.event_processor.get_anthropic_client")
@pytest.mark.asyncio
async def test_event_embedding_called_for_important_events(mock_get_client):
    """Events with importance_score >= 0.3 should be embedded into Qdrant."""
    from src.services.event_processor import EventProcessor

    scores = {
        "importance_score": 0.8,
        "urgency_score": 0.5,
        "confidence_score": 0.9,
        "importance_signals": {},
        "summary": "Important update from investor",
    }
    mock_client = MagicMock()
    mock_client.messages.create = AsyncMock(
        return_value=MagicMock(content=[MagicMock(text=json.dumps(scores))])
    )
    mock_get_client.return_value = mock_client

    mock_db = MagicMock()
    mock_db.add = MagicMock()
    mock_db.commit = AsyncMock()
    result_mock = MagicMock()
    result_mock.scalar_one_or_none.return_value = None
    mock_db.execute = AsyncMock(return_value=result_mock)

    mock_vs = AsyncMock()
    mock_es = AsyncMock()
    mock_es.embed_text = AsyncMock(return_value=[0.1] * 1024)

    settings = make_mock_settings()
    processor = EventProcessor(
        settings=settings,
        db=mock_db,
        embedding_service=mock_es,
        vector_store=mock_vs,
    )
    raw = make_raw_event()
    event_id = await processor.process(raw, TEST_USER_ID, TEST_WORKSPACE_ID)

    assert event_id is not None
    mock_vs.upsert.assert_called_once()
    call_kwargs = mock_vs.upsert.call_args
    payload = call_kwargs.kwargs.get("payload") or call_kwargs[1].get("payload")
    # Check required payload fields
    assert "event_id" in payload
    assert "event_type" in payload
    assert "source" in payload
    assert "importance_score" in payload
    assert "workspace_id" in payload
    assert payload["workspace_id"] == TEST_WORKSPACE_ID


@patch("src.services.event_processor.get_anthropic_client")
@pytest.mark.asyncio
async def test_event_embedding_skipped_for_low_importance(mock_get_client):
    """Events with importance_score < 0.3 should NOT be embedded."""
    from src.services.event_processor import EventProcessor

    scores = {
        "importance_score": 0.1,
        "urgency_score": 0.1,
        "confidence_score": 0.5,
        "importance_signals": {},
        "summary": "Newsletter",
    }
    mock_client = MagicMock()
    mock_client.messages.create = AsyncMock(
        return_value=MagicMock(content=[MagicMock(text=json.dumps(scores))])
    )
    mock_get_client.return_value = mock_client

    mock_db = MagicMock()
    mock_db.add = MagicMock()
    mock_db.commit = AsyncMock()
    result_mock = MagicMock()
    result_mock.scalar_one_or_none.return_value = None
    mock_db.execute = AsyncMock(return_value=result_mock)

    mock_vs = AsyncMock()
    mock_es = AsyncMock()

    settings = make_mock_settings()
    processor = EventProcessor(
        settings=settings,
        db=mock_db,
        embedding_service=mock_es,
        vector_store=mock_vs,
    )
    raw = make_raw_event()
    await processor.process(raw, TEST_USER_ID, TEST_WORKSPACE_ID)

    mock_vs.upsert.assert_not_called()


@patch("src.services.event_processor.get_anthropic_client")
@pytest.mark.asyncio
async def test_event_embedding_failure_does_not_block(mock_get_client):
    """Embedding failure should not prevent event from being stored."""
    from src.services.event_processor import EventProcessor

    scores = {
        "importance_score": 0.9,
        "urgency_score": 0.5,
        "confidence_score": 0.9,
        "importance_signals": {},
        "summary": "Critical update",
    }
    mock_client = MagicMock()
    mock_client.messages.create = AsyncMock(
        return_value=MagicMock(content=[MagicMock(text=json.dumps(scores))])
    )
    mock_get_client.return_value = mock_client

    mock_db = MagicMock()
    mock_db.add = MagicMock()
    mock_db.commit = AsyncMock()
    result_mock = MagicMock()
    result_mock.scalar_one_or_none.return_value = None
    mock_db.execute = AsyncMock(return_value=result_mock)

    mock_vs = AsyncMock()
    mock_vs.upsert = AsyncMock(side_effect=RuntimeError("Qdrant down"))
    mock_es = AsyncMock()
    mock_es.embed_text = AsyncMock(return_value=[0.1] * 1024)

    settings = make_mock_settings()
    processor = EventProcessor(
        settings=settings,
        db=mock_db,
        embedding_service=mock_es,
        vector_store=mock_vs,
    )
    raw = make_raw_event()
    event_id = await processor.process(raw, TEST_USER_ID, TEST_WORKSPACE_ID)

    # Event should still be stored even though embedding failed
    assert event_id is not None
    mock_db.add.assert_called_once()


# ---------------------------------------------------------------------------
# 2. Conversations embedding
# ---------------------------------------------------------------------------


@pytest.mark.asyncio
async def test_conversation_summary_embedded():
    """_summarize_history should embed the summary into Qdrant conversations collection."""
    settings = make_mock_settings()
    settings.qdrant_url = "http://localhost:6333"

    mock_vs = AsyncMock()
    mock_es = AsyncMock()
    mock_es.embed_text = AsyncMock(return_value=[0.1] * 1024)

    lines = ["User: Hello", "Assistant: Hi there", "User: What's my schedule?"]

    with (
        patch("src.orchestrator.jarvis.get_anthropic_client") as mock_get_client,
        patch(
            "src.services.vector_store.VectorStore",
            return_value=mock_vs,
        ) as _mock_vs_cls,
        patch(
            "src.services.embedding_service.EmbeddingService",
            return_value=mock_es,
        ) as _mock_es_cls,
    ):
        mock_client = MagicMock()
        mock_client.messages.create = AsyncMock(
            return_value=MagicMock(
                content=[MagicMock(type="text", text="User greeted and asked about schedule.")]
            )
        )
        mock_get_client.return_value = mock_client

        from src.orchestrator.jarvis import JarvisOrchestrator

        orch = JarvisOrchestrator(settings=settings, db_factory=MagicMock(), services=MagicMock())

        summary = await orch._summarize_history(
            lines, conversation_id="conv_test123", user_id=TEST_USER_ID
        )

        assert summary == "User greeted and asked about schedule."
        mock_vs.upsert.assert_called_once()
        call_kwargs = mock_vs.upsert.call_args
        assert call_kwargs.kwargs["collection"] == "conversations"
        assert call_kwargs.kwargs["id"] == "conv_test123"
        payload = call_kwargs.kwargs["payload"]
        assert payload["conversation_id"] == "conv_test123"
        assert payload["message_count"] == 3
        assert "summary" in payload
        assert "created_at" in payload


@pytest.mark.asyncio
async def test_conversation_embedding_skipped_without_conversation_id():
    """No embedding should happen when conversation_id is None."""
    settings = make_mock_settings()
    settings.qdrant_url = "http://localhost:6333"

    mock_vs = AsyncMock()

    with (
        patch("src.orchestrator.jarvis.get_anthropic_client") as mock_get_client,
        patch("src.services.vector_store.VectorStore", return_value=mock_vs),
        patch("src.services.embedding_service.EmbeddingService"),
    ):
        mock_client = MagicMock()
        mock_client.messages.create = AsyncMock(
            return_value=MagicMock(content=[MagicMock(type="text", text="Summary")])
        )
        mock_get_client.return_value = mock_client

        from src.orchestrator.jarvis import JarvisOrchestrator

        orch = JarvisOrchestrator(settings=settings, db_factory=MagicMock(), services=MagicMock())

        summary = await orch._summarize_history(
            ["line1"], conversation_id=None, user_id=TEST_USER_ID
        )
        assert summary == "Summary"
        mock_vs.upsert.assert_not_called()


# ---------------------------------------------------------------------------
# 3. Approvals embedding
# ---------------------------------------------------------------------------


@pytest.mark.asyncio
async def test_approval_embed_includes_required_fields():
    """_embed_approval_decision should include all required payload fields."""
    from src.api.routes_approvals import _embed_approval_decision

    mock_es = AsyncMock()
    mock_es.embed_text = AsyncMock(return_value=[0.1] * 1024)
    mock_vs = AsyncMock()

    await _embed_approval_decision(
        approval_id="apr_test123",
        approval_type="tool:send_email",
        summary="Send email to investor",
        risk_level="medium",
        outcome="approved",
        user_id=TEST_USER_ID,
        embedding_service=mock_es,
        vector_store=mock_vs,
        workspace_id=TEST_WORKSPACE_ID,
    )

    mock_vs.upsert.assert_called_once()
    call_kwargs = mock_vs.upsert.call_args
    payload = call_kwargs.kwargs.get("payload") or call_kwargs[1].get("payload")
    assert payload["approval_id"] == "apr_test123"
    assert payload["approval_type"] == "tool:send_email"
    assert payload["decision"] == "approved"
    assert payload["workspace_id"] == TEST_WORKSPACE_ID
    assert "created_at" in payload


@pytest.mark.asyncio
async def test_approval_embed_failure_is_graceful():
    """Embedding failure should not raise."""
    from src.api.routes_approvals import _embed_approval_decision

    mock_es = AsyncMock()
    mock_es.embed_text = AsyncMock(side_effect=RuntimeError("Bedrock down"))
    mock_vs = AsyncMock()

    # Should not raise
    await _embed_approval_decision(
        approval_id="apr_test456",
        approval_type="plan",
        summary="Execute plan",
        risk_level="high",
        outcome="rejected",
        user_id=TEST_USER_ID,
        embedding_service=mock_es,
        vector_store=mock_vs,
    )
    mock_vs.upsert.assert_not_called()


# ---------------------------------------------------------------------------
# 4. Artifacts embedding
# ---------------------------------------------------------------------------


@pytest.mark.asyncio
async def test_artifact_embed_includes_required_fields():
    """_embed_artifact should include all required payload fields."""
    from src.api.routes_artifacts import _embed_artifact

    mock_es = AsyncMock()
    mock_es.embed_text = AsyncMock(return_value=[0.1] * 1024)
    mock_vs = AsyncMock()

    await _embed_artifact(
        artifact_id="art_test123",
        title="quarterly-report.pdf",
        description="Q1 2026 financial report",
        artifact_type="document",
        mime_type="application/pdf",
        user_id=TEST_USER_ID,
        embedding_service=mock_es,
        vector_store=mock_vs,
        workspace_id=TEST_WORKSPACE_ID,
    )

    mock_vs.upsert.assert_called_once()
    call_kwargs = mock_vs.upsert.call_args
    payload = call_kwargs.kwargs.get("payload") or call_kwargs[1].get("payload")
    assert payload["artifact_id"] == "art_test123"
    assert payload["artifact_type"] == "document"
    assert payload["filename"] == "quarterly-report.pdf"
    assert payload["workspace_id"] == TEST_WORKSPACE_ID
    assert "created_at" in payload


@pytest.mark.asyncio
async def test_artifact_embed_skipped_for_empty_text():
    """No embedding when title and description are both empty."""
    from src.api.routes_artifacts import _embed_artifact

    mock_es = AsyncMock()
    mock_vs = AsyncMock()

    await _embed_artifact(
        artifact_id="art_test789",
        title=None,
        description=None,
        artifact_type="",
        mime_type=None,
        user_id=TEST_USER_ID,
        embedding_service=mock_es,
        vector_store=mock_vs,
    )

    mock_es.embed_text.assert_not_called()
    mock_vs.upsert.assert_not_called()


@pytest.mark.asyncio
async def test_artifact_embed_failure_is_graceful():
    """Embedding failure should not raise."""
    from src.api.routes_artifacts import _embed_artifact

    mock_es = AsyncMock()
    mock_es.embed_text = AsyncMock(side_effect=RuntimeError("Bedrock down"))
    mock_vs = AsyncMock()

    # Should not raise
    await _embed_artifact(
        artifact_id="art_fail",
        title="test.txt",
        description="Test file",
        artifact_type="file",
        mime_type="text/plain",
        user_id=TEST_USER_ID,
        embedding_service=mock_es,
        vector_store=mock_vs,
    )
    mock_vs.upsert.assert_not_called()
