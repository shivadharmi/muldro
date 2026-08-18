"""Task 11: the Opus Planner fast-path.

Triage flags each ingested event ``actionable`` (persisted on the stored
``NormalizedEvent.importance_signals``). ``run_perception_cycle`` must skip the
Planner call entirely (Step 3) if no event from this poll was triaged actionable
— saving an unconditional Opus call on pure-noise polls.
"""

from unittest.mock import AsyncMock, MagicMock

import pytest

from tests.conftest import TEST_USER_ID, TEST_WORKSPACE_ID, make_mock_settings, make_raw_event


def _wire_common_mocks(pr, raw_events=None):
    """Wire the collaborator mocks shared by all cycle runs."""
    if raw_events is None:
        raw_events = [make_raw_event()]
    pr._poller.poll = AsyncMock(return_value=(raw_events, "cursor_123", None, "opaque"))
    pr._poller.ingest_raw_events = AsyncMock(return_value=["New event"])
    pr._poller.update_cursor = AsyncMock()
    pr._apply_perception_policy_from_planner = AsyncMock()
    pr._queue_perception_plan = AsyncMock(return_value=None)
    pr._events.publish_event = AsyncMock()
    pr._trace_manager = MagicMock()
    pr._trace_manager.start_trace.return_value = MagicMock(trace_id="trace_1")
    pr._trace_manager.finish_trace = AsyncMock()
    pr._budget = MagicMock()
    pr._budget.get_budget_status = AsyncMock(return_value=MagicMock())
    pr._budget.should_allow_perception.return_value = True
    return raw_events


def _make_orchestrator(settings, execute_result=None):
    from src.orchestrator.muldro import MuldroOrchestrator
    from src.orchestrator.services import ServiceContainer

    mock_db = AsyncMock()
    mock_db.commit = AsyncMock()
    custom_result = execute_result is not None
    mock_exec_result = execute_result or MagicMock()
    mock_exec_result.scalar_one_or_none.return_value = None
    mock_exec_result.scalars.return_value.all.return_value = []
    if not custom_result:
        mock_exec_result.all.return_value = []
    mock_db.execute.return_value = mock_exec_result
    db_ctx = AsyncMock()
    db_ctx.__aenter__ = AsyncMock(return_value=mock_db)
    db_ctx.__aexit__ = AsyncMock(return_value=False)
    db_factory = MagicMock(return_value=db_ctx)

    return MuldroOrchestrator(settings=settings, db_factory=db_factory, services=ServiceContainer())


class TestPlannerFastPathOnTriage:
    @pytest.mark.asyncio
    async def test_planner_skipped_when_no_actionable(self):
        """No actionable event → Planner not called."""
        settings = make_mock_settings()
        orch = _make_orchestrator(settings)
        pr = orch._perception
        _wire_common_mocks(pr)
        pr._invoker.call_agent = AsyncMock(return_value="planner output")
        pr._has_actionable = AsyncMock(return_value=False)

        result = await orch.run_perception_cycle(
            source="github",
            user_id=TEST_USER_ID,
            workspace_id=TEST_WORKSPACE_ID,
        )

        assert result["status"] == "completed"
        called_agents = [c.args[0] for c in pr._invoker.call_agent.call_args_list]
        assert "planner" not in called_agents
        assert result["planner"] is None
        pr._has_actionable.assert_awaited_once()

    @pytest.mark.asyncio
    async def test_planner_runs_when_actionable(self):
        """An actionable stored event → Planner called.

        Exercises the real ``_has_actionable`` query logic (not mocked): the
        DB execute() is wired to return a row whose importance_signals carries
        actionable=True, matching what triage persists on NormalizedEvent.
        """
        settings = make_mock_settings()
        mock_exec_result = MagicMock()
        mock_exec_result.all.return_value = [({"actionable": True, "tier": "act"},)]
        orch = _make_orchestrator(settings, execute_result=mock_exec_result)
        pr = orch._perception
        _wire_common_mocks(pr)
        pr._invoker.call_agent = AsyncMock(return_value="planner output")
        # _has_actionable is NOT mocked here — real query path runs.

        result = await orch.run_perception_cycle(
            source="github",
            user_id=TEST_USER_ID,
            workspace_id=TEST_WORKSPACE_ID,
        )

        assert result["status"] == "completed"
        called_agents = [c.args[0] for c in pr._invoker.call_agent.call_args_list]
        assert "planner" in called_agents
        assert result["planner"] == "planner output"


class TestHasActionableHelper:
    @pytest.mark.asyncio
    async def test_has_actionable_empty_raw_events_returns_false(self):
        """No raw events (nothing ingested) → trivially not actionable, no query."""
        settings = make_mock_settings()
        orch = _make_orchestrator(settings)
        pr = orch._perception

        assert await pr._has_actionable([], TEST_WORKSPACE_ID) is False

    @pytest.mark.asyncio
    async def test_has_actionable_true_when_any_row_actionable(self):
        settings = make_mock_settings()
        mock_exec_result = MagicMock()
        mock_exec_result.all.return_value = [
            ({"actionable": False},),
            ({"actionable": True},),
        ]
        orch = _make_orchestrator(settings, execute_result=mock_exec_result)
        pr = orch._perception

        raw_events = [make_raw_event()]
        assert await pr._has_actionable(raw_events, TEST_WORKSPACE_ID) is True

    @pytest.mark.asyncio
    async def test_has_actionable_false_when_all_rows_non_actionable(self):
        settings = make_mock_settings()
        mock_exec_result = MagicMock()
        mock_exec_result.all.return_value = [
            ({"actionable": False},),
            ({"actionable": False, "tier": "skip"},),
        ]
        orch = _make_orchestrator(settings, execute_result=mock_exec_result)
        pr = orch._perception

        raw_events = [make_raw_event()]
        assert await pr._has_actionable(raw_events, TEST_WORKSPACE_ID) is False
