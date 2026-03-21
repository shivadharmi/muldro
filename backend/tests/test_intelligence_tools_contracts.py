"""Contract tests for intelligence_server.py tool functions.

Verifies correct arg order, field names, and service method calls.
Includes regression tests for previously-fixed tools.
"""

from unittest.mock import AsyncMock, MagicMock, patch

import pytest

# Module under test
from src.tools import intelligence_server


@pytest.fixture(autouse=True)
def configure_intelligence_server():
    """Configure the intelligence server with mocked dependencies."""
    mock_db_factory = MagicMock()

    # Build a mock async context manager for _get_db
    mock_session = AsyncMock()
    mock_session.execute = AsyncMock()
    mock_session.add = MagicMock()
    mock_session.flush = AsyncMock()
    mock_session.commit = AsyncMock()
    mock_session.rollback = AsyncMock()

    async_cm = AsyncMock()
    async_cm.__aenter__ = AsyncMock(return_value=mock_session)
    async_cm.__aexit__ = AsyncMock(return_value=False)
    mock_db_factory.return_value = async_cm

    mock_settings = MagicMock()
    mock_services = MagicMock()

    intelligence_server.configure(mock_db_factory, mock_settings, mock_services)

    yield {
        "db_factory": mock_db_factory,
        "session": mock_session,
        "settings": mock_settings,
        "services": mock_services,
    }

    # Clean up
    intelligence_server._db_factory = None
    intelligence_server._settings = None
    intelligence_server._services = None


class TestIngestEvent:
    """Regression: ingest_event arg order was previously broken."""

    async def test_calls_processor_with_correct_args(self, configure_intelligence_server):
        ctx = configure_intelligence_server
        processor = AsyncMock()
        processor.process = AsyncMock(return_value={"event_id": "evt_123", "importance_score": 0.8})
        ctx["services"].event_processor = processor

        result = await intelligence_server.ingest_event(
            user_id="usr_1",
            source="gmail",
            event_type="email_received",
            entity_type="message_thread",
            entity_id="thread_1",
            title="Test email",
            workspace_id="ws_1",
        )

        assert result["status"] == "ingested"
        processor.process.assert_called_once()
        call_args = processor.process.call_args
        assert call_args[0][0] == "usr_1"  # user_id is first positional arg


class TestUpdateExecution:
    """Fix: update_execution must use transition_run() not direct mutation."""

    async def test_uses_transition_run(self, configure_intelligence_server):
        ctx = configure_intelligence_server

        # Mock a run object
        mock_run = MagicMock()
        mock_run.status = "running"
        mock_run.run_id = "run_123"

        mock_result = MagicMock()
        mock_result.scalar_one_or_none = MagicMock(return_value=mock_run)
        ctx["session"].execute = AsyncMock(return_value=mock_result)

        with patch("src.services.execution_state.transition_run") as mock_transition:
            result = await intelligence_server.update_execution(
                execution_id="run_123",
                status="completed",
                workspace_id="ws_1",
            )

            mock_transition.assert_called_once_with(mock_run, "completed")
            assert result["status"] == "updated"

    async def test_invalid_transition_returns_error(self, configure_intelligence_server):
        ctx = configure_intelligence_server

        mock_run = MagicMock()
        mock_run.status = "completed"
        mock_run.run_id = "run_123"

        mock_result = MagicMock()
        mock_result.scalar_one_or_none = MagicMock(return_value=mock_run)
        ctx["session"].execute = AsyncMock(return_value=mock_result)

        from src.services.execution_state import InvalidTransitionError

        with patch(
            "src.services.execution_state.transition_run",
            side_effect=InvalidTransitionError("run", "run_123", "completed", "running"),
        ):
            result = await intelligence_server.update_execution(
                execution_id="run_123",
                status="running",
                workspace_id="ws_1",
            )
            assert result["status"] == "error"
            assert "Invalid" in result["error"]


class TestGetBriefing:
    """Regression: get_briefing briefing_date param was previously broken."""

    async def test_calls_presenter_with_date(self, configure_intelligence_server):
        ctx = configure_intelligence_server
        presenter = AsyncMock()
        mock_briefing = MagicMock()
        mock_briefing.briefing_id = "br_1"
        mock_briefing.briefing_date = "2025-01-01"
        mock_briefing.headline = "Test"
        mock_briefing.top_priorities = []
        mock_briefing.changes_since_last = []
        mock_briefing.pending_approvals = []
        mock_briefing.recommended_actions = []
        mock_briefing.full_text = "text"
        presenter.generate_briefing = AsyncMock(return_value=mock_briefing)
        ctx["services"].presenter = presenter

        result = await intelligence_server.get_briefing(
            user_id="usr_1", date="2025-06-15", workspace_id="ws_1"
        )

        assert result["status"] == "ok"
        presenter.generate_briefing.assert_called_once()
        call_args = presenter.generate_briefing.call_args
        assert call_args[0][0] == "usr_1"


class TestGetDbSessionManagement:
    """Fix: _get_db must use async with for proper session lifecycle."""

    async def test_get_db_uses_context_manager(self, configure_intelligence_server):
        ctx = configure_intelligence_server

        async with intelligence_server._get_db() as session:
            assert session is not None

        # The factory was called and context manager was used
        ctx["db_factory"].assert_called_once()


class TestSearchMemory:
    async def test_calls_memory_service_retrieve(self, configure_intelligence_server):
        ctx = configure_intelligence_server
        memory_svc = AsyncMock()
        memory_svc.retrieve = AsyncMock(return_value=[{"fact": "test"}])
        ctx["services"].memory_service = memory_svc

        result = await intelligence_server.search_memory(
            user_id="usr_1", query="test query", workspace_id="ws_1"
        )

        assert result["count"] == 1
        memory_svc.retrieve.assert_called_once()
