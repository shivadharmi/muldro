"""Test that conversation summarization works correctly.

Note: The dead vector embedding block was removed in Fix-6 (Task 2.2).
_summarize_history now only returns the summary text.
"""

from unittest.mock import AsyncMock, MagicMock, patch

import pytest

from src.orchestrator.services import ServiceContainer
from tests.conftest import make_mock_settings


@pytest.mark.asyncio
async def test_summarize_history_returns_summary():
    """_summarize_history returns the Claude summary text."""
    settings = make_mock_settings()
    settings.use_bedrock = False

    with patch(
        "src.orchestrator.context_assembler.complete_text",
        AsyncMock(return_value="Summary of conversation."),
    ):
        from src.orchestrator.jarvis import JarvisOrchestrator

        orch = JarvisOrchestrator(
            settings=settings, db_factory=MagicMock(), services=ServiceContainer()
        )

        lines = ["User: Hello", "Assistant: Hi there", "User: What's up?"]
        summary = await orch._context._summarize_history(lines, conversation_id="conv_test123")

        assert summary == "Summary of conversation."


@pytest.mark.asyncio
async def test_summarize_history_without_conversation_id():
    """Without conversation_id, summarization still works."""
    settings = make_mock_settings()
    settings.use_bedrock = False

    with patch(
        "src.orchestrator.context_assembler.complete_text",
        AsyncMock(return_value="Summary."),
    ):
        from src.orchestrator.jarvis import JarvisOrchestrator

        orch = JarvisOrchestrator(
            settings=settings, db_factory=MagicMock(), services=ServiceContainer()
        )

        lines = ["User: Hello"]
        summary = await orch._context._summarize_history(lines)

        assert summary == "Summary."
