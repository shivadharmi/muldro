"""Test that conversation summaries are embedded into Qdrant."""

from unittest.mock import AsyncMock, MagicMock, patch

import pytest

from src.orchestrator.services import ServiceContainer
from tests.conftest import make_mock_settings


@pytest.mark.asyncio
async def test_summarize_history_embeds_to_qdrant():
    """After summarizing history, the summary should be upserted to Qdrant."""
    settings = make_mock_settings()
    settings.use_bedrock = False

    mock_vector_store = AsyncMock()
    mock_embedding_service = AsyncMock()
    mock_embedding_service.embed_text = AsyncMock(return_value=[0.1] * 1024)

    with patch("src.orchestrator.jarvis.get_anthropic_client") as mock_get_client:
        mock_client = MagicMock()
        summary_response = MagicMock()
        summary_response.content = [MagicMock(type="text", text="Summary of conversation.")]
        mock_client.messages.create = AsyncMock(return_value=summary_response)
        mock_get_client.return_value = mock_client

        from src.orchestrator.jarvis import JarvisOrchestrator

        orch = JarvisOrchestrator(
            settings=settings, db_factory=MagicMock(), services=ServiceContainer()
        )
        orch._vector_store = mock_vector_store
        orch._embedding_service = mock_embedding_service
        orch._current_user_id = "usr_test"

        lines = ["User: Hello", "Assistant: Hi there", "User: What's up?"]
        summary = await orch._summarize_history(lines, conversation_id="conv_test123")

        assert summary == "Summary of conversation."
        mock_vector_store.upsert.assert_called_once()
        call_kwargs = mock_vector_store.upsert.call_args.kwargs
        assert call_kwargs["collection"] == "conversations"
        assert call_kwargs["id"] == "conv_test123"
        assert call_kwargs["user_id"] == "usr_test"
        payload = call_kwargs["payload"]
        assert payload["conversation_id"] == "conv_test123"
        assert payload["message_count"] == 3


@pytest.mark.asyncio
async def test_summarize_history_no_embed_without_conversation_id():
    """Without conversation_id, no embedding should happen."""
    settings = make_mock_settings()
    settings.use_bedrock = False

    mock_vector_store = AsyncMock()
    mock_embedding_service = AsyncMock()

    with patch("src.orchestrator.jarvis.get_anthropic_client") as mock_get_client:
        mock_client = MagicMock()
        summary_response = MagicMock()
        summary_response.content = [MagicMock(type="text", text="Summary.")]
        mock_client.messages.create = AsyncMock(return_value=summary_response)
        mock_get_client.return_value = mock_client

        from src.orchestrator.jarvis import JarvisOrchestrator

        orch = JarvisOrchestrator(
            settings=settings, db_factory=MagicMock(), services=ServiceContainer()
        )
        orch._vector_store = mock_vector_store
        orch._embedding_service = mock_embedding_service

        lines = ["User: Hello"]
        summary = await orch._summarize_history(lines)

        assert summary == "Summary."
        mock_vector_store.upsert.assert_not_called()
