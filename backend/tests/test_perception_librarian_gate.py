"""Task 10 Change A: the routine Librarian extraction pass in the perception
cycle is redundant with the tier-gated worker consumers (which own entity/
memory extraction), so run_perception_cycle must NOT call the librarian agent.
"""

from unittest.mock import AsyncMock, MagicMock

import pytest

from tests.conftest import TEST_USER_ID, TEST_WORKSPACE_ID, make_mock_settings


def _wire_common_mocks(pr):
    """Wire the collaborator mocks shared by both cycle runs."""
    pr._poller.poll = AsyncMock(
        return_value=([MagicMock(entity_id=None)], "cursor_123", None, "opaque")
    )
    pr._poller.ingest_raw_events = AsyncMock(return_value=["New PR opened"])
    pr._poller.update_cursor = AsyncMock()
    pr._apply_perception_policy_from_planner = AsyncMock()
    pr._queue_perception_plan = AsyncMock(return_value=None)
    # Task 11 fast-path: gate is orthogonal to what this file tests (the
    # librarian call), so force it "actionable" so the planner still runs.
    pr._has_actionable = AsyncMock(return_value=True)
    pr._events.publish_event = AsyncMock()
    pr._trace_manager = MagicMock()
    pr._trace_manager.start_trace.return_value = MagicMock(trace_id="trace_1")
    pr._trace_manager.finish_trace = AsyncMock()
    pr._budget = MagicMock()
    pr._budget.get_budget_status = AsyncMock(return_value=MagicMock())
    pr._budget.should_allow_perception.return_value = True


def _make_orchestrator(settings):
    from src.orchestrator.jarvis import JarvisOrchestrator
    from src.orchestrator.services import ServiceContainer

    mock_db = AsyncMock()
    mock_db.commit = AsyncMock()
    mock_exec_result = MagicMock()
    mock_exec_result.scalar_one_or_none.return_value = None
    mock_exec_result.scalars.return_value.all.return_value = []
    mock_db.execute.return_value = mock_exec_result
    db_ctx = AsyncMock()
    db_ctx.__aenter__ = AsyncMock(return_value=mock_db)
    db_ctx.__aexit__ = AsyncMock(return_value=False)
    db_factory = MagicMock(return_value=db_ctx)

    return JarvisOrchestrator(settings=settings, db_factory=db_factory, services=ServiceContainer())


class TestLibrarianGateOnTriage:
    @pytest.mark.asyncio
    async def test_skips_librarian_call(self):
        """The worker owns extraction — run_perception_cycle must not call the
        librarian agent at all."""
        settings = make_mock_settings()
        orch = _make_orchestrator(settings)
        pr = orch._perception
        _wire_common_mocks(pr)
        pr._invoker.call_agent = AsyncMock(return_value="planner output")

        result = await orch.run_perception_cycle(
            source="github",
            user_id=TEST_USER_ID,
            workspace_id=TEST_WORKSPACE_ID,
        )

        assert result["status"] == "completed"
        called_agents = [c.args[0] for c in pr._invoker.call_agent.call_args_list]
        assert "librarian" not in called_agents
        assert "planner" in called_agents
        assert result["librarian"] is None
