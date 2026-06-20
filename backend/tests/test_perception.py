"""Tests for perception layer — DB-backed coordinator + policy service integration."""

from datetime import datetime, timedelta, timezone
from unittest.mock import AsyncMock, MagicMock, patch

import pytest

from src.models.perception_state import PerceptionState
from src.orchestrator.perception import PerceptionCoordinator
from src.services.perception_policy import (
    CIRCUIT_COOLDOWN_S,
    CIRCUIT_FAILURE_THRESHOLD,
    PerceptionPolicyService,
)

# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------


def _make_state(**overrides) -> PerceptionState:
    defaults = dict(
        state_id="pst_test",
        workspace_id="ws_test",
        user_id="usr_test",
        source="gmail",
        mode="poll",
        base_interval_s=300,
        effective_interval_s=300,
        next_run_at=None,
        last_run_at=None,
        agent_interval_s=None,
        watch_entities=None,
        consecutive_failures=0,
        last_error=None,
        circuit_state="closed",
        circuit_opened_at=None,
        pending_run=False,
        signal_source=None,
        signal_at=None,
        last_event_count=0,
        total_runs=0,
    )
    defaults.update(overrides)
    return PerceptionState(**defaults)


def _mock_db():
    db = AsyncMock()
    db.flush = AsyncMock()
    db.commit = AsyncMock()
    db.add = MagicMock()
    return db


# ---------------------------------------------------------------------------
# Coordinator: workspace passthrough
# ---------------------------------------------------------------------------


class TestWorkspacePassthrough:
    @pytest.mark.asyncio
    async def test_run_due_cycles_passes_workspace_id(self):
        """run_due_cycles should pass workspace_id to run_perception_cycle."""
        orch = MagicMock()
        orch.run_perception_cycle = AsyncMock(
            return_value={"status": "completed", "source": "gmail", "events": 2}
        )
        orch._publish_event = AsyncMock()

        state = _make_state(
            next_run_at=datetime.now(timezone.utc) - timedelta(seconds=10),
        )

        mock_db = _mock_db()
        mock_svc = AsyncMock(spec=PerceptionPolicyService)
        mock_svc.get_due_sources = AsyncMock(return_value=[state])
        mock_svc.record_success = AsyncMock(return_value=state)

        mock_cm = AsyncMock()
        mock_cm.__aenter__ = AsyncMock(return_value=mock_db)
        mock_cm.__aexit__ = AsyncMock(return_value=False)
        mock_factory_fn = MagicMock(return_value=mock_cm)

        with (
            patch("src.models.database.get_session_factory", return_value=mock_factory_fn),
            patch(
                "src.services.perception_policy.PerceptionPolicyService",
                return_value=mock_svc,
            ),
        ):
            coord = PerceptionCoordinator(orch, user_id="usr_test", workspace_id="ws_test")
            await coord.run_due_cycles()

        for call in orch.run_perception_cycle.call_args_list:
            assert call.kwargs.get("workspace_id") == "ws_test"


# ---------------------------------------------------------------------------
# Circuit breaker (via policy service)
# ---------------------------------------------------------------------------


class TestCircuitBreaker:
    @pytest.mark.asyncio
    async def test_circuit_opens_after_consecutive_failures(self):
        """After CIRCUIT_FAILURE_THRESHOLD failures, circuit should open."""
        db = _mock_db()
        state = _make_state(consecutive_failures=CIRCUIT_FAILURE_THRESHOLD - 1)
        svc = PerceptionPolicyService(db)

        # Use "unknown"-classified error so default threshold (3) applies
        result = await svc.record_failure(state, "unknown internal error")
        assert result.circuit_state == "open"
        assert result.circuit_opened_at is not None

    @pytest.mark.asyncio
    async def test_circuit_closes_after_success(self):
        """A success should reset the circuit breaker."""
        db = _mock_db()
        state = _make_state(
            circuit_state="half_open",
            consecutive_failures=3,
        )
        svc = PerceptionPolicyService(db)

        result = await svc.record_success(state, event_count=1)
        assert result.circuit_state == "closed"
        assert result.consecutive_failures == 0

    @pytest.mark.asyncio
    async def test_exception_records_failure(self):
        """Exceptions should increment failure count."""
        db = _mock_db()
        state = _make_state()
        svc = PerceptionPolicyService(db)

        result = await svc.record_failure(state, "connection lost")
        assert result.consecutive_failures == 1
        assert result.last_error == "connection lost"

    def test_circuit_reopens_after_cooldown(self):
        """After cooldown, circuit should transition to half_open."""
        opened = datetime.now(timezone.utc) - timedelta(seconds=CIRCUIT_COOLDOWN_S + 10)
        state = _make_state(
            circuit_state="open",
            circuit_opened_at=opened,
            consecutive_failures=5,
        )
        svc = PerceptionPolicyService(_mock_db())
        svc._maybe_reopen_circuit(state, datetime.now(timezone.utc))

        assert state.circuit_state == "half_open"
        assert state.consecutive_failures == 0


# ---------------------------------------------------------------------------
# Adaptive backoff (via policy service)
# ---------------------------------------------------------------------------


class TestAdaptiveBackoff:
    def test_no_backoff_on_zero_failures(self):
        """Sources with no failures should use base interval."""
        state = _make_state(base_interval_s=300, consecutive_failures=0)
        svc = PerceptionPolicyService(_mock_db())
        assert svc._compute_effective_interval(state) == 300

    def test_backoff_doubles_per_failure(self):
        """After 1 failure, interval doubles to 600s."""
        state = _make_state(base_interval_s=300, consecutive_failures=1)
        svc = PerceptionPolicyService(_mock_db())
        assert svc._compute_effective_interval(state) == 600

    def test_backoff_capped_at_8x(self):
        """Backoff should not exceed 8x the base interval."""
        state = _make_state(base_interval_s=300, consecutive_failures=10)
        svc = PerceptionPolicyService(_mock_db())
        # 300 * 8 = 2400
        assert svc._compute_effective_interval(state) == 2400

    @pytest.mark.asyncio
    async def test_success_resets_failure_count(self):
        """A successful cycle should reset consecutive failure count."""
        db = _mock_db()
        state = _make_state(consecutive_failures=3)
        svc = PerceptionPolicyService(db)

        result = await svc.record_success(state, event_count=2)
        assert result.consecutive_failures == 0


# ---------------------------------------------------------------------------
# Scheduler perception tick
# ---------------------------------------------------------------------------


class TestSchedulerPerceptionTick:
    @pytest.mark.asyncio
    @patch("src.services.scheduler.get_session_factory")
    async def test_tick_perception_runs_due_sources(self, mock_factory):
        """_tick_perception should call run_perception_cycle for due sources."""
        from src.services.scheduler import SchedulerLoop

        state = _make_state(
            pending_run=True,
            next_run_at=datetime.now(timezone.utc) - timedelta(seconds=10),
        )

        orchestrator = MagicMock()
        orchestrator.run_perception_cycle = AsyncMock(
            return_value={"status": "completed", "events": 3}
        )
        orchestrator._budget = MagicMock()
        orchestrator._budget.get_budget_status = AsyncMock(return_value={"mode": "normal"})
        orchestrator._budget.should_allow_perception = MagicMock(return_value=True)
        orchestrator._budget.get_perception_interval_multiplier = MagicMock(return_value=1)

        mock_db = _mock_db()
        mock_cm = AsyncMock()
        mock_cm.__aenter__ = AsyncMock(return_value=mock_db)
        mock_cm.__aexit__ = AsyncMock(return_value=False)
        mock_factory.return_value = MagicMock(return_value=mock_cm)

        mock_settings = MagicMock()
        mock_settings.max_perception_per_tick = 5
        scheduler = SchedulerLoop(mock_settings, orchestrator=orchestrator)

        mock_svc_instance = AsyncMock()
        mock_svc_instance.get_due_sources_all_users = AsyncMock(return_value=[state])
        mock_svc_instance.record_success = AsyncMock(return_value=state)

        with (
            patch(
                "src.services.perception_policy.PerceptionPolicyService",
                return_value=mock_svc_instance,
            ),
            patch.object(scheduler, "_resolve_workspace", new=AsyncMock(return_value="ws_test")),
        ):
            await scheduler._tick_perception(mock_factory.return_value)

        orchestrator.run_perception_cycle.assert_awaited_once_with(
            "gmail", user_id="usr_test", workspace_id="ws_test"
        )

    @pytest.mark.asyncio
    async def test_no_orchestrator_no_perception(self):
        """Without orchestrator, _tick_perception should be a no-op."""
        from src.services.scheduler import SchedulerLoop

        scheduler = SchedulerLoop(MagicMock(), orchestrator=None)
        # Should not raise
        await scheduler._tick_perception(MagicMock())

    @pytest.mark.asyncio
    @patch("src.services.scheduler.get_session_factory")
    async def test_generic_cycle_exception_is_transient(self, mock_factory):
        """A bare exception escaping run_perception_cycle must fail-safe to transient.

        An uncategorized cycle failure should route through the transient sentinel
        (circuit threshold 6), not classify as unknown (the meaningless middle
        threshold of 3). Asserts via the classification of the error string the
        scheduler tick passes to record_failure.
        """
        from src.services.perception_policy import classify_error
        from src.services.scheduler import SchedulerLoop

        state = _make_state(
            pending_run=True,
            next_run_at=datetime.now(timezone.utc) - timedelta(seconds=10),
        )

        orchestrator = MagicMock()
        orchestrator.run_perception_cycle = AsyncMock(side_effect=RuntimeError("boom"))
        orchestrator._budget = MagicMock()
        orchestrator._budget.get_budget_status = AsyncMock(return_value={"mode": "normal"})
        orchestrator._budget.should_allow_perception = MagicMock(return_value=True)
        orchestrator._budget.get_perception_interval_multiplier = MagicMock(return_value=1)

        mock_db = _mock_db()
        mock_cm = AsyncMock()
        mock_cm.__aenter__ = AsyncMock(return_value=mock_db)
        mock_cm.__aexit__ = AsyncMock(return_value=False)
        mock_factory.return_value = MagicMock(return_value=mock_cm)

        mock_settings = MagicMock()
        mock_settings.max_perception_per_tick = 5
        mock_settings.perception_concurrency = 1
        scheduler = SchedulerLoop(mock_settings, orchestrator=orchestrator)

        recorded: list[str] = []

        async def _capture_failure(_state, error):
            recorded.append(error)
            return _state

        mock_svc_instance = AsyncMock()
        mock_svc_instance.get_due_sources_all_users = AsyncMock(return_value=[state])
        mock_svc_instance.record_failure = AsyncMock(side_effect=_capture_failure)

        with (
            patch(
                "src.services.perception_policy.PerceptionPolicyService",
                return_value=mock_svc_instance,
            ),
            patch.object(scheduler, "_resolve_workspace", new=AsyncMock(return_value="ws_test")),
        ):
            await scheduler._tick_perception(mock_factory.return_value)

        assert recorded, "record_failure should have been called for the escaped exception"
        assert classify_error(recorded[0]) == "transient"


# ---------------------------------------------------------------------------
# Coordinator: generic cycle exception fail-safe
# ---------------------------------------------------------------------------


class TestCoordinatorGenericException:
    @pytest.mark.asyncio
    async def test_generic_cycle_exception_is_transient(self):
        """A bare exception escaping run_perception_cycle in run_due_cycles fails safe.

        The PerceptionCoordinator outer handler must route an uncategorized error
        through the transient sentinel (threshold 6), not unknown (threshold 3).
        """
        from src.services.perception_policy import classify_error

        orch = MagicMock()
        orch.run_perception_cycle = AsyncMock(side_effect=RuntimeError("boom"))
        orch._publish_event = AsyncMock()

        state = _make_state(
            next_run_at=datetime.now(timezone.utc) - timedelta(seconds=10),
        )

        recorded: list[str] = []

        async def _capture_failure(_state, error):
            recorded.append(error)
            return _state

        mock_db = _mock_db()
        mock_svc = AsyncMock(spec=PerceptionPolicyService)
        mock_svc.get_due_sources = AsyncMock(return_value=[state])
        mock_svc.record_failure = AsyncMock(side_effect=_capture_failure)

        mock_cm = AsyncMock()
        mock_cm.__aenter__ = AsyncMock(return_value=mock_db)
        mock_cm.__aexit__ = AsyncMock(return_value=False)
        mock_factory_fn = MagicMock(return_value=mock_cm)

        with (
            patch("src.models.database.get_session_factory", return_value=mock_factory_fn),
            patch(
                "src.services.perception_policy.PerceptionPolicyService",
                return_value=mock_svc,
            ),
        ):
            coord = PerceptionCoordinator(orch, user_id="usr_test", workspace_id="ws_test")
            await coord.run_due_cycles()

        assert recorded, "record_failure should have been called for the escaped exception"
        assert classify_error(recorded[0]) == "transient"


# ---------------------------------------------------------------------------
# observe_source schedule skips when perception_state manages
# ---------------------------------------------------------------------------


class TestObserveSourceSkip:
    @pytest.mark.asyncio
    @patch("src.services.scheduler.schedule_dispatch.get_session_factory")
    async def test_observe_source_skips_when_managed(self, mock_factory):
        """observe_source should skip when perception_state row is active."""
        from src.services.scheduler import SchedulerLoop

        state = _make_state(mode="poll")

        mock_db = _mock_db()
        mock_result = MagicMock()
        mock_result.scalar_one_or_none = MagicMock(return_value=state)
        mock_db.execute = AsyncMock(return_value=mock_result)

        mock_cm = AsyncMock()
        mock_cm.__aenter__ = AsyncMock(return_value=mock_db)
        mock_cm.__aexit__ = AsyncMock(return_value=False)
        mock_factory.return_value = MagicMock(return_value=mock_cm)

        orchestrator = MagicMock()
        orchestrator.run_perception_cycle = AsyncMock()

        scheduler = SchedulerLoop(MagicMock(), orchestrator=orchestrator)

        sched = MagicMock()
        sched.action_type = "observe_source"
        sched.action_config = {"source": "gmail"}
        sched.user_id = "usr_test"

        with patch.object(scheduler, "_resolve_workspace", new=AsyncMock(return_value="ws_test")):
            await scheduler._fire(sched)

        # Should NOT have called run_perception_cycle
        orchestrator.run_perception_cycle.assert_not_awaited()


# ---------------------------------------------------------------------------
# Perception decision extraction (Pass 3)
# ---------------------------------------------------------------------------


class TestPerceptionDecisionExtraction:
    def test_extracts_valid_policy(self):
        """Should parse perception_policy JSON from planner text."""
        from src.orchestrator.jarvis import JarvisOrchestrator

        text = """Here is my analysis.
"perception_policy": {"next_check_seconds": 120, "urgency": "high", "reasoning": "active thread"}
Done."""
        policy = JarvisOrchestrator._extract_perception_policy(text)
        assert policy is not None
        assert policy.next_check_seconds == 120
        assert policy.urgency == "high"

    def test_returns_none_when_no_policy(self):
        """Should return None when planner doesn't include perception_policy."""
        from src.orchestrator.jarvis import JarvisOrchestrator

        text = '{"decision": "acknowledge", "reasoning": "nothing important"}'
        policy = JarvisOrchestrator._extract_perception_policy(text)
        assert policy is None

    def test_returns_none_on_empty_text(self):
        from src.orchestrator.jarvis import JarvisOrchestrator

        assert JarvisOrchestrator._extract_perception_policy("") is None
        assert JarvisOrchestrator._extract_perception_policy(None) is None

    def test_returns_none_on_invalid_json(self):
        from src.orchestrator.jarvis import JarvisOrchestrator

        text = '"perception_policy": {not valid json}'
        policy = JarvisOrchestrator._extract_perception_policy(text)
        assert policy is None

    def test_extracts_with_watch_entities(self):
        from src.orchestrator.jarvis import JarvisOrchestrator

        text = '"perception_policy": {"next_check_seconds": 60, "watch_entities": ["ent_abc"]}'
        policy = JarvisOrchestrator._extract_perception_policy(text)
        assert policy is not None
        assert policy.watch_entities == ["ent_abc"]


# ---------------------------------------------------------------------------
# Intent classifier source validation (Pass 3)
# ---------------------------------------------------------------------------


class TestIntentClassifierSources:
    def test_valid_sources_constant(self):
        from src.orchestrator.intent_classifier import VALID_PERCEPTION_SOURCES

        assert "gmail" in VALID_PERCEPTION_SOURCES
        assert "calendar" in VALID_PERCEPTION_SOURCES
        assert "slack" in VALID_PERCEPTION_SOURCES
        assert "github" in VALID_PERCEPTION_SOURCES


# ---------------------------------------------------------------------------
# Cross-source synthesis uses internal path (Phase 4)
# ---------------------------------------------------------------------------


class TestCrossSynthesisInternalPath:
    def test_run_cross_source_synthesis_exists(self):
        """Verify run_cross_source_synthesis method exists on orchestrator."""
        from src.orchestrator.jarvis import JarvisOrchestrator

        assert hasattr(JarvisOrchestrator, "run_cross_source_synthesis")

    def test_scheduler_uses_internal_synthesis_path(self):
        """Cross-source synthesis should use run_cross_source_synthesis, not process_message."""
        import inspect

        from src.services.scheduler import SchedulerLoop

        source = inspect.getsource(SchedulerLoop._tick_perception)
        # Should use run_cross_source_synthesis, not process_message
        assert "run_cross_source_synthesis" in source
        assert "process_message(" not in source


# ---------------------------------------------------------------------------
# DLQ wiring in perception cycle (Phase 6)
# ---------------------------------------------------------------------------


class TestPerceptionCycleDLQ:
    def test_perception_cycle_has_dlq_enqueue(self):
        """run_perception_cycle's except block should enqueue to DLQ."""
        import inspect

        from src.orchestrator.perception_runner import PerceptionRunner

        source = inspect.getsource(PerceptionRunner.run_perception_cycle)
        assert "DeadLetterService" in source
        assert "dlq.enqueue" in source


# ---------------------------------------------------------------------------
# Relevance assessment integration in perception cycle (Spec 4A)
# ---------------------------------------------------------------------------


class TestPerceptionRelevanceAssessment:
    """Test relevance assessment integration in run_perception_cycle."""

    @pytest.mark.asyncio
    async def test_perception_cycle_calls_relevance_assessor(self):
        """After librarian extraction, relevance should be assessed."""
        from unittest.mock import AsyncMock, MagicMock, patch

        from tests.conftest import TEST_USER_ID, TEST_WORKSPACE_ID, make_mock_settings

        settings = make_mock_settings()

        with (
            patch("src.orchestrator.jarvis.get_anthropic_client") as mock_get_client,
            patch("src.services.relevance_assessor.assess_relevance") as mock_assess,
        ):
            mock_client = AsyncMock()
            mock_get_client.return_value = mock_client

            from src.services.relevance_assessor import RelevanceAssessment

            mock_assess.return_value = RelevanceAssessment(
                relevance_score=0.5,
                reasoning="Moderately relevant",
                urgency="today",
                notification_tier="briefing",
            )

            from src.orchestrator.jarvis import JarvisOrchestrator
            from src.orchestrator.services import ServiceContainer

            mock_db = AsyncMock()
            mock_db.commit = AsyncMock()
            # EngagementService queries return None (not suppressed, no history)
            mock_exec_result = MagicMock()
            mock_exec_result.scalar_one_or_none.return_value = None
            mock_exec_result.scalars.return_value.all.return_value = []
            mock_db.execute.return_value = mock_exec_result
            db_ctx = AsyncMock()
            db_ctx.__aenter__ = AsyncMock(return_value=mock_db)
            db_ctx.__aexit__ = AsyncMock(return_value=False)
            db_factory = MagicMock(return_value=db_ctx)

            orch = JarvisOrchestrator(
                settings=settings,
                db_factory=db_factory,
                services=ServiceContainer(),
            )

            # Perception now lives on PerceptionRunner; connector I/O on its
            # ConnectorPoller — retarget mocks accordingly.
            pr = orch._perception
            pr._poller.poll = AsyncMock(
                return_value=(
                    [MagicMock(entity_id=None)],
                    "cursor_123",
                    None,
                    "opaque",
                )
            )
            pr._poller.ingest_raw_events = AsyncMock(return_value=["New PR opened"])
            pr._poller.update_cursor = AsyncMock()
            pr._invoker.call_agent = AsyncMock(return_value="extracted entities")
            pr._apply_perception_policy_from_planner = AsyncMock()
            pr._queue_perception_plan = AsyncMock(return_value=None)
            pr._events.publish_event = AsyncMock()
            pr._trace_manager = MagicMock()
            pr._trace_manager.start_trace.return_value = MagicMock(trace_id="trace_1")
            pr._trace_manager.finish_trace = AsyncMock()
            pr._budget = MagicMock()
            pr._budget.get_budget_status = AsyncMock(return_value=MagicMock())
            pr._budget.should_allow_perception.return_value = True

            result = await orch.run_perception_cycle(
                source="github",
                user_id=TEST_USER_ID,
                workspace_id=TEST_WORKSPACE_ID,
            )

            assert result["status"] == "completed"
            mock_assess.assert_called_once()

    @pytest.mark.asyncio
    async def test_perception_cycle_threads_engagement_penalty(self):
        """The deterministic dismissal penalty must reach assess_relevance."""
        from unittest.mock import AsyncMock, MagicMock, patch

        from tests.conftest import TEST_USER_ID, TEST_WORKSPACE_ID, make_mock_settings

        settings = make_mock_settings()

        with (
            patch("src.orchestrator.jarvis.get_anthropic_client") as mock_get_client,
            patch("src.services.relevance_assessor.assess_relevance") as mock_assess,
            patch(
                "src.services.engagement_service.EngagementService.is_suppressed",
                new=AsyncMock(return_value=False),
            ),
            patch(
                "src.services.engagement_service.EngagementService.get_relevance_penalty",
                new=AsyncMock(return_value=0.2),
            ),
        ):
            mock_get_client.return_value = AsyncMock()

            from src.services.relevance_assessor import RelevanceAssessment

            mock_assess.return_value = RelevanceAssessment(
                relevance_score=0.5, urgency="today", notification_tier="briefing"
            )

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

            orch = JarvisOrchestrator(
                settings=settings, db_factory=db_factory, services=ServiceContainer()
            )
            # Perception now lives on PerceptionRunner; connector I/O on its
            # ConnectorPoller — retarget mocks accordingly.
            pr = orch._perception
            pr._poller.poll = AsyncMock(
                return_value=([MagicMock(entity_id=None)], "c", None, "opaque")
            )
            pr._poller.ingest_raw_events = AsyncMock(return_value=["New PR opened"])
            pr._poller.update_cursor = AsyncMock()
            pr._invoker.call_agent = AsyncMock(return_value="extracted entities")
            pr._apply_perception_policy_from_planner = AsyncMock()
            pr._queue_perception_plan = AsyncMock(return_value=None)
            pr._events.publish_event = AsyncMock()
            pr._trace_manager = MagicMock()
            pr._trace_manager.start_trace.return_value = MagicMock(trace_id="t1")
            pr._trace_manager.finish_trace = AsyncMock()
            pr._budget = MagicMock()
            pr._budget.get_budget_status = AsyncMock(return_value=MagicMock())
            pr._budget.should_allow_perception.return_value = True

            await orch.run_perception_cycle(
                source="github",
                user_id=TEST_USER_ID,
                workspace_id=TEST_WORKSPACE_ID,
            )

            mock_assess.assert_called_once()
            assert mock_assess.call_args.kwargs.get("relevance_penalty") == 0.2


class TestNonActionableSynthesisSurfacing:
    """A perception/synthesis plan with no actionable steps must still surface
    its insight (as a briefing item) instead of being silently dropped."""

    @pytest.mark.asyncio
    async def test_non_actionable_plan_stored_as_briefing(self):
        from unittest.mock import AsyncMock, MagicMock, patch

        from src.contracts import PlanOutput, PlanStep
        from tests.conftest import TEST_USER_ID, TEST_WORKSPACE_ID, make_mock_settings

        settings = make_mock_settings()
        plan = PlanOutput(
            goal="Investor interest is rising across 3 sources",
            reasoning="Emails, calendar and Slack all point to fundraising momentum",
            steps=[PlanStep(description="note it", capability="respond", risk="none")],
        )

        with (
            patch("src.orchestrator.jarvis.get_anthropic_client"),
            patch("src.orchestrator.perception_runner.extract_plan", return_value=plan),
            patch("src.services.memory_service.MemoryService") as mock_mem,
        ):
            mem = AsyncMock()
            mock_mem.return_value = mem

            mock_db = AsyncMock()
            mock_db.commit = AsyncMock()
            db_ctx = AsyncMock()
            db_ctx.__aenter__ = AsyncMock(return_value=mock_db)
            db_ctx.__aexit__ = AsyncMock(return_value=False)
            db_factory = MagicMock(return_value=db_ctx)

            from src.orchestrator.jarvis import JarvisOrchestrator
            from src.orchestrator.services import ServiceContainer

            orch = JarvisOrchestrator(
                settings=settings, db_factory=db_factory, services=ServiceContainer()
            )

            await orch._queue_perception_plan(
                "ignored", "synthesis", TEST_USER_ID, TEST_WORKSPACE_ID, "trace_1"
            )

            mem.store_briefing_memory.assert_awaited_once()
            text = mem.store_briefing_memory.call_args.kwargs.get("text", "")
            assert "Investor interest" in text

    @pytest.mark.asyncio
    async def test_empty_goal_plan_not_stored(self):
        """A truly empty plan (no goal) should not create a briefing item."""
        from unittest.mock import AsyncMock, MagicMock, patch

        from src.contracts import PlanOutput
        from tests.conftest import TEST_USER_ID, TEST_WORKSPACE_ID, make_mock_settings

        settings = make_mock_settings()
        plan = PlanOutput(goal="", reasoning="", steps=[])

        with (
            patch("src.orchestrator.jarvis.get_anthropic_client"),
            patch("src.orchestrator.perception_runner.extract_plan", return_value=plan),
            patch("src.services.memory_service.MemoryService") as mock_mem,
        ):
            mem = AsyncMock()
            mock_mem.return_value = mem
            mock_db = AsyncMock()
            db_ctx = AsyncMock()
            db_ctx.__aenter__ = AsyncMock(return_value=mock_db)
            db_ctx.__aexit__ = AsyncMock(return_value=False)
            db_factory = MagicMock(return_value=db_ctx)

            from src.orchestrator.jarvis import JarvisOrchestrator
            from src.orchestrator.services import ServiceContainer

            orch = JarvisOrchestrator(
                settings=settings, db_factory=db_factory, services=ServiceContainer()
            )
            await orch._queue_perception_plan(
                "ignored", "synthesis", TEST_USER_ID, TEST_WORKSPACE_ID, "trace_1"
            )
            mem.store_briefing_memory.assert_not_called()
