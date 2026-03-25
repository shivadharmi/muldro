"""Tests for Phase 7 — Proactive Autonomy.

Tests trigger action execution, initiative-driven auto-planning,
proactive notifications, schedule seeding, and perception wiring.
"""

from datetime import datetime, timezone
from unittest.mock import AsyncMock, MagicMock, patch

import pytest

from src.services.event_processor import EventProcessor
from src.services.schedule_seeder import DEFAULT_SCHEDULES, seed_default_schedules
from tests.conftest import TEST_USER_ID

# ── Trigger Action Execution ──────────────────────────────────


def _make_processor(**kwargs):
    """Build an EventProcessor with mocked dependencies."""
    settings = MagicMock()
    settings.anthropic_model = "claude-sonnet-4-20250514"
    db = MagicMock()
    db.add = MagicMock()
    db.commit = AsyncMock()
    db.flush = AsyncMock()

    defaults = {
        "settings": settings,
        "db": db,
    }
    defaults.update(kwargs)
    with patch("src.services.event_processor.get_anthropic_client"):
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
    async def test_plan_action_creates_plan(self):
        """trigger action_type='plan' should call planner."""
        planner = MagicMock()
        planner.plan_for_command = AsyncMock()
        proc = _make_processor(planner=planner)

        trigger = _make_trigger(
            action_type="plan",
            action_config={"instructions": "Check for updates"},
        )
        event = _make_event_model()

        await proc._execute_trigger_action(trigger, event, "usr_1")

        planner.plan_for_command.assert_called_once()
        args = planner.plan_for_command.call_args[0]
        assert args[0] == "Check for updates"
        assert args[1] == "usr_1"

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
    async def test_high_score_triggers_auto_plan(self):
        """Events with high initiative score should auto-create plans."""
        planner = MagicMock()
        planner.plan_for_event = AsyncMock()

        proc = _make_processor(planner=planner)
        # Priority person + deadline boosts push score above threshold
        event = _make_event_model(
            importance_score=0.95,
            urgency_score=0.90,
            importance_signals={
                "from_priority_person": True,
                "contains_deadline": True,
            },
        )

        await proc._evaluate_initiative(event, "usr_1")

        planner.plan_for_event.assert_called_once_with("evt_test", "usr_1", workspace_id="")

    @pytest.mark.asyncio
    async def test_low_score_no_auto_plan(self):
        """Events with low initiative score should NOT auto-plan."""
        planner = MagicMock()
        planner.plan_for_event = AsyncMock()

        proc = _make_processor(planner=planner)
        event = _make_event_model(importance_score=0.2, urgency_score=0.1)

        await proc._evaluate_initiative(event, "usr_1")

        planner.plan_for_event.assert_not_called()

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

    def test_default_schedules_have_7_entries(self):
        assert len(DEFAULT_SCHEDULES) == 7

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
        """Should seed all 7 schedules when none exist."""
        db = MagicMock()
        result_mock = MagicMock()
        result_mock.scalars.return_value = result_mock
        result_mock.all.return_value = []
        db.execute = AsyncMock(return_value=result_mock)
        db.add = MagicMock()
        db.flush = AsyncMock()

        count = await seed_default_schedules(db, user_id=TEST_USER_ID)

        assert count == 7
        assert db.add.call_count == 7

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

        assert count == 5  # 7 - 2 existing

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
    async def test_all_seeded_schedules_disabled(self):
        """All seeded schedules should be disabled by default."""
        db = MagicMock()
        result_mock = MagicMock()
        result_mock.all.return_value = []
        db.execute = AsyncMock(return_value=result_mock)
        db.add = MagicMock()
        db.flush = AsyncMock()

        await seed_default_schedules(db, user_id=TEST_USER_ID)

        for call in db.add.call_args_list:
            schedule = call[0][0]
            assert schedule.enabled is False, f"{schedule.name} should be disabled"


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
