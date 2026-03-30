"""Tests for communication tool dispatch prefix resolution."""

from unittest.mock import AsyncMock, MagicMock

import pytest

from src.orchestrator.services import ServiceContainer
from tests.conftest import make_mock_settings


class TestInternalToolServerMapping:
    """Verify _call_internal_tool uses correct namespace prefix per server."""

    @pytest.fixture
    def orchestrator(self):
        from src.orchestrator.jarvis import JarvisOrchestrator

        settings = make_mock_settings()
        db_factory = MagicMock()
        return JarvisOrchestrator(
            settings=settings,
            db_factory=db_factory,
            services=ServiceContainer(),
        )

    async def test_intelligence_tool_uses_intelligence_prefix(self, orchestrator):
        mock_client = AsyncMock()
        mock_result = MagicMock()
        mock_result.is_error = False
        mock_result.structured_content = {"result": {"status": "ok"}}
        mock_client.call_tool = AsyncMock(return_value=mock_result)
        orchestrator._internal_client = mock_client

        await orchestrator._call_internal_tool(
            "search", {"query": "test"}, server_prefix="intelligence"
        )
        mock_client.call_tool.assert_called_once_with("intelligence_search", {"query": "test"})

    async def test_communication_tool_uses_communication_prefix(self, orchestrator):
        mock_client = AsyncMock()
        mock_result = MagicMock()
        mock_result.is_error = False
        mock_result.structured_content = {"result": {"status": "sent"}}
        mock_client.call_tool = AsyncMock(return_value=mock_result)
        orchestrator._internal_client = mock_client

        await orchestrator._call_internal_tool(
            "send_telegram", {"text": "hello"}, server_prefix="communication"
        )
        mock_client.call_tool.assert_called_once_with(
            "communication_send_telegram", {"text": "hello"}
        )

    async def test_send_approval_uses_communication_prefix(self, orchestrator):
        mock_client = AsyncMock()
        mock_result = MagicMock()
        mock_result.is_error = False
        mock_result.structured_content = {"result": {"status": "sent"}}
        mock_client.call_tool = AsyncMock(return_value=mock_result)
        orchestrator._internal_client = mock_client

        await orchestrator._call_internal_tool(
            "send_approval_prompt",
            {"approval_id": "apr_001", "title": "Test", "summary": "test"},
            server_prefix="communication",
        )
        mock_client.call_tool.assert_called_once_with(
            "communication_send_approval_prompt",
            {"approval_id": "apr_001", "title": "Test", "summary": "test"},
        )

    async def test_push_ui_uses_communication_prefix(self, orchestrator):
        mock_client = AsyncMock()
        mock_result = MagicMock()
        mock_result.is_error = False
        mock_result.structured_content = {"result": {"status": "published"}}
        mock_client.call_tool = AsyncMock(return_value=mock_result)
        orchestrator._internal_client = mock_client

        await orchestrator._call_internal_tool(
            "push_ui_update",
            {"surface_id": "daily_brief", "payload": "{}", "user_id": "usr_001"},
            server_prefix="communication",
        )
        mock_client.call_tool.assert_called_once_with(
            "communication_push_ui_update",
            {"surface_id": "daily_brief", "payload": "{}", "user_id": "usr_001"},
        )
