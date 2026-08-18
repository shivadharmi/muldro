"""Tests for Phase 7 — Proactive Autonomy.

Tests trigger action execution, initiative-driven auto-planning,
proactive notifications, schedule seeding, and perception wiring.
"""

from datetime import datetime, timezone
from unittest.mock import AsyncMock, MagicMock

import pytest

from src.services.event_processor import EventProcessor
from src.services.schedule_seeder import (
    DEFAULT_SCHEDULES,
    enable_schedules_for_connector,
    seed_default_schedules,
)
from tests.conftest import TEST_USER_ID, TEST_WORKSPACE_ID

# ── Trigger Action Execution ──────────────────────────────────


def _make_processor(**kwargs):
    """Build an EventProcessor with mocked dependencies."""
    settings = MagicMock()
    settings.anthropic_model = "claude-sonnet-4-20250514"
    settings.event_processor_concurrency = 5
    db = MagicMock()
    db.add = MagicMock()
    db.commit = AsyncMock()
    db.flush = AsyncMock()

    defaults = {
        "settings": settings,
        "db": db,
    }
    defaults.update(kwargs)
    return EventProcessor(**defaults)


def _make_event_model(
    event_id="evt_test",
    title="Test Event",
    summary="Test summary",
    source="gmail",
    event_type="email.received",
    importance_score=0.5,
    urgency_score=0.3,
    importance_signals=None,
    actor_entities=None,
):
    event = MagicMock()
    event.event_id = event_id
    event.title = title
    event.summary = summary
    event.source = source
    event.event_type = event_type
    event.importance_score = importance_score
    event.urgency_score = urgency_score
    event.importance_signals = importance_signals
    event.actor_entities = actor_entities
    return event


def _make_trigger(
    action_type="notify",
    action_config=None,
    name="test_trigger",
    trigger_id="trg_test",
):
    trigger = MagicMock()
    trigger.trigger_id = trigger_id
    trigger.name = name
    trigger.action_type = action_type
    trigger.action_config = action_config or {}
    return trigger


class TestTriggerActionExecution:
    @pytest.mark.asyncio
    async def test_notify_action_sends_notification(self):
        """trigger action_type='notify' should call notifier."""
        notifier = MagicMock()
        notifier.notify = AsyncMock(return_value={"status": "sent"})
        proc = _make_processor(notifier=notifier)

        trigger = _make_trigger(action_type="notify")
        event = _make_event_model()

        await proc._execute_trigger_action(trigger, event, "usr_1")

        notifier.notify.assert_called_once()
        call_kwargs = notifier.notify.call_args[1]
        assert call_kwargs["user_id"] == "usr_1"
        assert call_kwargs["notification_type"] == "info_update"

    @pytest.mark.asyncio
    async def test_plan_action_falls_through_to_debug_log(self):
        """trigger action_type='plan' should fall through (planner removed)."""
        proc = _make_processor()

        trigger = _make_trigger(
            action_type="plan",
            action_config={"instructions": "Check for updates"},
        )
        event = _make_event_model()

        # Should not raise — falls through to the else/debug branch
        await proc._execute_trigger_action(trigger, event, "usr_1")

    @pytest.mark.asyncio
    async def test_escalate_action_sends_critical_alert(self):
        """trigger action_type='escalate' should send critical_alert."""
        notifier = MagicMock()
        notifier.notify = AsyncMock(return_value={"status": "sent"})
        proc = _make_processor(notifier=notifier)

        trigger = _make_trigger(action_type="escalate")
        event = _make_event_model()

        await proc._execute_trigger_action(trigger, event, "usr_1")

        notifier.notify.assert_called_once()
        call_kwargs = notifier.notify.call_args[1]
        assert call_kwargs["notification_type"] == "critical_alert"
        assert call_kwargs["data"]["urgency"] == 1.0

    @pytest.mark.asyncio
    async def test_unknown_action_logs_debug(self):
        """Unknown action types should not raise, just log."""
        proc = _make_processor()
        trigger = _make_trigger(action_type="unknown_action")
        event = _make_event_model()

        # Should not raise
        await proc._execute_trigger_action(trigger, event, "usr_1")


# ── Initiative-Driven Auto-Planning ──────────────────────────


class TestInitiativeAutoPlanning:
    @pytest.mark.asyncio
    async def test_high_score_logs_high_priority(self):
        """High initiative score should log high-priority (planning handled by perception cycle)."""
        event_bus = MagicMock()
        event_bus.publish = AsyncMock()
        event_bus.event_stream = MagicMock(return_value="muldro:events:usr_1")

        proc = _make_processor(event_bus=event_bus)
        event = _make_event_model(
            importance_score=0.95,
            urgency_score=0.90,
            importance_signals={
                "from_priority_person": True,
                "contains_deadline": True,
            },
        )

        await proc._evaluate_initiative(event, "usr_1")

        # Should publish high_priority event (not auto_plan)
        event_bus.publish.assert_called_once()
        call_args = event_bus.publish.call_args[0]
        assert call_args[1] == "initiative.high_priority"

    @pytest.mark.asyncio
    async def test_low_score_no_event_published(self):
        """Events with low initiative score should NOT trigger any action."""
        event_bus = MagicMock()
        event_bus.publish = AsyncMock()

        proc = _make_processor(event_bus=event_bus)
        event = _make_event_model(importance_score=0.2, urgency_score=0.1)

        await proc._evaluate_initiative(event, "usr_1")

        event_bus.publish.assert_not_called()

    @pytest.mark.asyncio
    async def test_medium_score_proactive_notification(self):
        """Medium-score events should trigger proactive notification."""
        notifier = MagicMock()
        notifier.notify = AsyncMock(return_value={"status": "sent"})

        # Use a memory service that returns no matches → high novelty (0.9)
        memory_service = MagicMock()
        memory_service.retrieve = AsyncMock(return_value=[])

        # Memory service also returns goal memories for initiative scoring
        memory_service.retrieve = AsyncMock(
            return_value=[{"fact_text": "Goal: Test Event follow-up", "memory_type": "goal"}]
        )

        proc = _make_processor(
            notifier=notifier,
            memory_service=memory_service,
        )
        # Score: 0.30*0.8 + 0.25*0.6 + 0.20*goal + 0.15*0 + 0.10*0.9
        # = 0.24 + 0.15 + goal + 0 + 0.09 = ~0.55+ with goal relevance
        event = _make_event_model(importance_score=0.8, urgency_score=0.6)

        await proc._evaluate_initiative(event, "usr_1")

        notifier.notify.assert_called_once()

    @pytest.mark.asyncio
    async def test_initiative_failure_does_not_raise(self):
        """Initiative evaluation failures should be caught gracefully."""
        proc = _make_processor()
        event = _make_event_model()

        # Should not raise even without planner/notifier
        await proc._evaluate_initiative(event, "usr_1")


# ── Schedule Seeder ───────────────────────────────────────────


class TestScheduleSeeder:
    def test_default_schedules_cover_key_actions(self):
        """Default schedules should include briefing, perception, and maintenance."""
        action_types = {s["action_type"] for s in DEFAULT_SCHEDULES}
        assert "generate_briefing" in action_types
        assert "observe_source" in action_types
        assert "consolidate_memories" in action_types
        assert "check_slos" in action_types
        assert "heartbeat" in action_types

    def test_default_schedules_have_8_entries(self):
        assert len(DEFAULT_SCHEDULES) == 8

    def test_morning_briefing_at_7am(self):
        briefing = next(s for s in DEFAULT_SCHEDULES if s["name"] == "morning_briefing")
        assert briefing["cron_expr"] == "0 7 * * *"
        assert briefing["priority"] == "high"

    def test_observation_schedules_cover_4_sources(self):
        observe = [s for s in DEFAULT_SCHEDULES if s["action_type"] == "observe_source"]
        sources = {s["action_config"]["source"] for s in observe}
        assert sources == {"gmail", "calendar", "slack", "github"}

    @pytest.mark.asyncio
    async def test_seed_creates_all_when_empty(self):
        """Should seed all 8 schedules when none exist."""
        db = MagicMock()
        result_mock = MagicMock()
        result_mock.scalars.return_value = result_mock
        result_mock.all.return_value = []
        db.execute = AsyncMock(return_value=result_mock)
        db.add = MagicMock()
        db.flush = AsyncMock()

        count = await seed_default_schedules(db, user_id=TEST_USER_ID)

        assert count == 8
        assert db.add.call_count == 8

    @pytest.mark.asyncio
    async def test_seed_skips_existing(self):
        """Should skip schedules that already exist (with matching fields)."""
        from src.services.schedule_seeder import DEFAULT_SCHEDULES

        db = MagicMock()
        # Simulate 2 existing schedules with matching fields
        existing_scheds = []
        for sd in DEFAULT_SCHEDULES:
            if sd["name"] in ("morning_briefing", "observe_gmail"):
                s = MagicMock()
                s.name = sd["name"]
                s.cron_expr = sd.get("cron_expr")
                s.action_type = sd["action_type"]
                s.action_config = sd.get("action_config")
                s.priority = sd.get("priority", "medium")
                existing_scheds.append(s)

        result_mock = MagicMock()
        result_mock.scalars.return_value = result_mock
        result_mock.all.return_value = existing_scheds
        db.execute = AsyncMock(return_value=result_mock)
        db.add = MagicMock()
        db.flush = AsyncMock()

        count = await seed_default_schedules(db, user_id=TEST_USER_ID)

        assert count == 6  # 8 - 2 existing

    @pytest.mark.asyncio
    async def test_seeded_schedules_have_next_run(self):
        """Seeded schedules should compute next_run_at from cron."""
        db = MagicMock()
        result_mock = MagicMock()
        result_mock.scalars.return_value = result_mock
        result_mock.all.return_value = []
        db.execute = AsyncMock(return_value=result_mock)
        db.add = MagicMock()
        db.flush = AsyncMock()

        await seed_default_schedules(db, user_id=TEST_USER_ID)

        # Check the first added schedule has a next_run_at
        first_added = db.add.call_args_list[0][0][0]
        assert first_added.next_run_at is not None

    @pytest.mark.asyncio
    async def test_connector_independent_schedules_enabled_at_creation(self):
        """Briefing + housekeeping schedules are enabled at workspace creation so
        the proactive loop is reachable before any OAuth. Connector-dependent
        observe_* schedules stay disabled until their connector is authorized."""
        from src.services.schedule_seeder import WORKSPACE_CREATION_SCHEDULES

        db = MagicMock()
        result_mock = MagicMock()
        result_mock.scalars.return_value = result_mock
        result_mock.all.return_value = []
        db.execute = AsyncMock(return_value=result_mock)
        db.add = MagicMock()
        db.flush = AsyncMock()

        await seed_default_schedules(db, user_id=TEST_USER_ID)

        by_name = {call[0][0].name: call[0][0] for call in db.add.call_args_list}
        assert by_name["morning_briefing"].enabled is True
        for name in WORKSPACE_CREATION_SCHEDULES:
            assert by_name[name].enabled is True, f"{name} should be enabled at creation"
        # Connector-dependent observers must NOT poll an unconnected source.
        assert by_name["observe_gmail"].enabled is False
        assert by_name["observe_calendar"].enabled is False
        assert by_name["observe_slack"].enabled is False
        assert by_name["observe_github"].enabled is False

    @pytest.mark.asyncio
    async def test_seed_scopes_to_workspace(self):
        """Schedules from workspace A should not prevent seeding in workspace B."""
        db = MagicMock()
        result_empty = MagicMock()
        result_empty.scalars.return_value = result_empty
        result_empty.all.return_value = []
        db.execute = AsyncMock(return_value=result_empty)
        db.add = MagicMock()
        db.flush = AsyncMock()

        # Seed workspace A
        count_a = await seed_default_schedules(
            db,
            user_id=TEST_USER_ID,
            workspace_id="ws_a",
        )
        assert count_a == 8

        # Reset mocks for workspace B — DB still returns empty for ws_b query
        db.add.reset_mock()
        db.flush.reset_mock()
        db.execute = AsyncMock(return_value=result_empty)

        count_b = await seed_default_schedules(
            db,
            user_id="usr_other",
            workspace_id="ws_b",
        )
        assert count_b == 8  # Should seed all 8, not skip

    @pytest.mark.asyncio
    async def test_enable_connector_scopes_to_workspace(self):
        """Enabling schedules for a connector should scope to the target workspace."""
        db = MagicMock()

        # Query 1: no existing observe_* enabled in this workspace
        result_no_existing = MagicMock()
        result_no_existing.first.return_value = None

        # Query 2: schedules to enable
        observe_sched = MagicMock()
        observe_sched.name = "observe_gmail"
        observe_sched.enabled = False
        observe_sched.next_run_at = None
        observe_sched.cron_expr = "*/5 * * * *"
        observe_sched.workspace_id = TEST_WORKSPACE_ID
        observe_sched.user_id = TEST_USER_ID

        briefing_sched = MagicMock()
        briefing_sched.name = "morning_briefing"
        briefing_sched.enabled = False
        briefing_sched.next_run_at = None
        briefing_sched.cron_expr = "0 7 * * *"
        briefing_sched.workspace_id = TEST_WORKSPACE_ID
        briefing_sched.user_id = TEST_USER_ID

        result_schedules = MagicMock()
        result_schedules.scalars.return_value = result_schedules
        result_schedules.all.return_value = [observe_sched, briefing_sched]

        # Query 3: PerceptionState schedule lookup
        result_sched_obj = MagicMock()
        result_sched_obj.scalar_one_or_none.return_value = observe_sched

        db.execute = AsyncMock(
            side_effect=[result_no_existing, result_schedules, result_sched_obj],
        )
        db.flush = AsyncMock()

        enabled = await enable_schedules_for_connector(
            db,
            "gmail",
            workspace_id=TEST_WORKSPACE_ID,
        )
        assert "observe_gmail" in enabled
        assert "morning_briefing" in enabled
        # Verify schedules were actually enabled
        assert observe_sched.enabled is True
        assert briefing_sched.enabled is True


# ── Perception Policy Service ─────────────────────────────────


class TestPerceptionPolicyWiring:
    @pytest.mark.asyncio
    async def test_scheduler_no_orchestrator_no_perception(self):
        """Without orchestrator, _tick_perception should be a no-op."""
        from src.services.scheduler import SchedulerLoop

        scheduler = SchedulerLoop(MagicMock(), orchestrator=None)
        # Should not raise or do anything
        await scheduler._tick_perception(MagicMock())

    def test_policy_service_computes_effective_interval(self):
        """Policy service should compute effective interval from base + backoff."""
        from src.models.perception_state import PerceptionState
        from src.services.perception_policy import PerceptionPolicyService

        state = PerceptionState(
            state_id="pst_test",
            workspace_id="ws_test",
            user_id=TEST_USER_ID,
            source="gmail",
            mode="poll",
            base_interval_s=300,
            effective_interval_s=300,
            consecutive_failures=2,
        )
        svc = PerceptionPolicyService(AsyncMock())
        # 300 * 2^2 = 1200
        assert svc._compute_effective_interval(state) == 1200

    def test_budget_multiplier_stretches_interval(self):
        """Budget multiplier should stretch the next_run_at interval."""
        from src.models.perception_state import PerceptionState
        from src.services.perception_policy import PerceptionPolicyService

        state = PerceptionState(
            state_id="pst_test",
            workspace_id="ws_test",
            user_id=TEST_USER_ID,
            source="gmail",
            mode="poll",
            base_interval_s=300,
            effective_interval_s=300,
            last_run_at=datetime.now(timezone.utc),
        )
        svc = PerceptionPolicyService(AsyncMock())
        next_run = svc._compute_next_run(state, budget_multiplier=3)
        delta = (next_run - datetime.now(timezone.utc)).total_seconds()
        # 300 * 3 = 900s
        assert 899 <= delta <= 901
