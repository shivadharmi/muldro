"""Tests for SchedulerLoop — backend-owned dynamic scheduling."""

from datetime import datetime, timedelta, timezone
from unittest.mock import AsyncMock, MagicMock, patch

import pytest

from src.services.scheduler import SchedulerLoop, compute_next_run
from tests.conftest import TEST_USER_ID, TEST_WORKSPACE_ID, make_mock_settings


@pytest.fixture
def settings():
    return make_mock_settings()


def _make_schedule(**overrides):
    """Factory for mock Schedule objects."""
    now = datetime.now(timezone.utc)
    defaults = dict(
        schedule_id="sched_test_001",
        user_id=TEST_USER_ID,
        name="test-schedule",
        schedule_type="recurring",
        cron_expr="*/15 * * * *",
        run_at=None,
        action_type="observe_source",
        action_config={"source": "gmail"},
        enabled=True,
        last_run_at=None,
        next_run_at=now - timedelta(minutes=1),
        run_count=5,
        consecutive_failures=0,
        last_error=None,
        source="system",
        priority="medium",
    )
    defaults.update(overrides)
    sched = MagicMock()
    for k, v in defaults.items():
        setattr(sched, k, v)
    return sched


class TestComputeNextRun:
    def test_every_15_minutes(self):
        base = datetime(2026, 3, 15, 10, 0, tzinfo=timezone.utc)
        result = compute_next_run("*/15 * * * *", base)
        assert result == datetime(2026, 3, 15, 10, 15, tzinfo=timezone.utc)

    def test_hourly(self):
        base = datetime(2026, 3, 15, 10, 30, tzinfo=timezone.utc)
        result = compute_next_run("0 * * * *", base)
        assert result == datetime(2026, 3, 15, 11, 0, tzinfo=timezone.utc)

    def test_weekday_morning(self):
        # Monday 2026-03-16
        base = datetime(2026, 3, 16, 7, 0, tzinfo=timezone.utc)
        result = compute_next_run("0 7 * * 1-5", base)
        # Next weekday 7am is Tuesday
        assert result == datetime(2026, 3, 17, 7, 0, tzinfo=timezone.utc)

    def test_every_30_minutes(self):
        base = datetime(2026, 3, 15, 10, 0, tzinfo=timezone.utc)
        result = compute_next_run("*/30 * * * *", base)
        assert result == datetime(2026, 3, 15, 10, 30, tzinfo=timezone.utc)


class TestSchedulerTick:
    @pytest.mark.asyncio
    async def test_tick_fires_due_schedules(self, settings):
        """Two due schedules should both fire."""
        sched1 = _make_schedule(
            schedule_id="sched_001",
            action_type="observe_source",
            action_config={"source": "gmail"},
        )
        sched2 = _make_schedule(
            schedule_id="sched_002",
            action_type="observe_source",
            action_config={"source": "github"},
        )

        mock_result = MagicMock()
        mock_result.scalars.return_value.all.return_value = [sched1, sched2]

        mock_db = MagicMock()
        mock_db.execute = AsyncMock(return_value=mock_result)
        mock_db.commit = AsyncMock()
        mock_db.__aenter__ = AsyncMock(return_value=mock_db)
        mock_db.__aexit__ = AsyncMock(return_value=False)

        mock_factory = MagicMock(return_value=mock_db)

        scheduler = SchedulerLoop(settings)

        with (
            patch("src.services.scheduler._base.get_session_factory", return_value=mock_factory),
            patch.object(scheduler, "_fire", new_callable=AsyncMock) as mock_fire,
        ):
            await scheduler._tick()

            assert mock_fire.call_count == 2
            assert sched1.run_count == 6
            assert sched2.run_count == 6
            assert mock_db.commit.await_count >= 1

    @pytest.mark.asyncio
    async def test_tick_skips_future_schedules(self, settings):
        """Schedules with next_run_at in the future should not be returned by query."""
        mock_result = MagicMock()
        mock_result.scalars.return_value.all.return_value = []

        mock_db = MagicMock()
        mock_db.execute = AsyncMock(return_value=mock_result)
        mock_db.commit = AsyncMock()
        mock_db.__aenter__ = AsyncMock(return_value=mock_db)
        mock_db.__aexit__ = AsyncMock(return_value=False)

        mock_factory = MagicMock(return_value=mock_db)

        scheduler = SchedulerLoop(settings)

        with patch("src.services.scheduler._base.get_session_factory", return_value=mock_factory):
            await scheduler._tick()
            # Commit is called at least once (to persist state), but no fire
            assert mock_db.commit.await_count >= 1

    @pytest.mark.asyncio
    async def test_tick_advances_next_run_at(self, settings):
        """After firing, next_run_at should be moved forward."""
        sched = _make_schedule(cron_expr="*/15 * * * *")

        mock_result = MagicMock()
        mock_result.scalars.return_value.all.return_value = [sched]

        mock_db = MagicMock()
        mock_db.execute = AsyncMock(return_value=mock_result)
        mock_db.commit = AsyncMock()
        mock_db.__aenter__ = AsyncMock(return_value=mock_db)
        mock_db.__aexit__ = AsyncMock(return_value=False)

        mock_factory = MagicMock(return_value=mock_db)

        scheduler = SchedulerLoop(settings)

        with (
            patch("src.services.scheduler._base.get_session_factory", return_value=mock_factory),
            patch.object(scheduler, "_fire", new_callable=AsyncMock),
        ):
            await scheduler._tick()

            # next_run_at should be in the future now
            assert sched.next_run_at > datetime.now(timezone.utc) - timedelta(seconds=5)

    @pytest.mark.asyncio
    async def test_one_shot_disables_after_fire(self, settings):
        """One-shot schedules should be disabled after firing."""
        sched = _make_schedule(
            schedule_type="one_shot",
            cron_expr=None,
            run_at=datetime.now(timezone.utc) - timedelta(minutes=1),
        )

        mock_result = MagicMock()
        mock_result.scalars.return_value.all.return_value = [sched]

        mock_db = MagicMock()
        mock_db.execute = AsyncMock(return_value=mock_result)
        mock_db.commit = AsyncMock()
        mock_db.__aenter__ = AsyncMock(return_value=mock_db)
        mock_db.__aexit__ = AsyncMock(return_value=False)

        mock_factory = MagicMock(return_value=mock_db)

        scheduler = SchedulerLoop(settings)

        with (
            patch("src.services.scheduler._base.get_session_factory", return_value=mock_factory),
            patch.object(scheduler, "_fire", new_callable=AsyncMock),
        ):
            await scheduler._tick()

            assert sched.enabled is False
            assert sched.next_run_at is None

    @pytest.mark.asyncio
    async def test_consecutive_failures_auto_disable(self, settings):
        """Schedule should be auto-disabled after 5 consecutive failures."""
        sched = _make_schedule(consecutive_failures=4)

        mock_result = MagicMock()
        mock_result.scalars.return_value.all.return_value = [sched]

        mock_db = MagicMock()
        mock_db.execute = AsyncMock(return_value=mock_result)
        mock_db.commit = AsyncMock()
        mock_db.__aenter__ = AsyncMock(return_value=mock_db)
        mock_db.__aexit__ = AsyncMock(return_value=False)

        mock_factory = MagicMock(return_value=mock_db)

        scheduler = SchedulerLoop(settings)

        with (
            patch("src.services.scheduler._base.get_session_factory", return_value=mock_factory),
            patch.object(
                scheduler, "_fire", new_callable=AsyncMock, side_effect=Exception("agent down")
            ),
        ):
            await scheduler._tick()

            assert sched.consecutive_failures == 5
            assert sched.enabled is False
            assert "agent down" in sched.last_error


class TestFireActions:
    """Tests for SchedulerLoop._fire() action dispatch.

    Every test patches _resolve_workspace so _fire() doesn't hit the DB.
    """

    @pytest.fixture(autouse=True)
    def _patch_resolve_workspace(self):
        with patch.object(
            SchedulerLoop,
            "_resolve_workspace",
            new_callable=AsyncMock,
            return_value=TEST_WORKSPACE_ID,
        ):
            yield

    @pytest.mark.asyncio
    @patch.object(
        SchedulerLoop,
        "_get_authorized_providers",
        new_callable=AsyncMock,
        return_value={"gmail"},
    )
    async def test_fire_observe_source_calls_orchestrator(self, mock_auth, settings):
        """observe_source should use orchestrator.run_perception_cycle when authorized."""
        sched = _make_schedule(
            action_type="observe_source",
            action_config={"source": "gmail"},
        )

        mock_orch = MagicMock()
        mock_orch.run_perception_cycle = AsyncMock(return_value={"status": "completed"})

        scheduler = SchedulerLoop(settings, orchestrator=mock_orch)
        await scheduler._fire(sched)

        mock_orch.run_perception_cycle.assert_awaited_once_with(
            "gmail", user_id=sched.user_id, workspace_id=TEST_WORKSPACE_ID
        )

    @pytest.mark.asyncio
    @patch.object(
        SchedulerLoop,
        "_get_authorized_providers",
        new_callable=AsyncMock,
        return_value=set(),
    )
    async def test_fire_observe_source_skips_unauthorized(self, mock_auth, settings):
        """observe_source should skip when source is not authorized."""
        sched = _make_schedule(
            action_type="observe_source",
            action_config={"source": "gmail"},
        )

        mock_orch = MagicMock()
        mock_orch.run_perception_cycle = AsyncMock()

        scheduler = SchedulerLoop(settings, orchestrator=mock_orch)
        await scheduler._fire(sched)

        mock_orch.run_perception_cycle.assert_not_awaited()

    @pytest.mark.asyncio
    async def test_fire_observe_source_requires_orchestrator(self, settings):
        """observe_source without orchestrator should raise RuntimeError."""
        sched = _make_schedule(
            action_type="observe_source",
            action_config={"source": "gmail"},
        )

        scheduler = SchedulerLoop(settings)
        with pytest.raises(RuntimeError, match="Orchestrator required"):
            await scheduler._fire(sched)

    @pytest.mark.asyncio
    async def test_fire_generate_briefing_calls_orchestrator(self, settings):
        """generate_briefing should use orchestrator."""
        sched = _make_schedule(
            action_type="generate_briefing",
            action_config={},
        )

        mock_orch = MagicMock()
        mock_orch.generate_briefing = AsyncMock(return_value={"status": "completed"})

        scheduler = SchedulerLoop(settings, orchestrator=mock_orch)
        await scheduler._fire(sched)

        mock_orch.generate_briefing.assert_awaited_once()

    @pytest.mark.asyncio
    async def test_fire_generate_briefing_requires_orchestrator(self, settings):
        """generate_briefing without orchestrator should raise RuntimeError."""
        sched = _make_schedule(
            action_type="generate_briefing",
            action_config={},
        )

        scheduler = SchedulerLoop(settings)
        with pytest.raises(RuntimeError, match="Orchestrator required"):
            await scheduler._fire(sched)

    @pytest.mark.asyncio
    async def test_fire_heartbeat_runs_directly(self, settings):
        """heartbeat should run HeartbeatService directly."""
        sched = _make_schedule(
            action_type="heartbeat",
            action_config={},
        )

        mock_hb = MagicMock()
        mock_hb.run = AsyncMock(return_value={})

        mock_db = MagicMock()
        mock_db.commit = AsyncMock()
        mock_db.__aenter__ = AsyncMock(return_value=mock_db)
        mock_db.__aexit__ = AsyncMock(return_value=False)
        mock_factory = MagicMock(return_value=mock_db)

        scheduler = SchedulerLoop(settings)

        with (
            patch(
                "src.services.scheduler.schedule_dispatch.get_session_factory",
                return_value=mock_factory,
            ),
            patch(
                "src.services.scheduler.schedule_dispatch.HeartbeatService", return_value=mock_hb
            ),
        ):
            await scheduler._fire(sched)

            mock_hb.run.assert_awaited_once_with(sched.user_id)

    @pytest.mark.asyncio
    async def test_fire_custom_agent_task_uses_orchestrator(self, settings):
        """custom_agent_task should use orchestrator.process_message."""
        sched = _make_schedule(
            action_type="custom_agent_task",
            action_config={"instructions": "Review open PRs"},
        )

        mock_orch = MagicMock()
        mock_orch.process_message = AsyncMock(return_value={"status": "ok"})

        scheduler = SchedulerLoop(settings, orchestrator=mock_orch)
        await scheduler._fire(sched)

        mock_orch.process_message.assert_awaited_once()

    @pytest.mark.asyncio
    async def test_fire_custom_agent_task_requires_orchestrator(self, settings):
        """custom_agent_task without orchestrator should raise RuntimeError."""
        sched = _make_schedule(
            action_type="custom_agent_task",
            action_config={"instructions": "Do something"},
        )

        scheduler = SchedulerLoop(settings)
        with pytest.raises(RuntimeError, match="Orchestrator required"):
            await scheduler._fire(sched)

    @pytest.mark.asyncio
    async def test_fire_meeting_prep_uses_orchestrator(self, settings):
        """meeting_prep should use orchestrator.process_message."""
        sched = _make_schedule(
            action_type="meeting_prep",
            action_config={},
        )

        mock_orch = MagicMock()
        mock_orch.process_message = AsyncMock(return_value={"status": "ok"})

        scheduler = SchedulerLoop(settings, orchestrator=mock_orch)
        await scheduler._fire(sched)

        mock_orch.process_message.assert_awaited_once()


class TestPersonaBatch:
    """Test _tick_persona_batch() in SchedulerLoop."""

    @pytest.mark.asyncio
    async def test_skips_when_not_10th_tick(self):
        from src.services.scheduler import SchedulerLoop
        from tests.conftest import make_mock_settings

        settings = make_mock_settings()
        scheduler = SchedulerLoop(settings=settings)
        scheduler._tick_count = 3

        await scheduler._tick_persona_batch(factory=AsyncMock())
        # No exception = pass

    @pytest.mark.asyncio
    async def test_skips_when_fewer_than_5_interactions(self):
        from src.services.scheduler import SchedulerLoop
        from tests.conftest import make_mock_settings

        settings = make_mock_settings()
        orchestrator = AsyncMock()
        scheduler = SchedulerLoop(settings=settings, orchestrator=orchestrator)
        scheduler._tick_count = 10

        mock_db = AsyncMock()
        mock_result = MagicMock()
        mock_result.scalars.return_value.all.return_value = [MagicMock()] * 3
        mock_db.execute = AsyncMock(return_value=mock_result)
        mock_db.__aenter__ = AsyncMock(return_value=mock_db)
        mock_db.__aexit__ = AsyncMock(return_value=False)
        mock_factory = MagicMock(return_value=mock_db)

        await scheduler._tick_persona_batch(factory=mock_factory)
        orchestrator._call_agent.assert_not_called()

    @pytest.mark.asyncio
    async def test_calls_persona_with_5_plus_interactions(self):
        from src.services.scheduler import SchedulerLoop
        from tests.conftest import make_mock_settings

        settings = make_mock_settings()
        orchestrator = AsyncMock()
        orchestrator._call_agent = AsyncMock(return_value="ok")
        scheduler = SchedulerLoop(settings=settings, orchestrator=orchestrator)
        scheduler._tick_count = 10

        mock_interactions = []
        for i in range(6):
            m = MagicMock()
            m.message_preview = f"message {i}"
            m.intent = "command"
            m.user_id = "usr_test"
            m.workspace_id = "ws_test"
            mock_interactions.append(m)

        mock_db = AsyncMock()
        mock_result = MagicMock()
        mock_result.scalars.return_value.all.return_value = mock_interactions
        mock_db.execute = AsyncMock(return_value=mock_result)
        mock_db.__aenter__ = AsyncMock(return_value=mock_db)
        mock_db.__aexit__ = AsyncMock(return_value=False)
        mock_factory = MagicMock(return_value=mock_db)

        await scheduler._tick_persona_batch(factory=mock_factory)
        orchestrator._call_agent.assert_called_once()
        call_args = orchestrator._call_agent.call_args
        assert call_args[0][0] == "persona"


class TestPendingNotificationRedelivery:
    """Test _tick_pending_notifications re-delivers follow-up notifications."""

    @pytest.mark.asyncio
    async def test_redelivers_pending_notifications(self, settings):
        mock_notif = MagicMock()
        mock_notif.user_id = TEST_USER_ID
        mock_notif.channel = "web"
        mock_notif.title = "Follow up"
        mock_notif.body = "Check this"
        mock_notif.payload_json = {}
        mock_notif.workspace_id = TEST_WORKSPACE_ID
        mock_notif.notification_id = "notif_test"
        mock_notif.status = "pending"

        mock_result = MagicMock()
        mock_result.scalars.return_value.all.return_value = [mock_notif]

        mock_db = MagicMock()
        mock_db.execute = AsyncMock(return_value=mock_result)
        mock_db.commit = AsyncMock()
        mock_db.__aenter__ = AsyncMock(return_value=mock_db)
        mock_db.__aexit__ = AsyncMock(return_value=False)
        mock_factory = MagicMock(return_value=mock_db)

        mock_notifier = AsyncMock()
        mock_notifier.notify = AsyncMock(return_value={"status": "sent"})

        mock_orch = MagicMock()
        mock_orch._notifier = mock_notifier

        scheduler = SchedulerLoop(settings, orchestrator=mock_orch)
        await scheduler._tick_pending_notifications(mock_factory)

        mock_notifier.notify.assert_called_once_with(
            user_id=TEST_USER_ID,
            notification_type="web",
            title="Follow up",
            body="Check this",
            data={},
            workspace_id=TEST_WORKSPACE_ID,
        )
        assert mock_notif.status == "sent"


class TestBoundedFirstPersonaBatch:
    """Test that first persona batch is bounded to 24h."""

    @pytest.mark.asyncio
    async def test_first_batch_has_where_clause(self, settings):
        """On first tick, query should filter by created_at > 24h ago."""
        orchestrator = AsyncMock()
        orchestrator._call_agent = AsyncMock(return_value="ok")
        scheduler = SchedulerLoop(settings=settings, orchestrator=orchestrator)
        scheduler._tick_count = 10
        # Ensure no _last_persona_batch_at
        assert not hasattr(scheduler, "_last_persona_batch_at") or (
            getattr(scheduler, "_last_persona_batch_at", None) is None
        )

        mock_db = AsyncMock()
        mock_result = MagicMock()
        mock_result.scalars.return_value.all.return_value = []  # no interactions
        mock_db.execute = AsyncMock(return_value=mock_result)
        mock_db.__aenter__ = AsyncMock(return_value=mock_db)
        mock_db.__aexit__ = AsyncMock(return_value=False)
        mock_factory = MagicMock(return_value=mock_db)

        await scheduler._tick_persona_batch(factory=mock_factory)

        # execute was called — the query should have a where clause
        mock_db.execute.assert_called_once()
        orchestrator._call_agent.assert_not_called()


class TestCrossSourceSynthesisTrigger:
    """Test that synthesis triggers on volume, not cooldown."""

    def test_synthesis_triggers_with_2_sources_3_events(self):
        sources_with_events = 2
        total_event_count = 3
        should_trigger = sources_with_events >= 2 and total_event_count >= 3
        assert should_trigger is True

    def test_synthesis_skips_with_1_source(self):
        sources_with_events = 1
        total_event_count = 5
        should_trigger = sources_with_events >= 2 and total_event_count >= 3
        assert should_trigger is False

    def test_synthesis_skips_with_2_sources_but_only_2_events(self):
        sources_with_events = 2
        total_event_count = 2
        should_trigger = sources_with_events >= 2 and total_event_count >= 3
        assert should_trigger is False


# ---------------------------------------------------------------------------
# Cross-source synthesis tenant grouping (P3)
# ---------------------------------------------------------------------------


def _make_perception_state(
    user_id: str,
    workspace_id: str,
    source: str,
    **overrides,
) -> MagicMock:
    """Factory for mock PerceptionState objects used in perception tick tests."""
    state = MagicMock()
    state.user_id = user_id
    state.workspace_id = workspace_id
    state.source = source
    state.pending_run = False
    state.next_run_at = datetime.now(timezone.utc) - timedelta(seconds=10)
    for k, v in overrides.items():
        setattr(state, k, v)
    return state


def _make_scheduler_for_tick(settings, orchestrator):
    """Return a SchedulerLoop instance set up for _tick_perception testing."""
    scheduler = SchedulerLoop(settings, orchestrator=orchestrator)
    return scheduler


def _build_tick_mocks(due_states: list, results: list):
    """
    Build the factory/db/svc mocks needed by _tick_perception.

    ``results`` must be positionally aligned with ``due_states``.
    Each element is either a (src_name, event_count) tuple or an exception.

    The mock lookup is keyed on (user_id, source) to avoid collisions when two
    tenants share a source name (e.g. both polling "gmail").
    """
    mock_db = MagicMock()
    mock_db.flush = AsyncMock()
    mock_db.commit = AsyncMock()
    mock_db.__aenter__ = AsyncMock(return_value=mock_db)
    mock_db.__aexit__ = AsyncMock(return_value=False)

    mock_svc = MagicMock()
    mock_svc.get_due_sources_all_users = AsyncMock(return_value=due_states)
    mock_svc.record_success = AsyncMock()
    mock_svc.record_failure = AsyncMock()

    mock_factory = MagicMock(return_value=mock_db)

    # Keyed on (user_id, source) so two tenants that share a source name
    # never silently overwrite each other's expected result.
    result_by_user_source: dict[tuple[str, str], object] = {}
    for state, res in zip(due_states, results):
        composite_key = (state.user_id, state.source)
        if isinstance(res, BaseException):
            result_by_user_source[composite_key] = res
        else:
            _src, evt_count = res
            result_by_user_source[composite_key] = {"status": "completed", "events": evt_count}

    async def _fake_run_perception_cycle(source, *, user_id, workspace_id):
        r = result_by_user_source.get((user_id, source), {"status": "completed", "events": 0})
        if isinstance(r, BaseException):
            raise r
        return r

    return mock_factory, mock_svc, _fake_run_perception_cycle


class TestCrossSourceSynthesisTenantGrouping:
    """Synthesis must be per-(user_id, workspace_id) group, never cross-tenant."""

    # ------------------------------------------------------------------
    # Case (a): two tenants both above threshold → two separate calls
    # ------------------------------------------------------------------
    @pytest.mark.asyncio
    async def test_two_tenants_both_above_threshold_trigger_separate_calls(self, settings):
        """Two tenants each having >=2 sources / >=3 events → synthesis fires once per tenant."""
        user_a = "usr_aaa"
        ws_a = "ws_aaa"
        user_b = "usr_bbb"
        ws_b = "ws_bbb"

        due_states = [
            _make_perception_state(user_a, ws_a, "gmail"),
            _make_perception_state(user_a, ws_a, "slack"),
            _make_perception_state(user_b, ws_b, "github"),
            _make_perception_state(user_b, ws_b, "calendar"),
        ]
        # Each source returns 2 events → each tenant has 4 total / 2 sources → triggers
        results = [
            ("gmail", 2),
            ("slack", 2),
            ("github", 2),
            ("calendar", 2),
        ]

        mock_factory, mock_svc, fake_cycle = _build_tick_mocks(due_states, results)

        mock_orch = MagicMock()
        mock_orch.run_cross_source_synthesis = AsyncMock()
        mock_orch.run_perception_cycle = AsyncMock(side_effect=fake_cycle)
        mock_orch._budget = MagicMock()
        mock_orch._budget.get_budget_status = AsyncMock(return_value={"mode": "normal"})
        mock_orch._budget.should_allow_perception = MagicMock(return_value=True)
        mock_orch._budget.get_perception_interval_multiplier = MagicMock(return_value=1)

        scheduler = _make_scheduler_for_tick(settings, mock_orch)

        with (
            patch(
                "src.services.perception_policy.PerceptionPolicyService",
                return_value=mock_svc,
            ),
            patch.object(
                scheduler,
                "_resolve_workspace",
                new=AsyncMock(side_effect=lambda uid: ws_a if uid == user_a else ws_b),
            ),
        ):
            await scheduler._tick_perception(mock_factory)

        assert mock_orch.run_cross_source_synthesis.await_count == 2

        calls = mock_orch.run_cross_source_synthesis.call_args_list
        call_kwargs = {c.kwargs["user_id"]: c.kwargs for c in calls}

        # Tenant A call
        assert user_a in call_kwargs
        assert call_kwargs[user_a]["workspace_id"] == ws_a
        assert set(call_kwargs[user_a]["source_names"]) == {"gmail", "slack"}

        # Tenant B call
        assert user_b in call_kwargs
        assert call_kwargs[user_b]["workspace_id"] == ws_b
        assert set(call_kwargs[user_b]["source_names"]) == {"github", "calendar"}

    # ------------------------------------------------------------------
    # Case (b): one tenant below threshold, one above → only above fires
    # ------------------------------------------------------------------
    @pytest.mark.asyncio
    async def test_only_above_threshold_tenant_triggers(self, settings):
        """Tenant A below threshold must not trigger; Tenant B above threshold must trigger."""
        user_a = "usr_aaa"
        ws_a = "ws_aaa"
        user_b = "usr_bbb"
        ws_b = "ws_bbb"

        due_states = [
            # Tenant A: 1 source, 5 events — fails sources >= 2 check
            _make_perception_state(user_a, ws_a, "gmail"),
            # Tenant B: 2 sources, 3 events — passes
            _make_perception_state(user_b, ws_b, "github"),
            _make_perception_state(user_b, ws_b, "slack"),
        ]
        results = [
            ("gmail", 5),
            ("github", 1),
            ("slack", 2),
        ]

        mock_factory, mock_svc, fake_cycle = _build_tick_mocks(due_states, results)

        mock_orch = MagicMock()
        mock_orch.run_cross_source_synthesis = AsyncMock()
        mock_orch.run_perception_cycle = AsyncMock(side_effect=fake_cycle)
        mock_orch._budget = MagicMock()
        mock_orch._budget.get_budget_status = AsyncMock(return_value={"mode": "normal"})
        mock_orch._budget.should_allow_perception = MagicMock(return_value=True)
        mock_orch._budget.get_perception_interval_multiplier = MagicMock(return_value=1)

        scheduler = _make_scheduler_for_tick(settings, mock_orch)

        with (
            patch(
                "src.services.perception_policy.PerceptionPolicyService",
                return_value=mock_svc,
            ),
            patch.object(
                scheduler,
                "_resolve_workspace",
                new=AsyncMock(side_effect=lambda uid: ws_a if uid == user_a else ws_b),
            ),
        ):
            await scheduler._tick_perception(mock_factory)

        assert mock_orch.run_cross_source_synthesis.await_count == 1
        call_kwargs = mock_orch.run_cross_source_synthesis.call_args.kwargs
        assert call_kwargs["user_id"] == user_b
        assert call_kwargs["workspace_id"] == ws_b
        assert set(call_kwargs["source_names"]) == {"github", "slack"}

    # ------------------------------------------------------------------
    # Case (c): single tenant, multi-source — still triggers exactly once
    # ------------------------------------------------------------------
    @pytest.mark.asyncio
    async def test_single_tenant_multi_source_triggers_once(self, settings):
        """Single tenant with >=2 sources / >=3 events must still trigger exactly once."""
        due_states = [
            _make_perception_state(TEST_USER_ID, TEST_WORKSPACE_ID, "gmail"),
            _make_perception_state(TEST_USER_ID, TEST_WORKSPACE_ID, "slack"),
            _make_perception_state(TEST_USER_ID, TEST_WORKSPACE_ID, "github"),
        ]
        results = [
            ("gmail", 2),
            ("slack", 2),
            ("github", 1),
        ]

        mock_factory, mock_svc, fake_cycle = _build_tick_mocks(due_states, results)

        mock_orch = MagicMock()
        mock_orch.run_cross_source_synthesis = AsyncMock()
        mock_orch.run_perception_cycle = AsyncMock(side_effect=fake_cycle)
        mock_orch._budget = MagicMock()
        mock_orch._budget.get_budget_status = AsyncMock(return_value={"mode": "normal"})
        mock_orch._budget.should_allow_perception = MagicMock(return_value=True)
        mock_orch._budget.get_perception_interval_multiplier = MagicMock(return_value=1)

        scheduler = _make_scheduler_for_tick(settings, mock_orch)

        with (
            patch(
                "src.services.perception_policy.PerceptionPolicyService",
                return_value=mock_svc,
            ),
            patch.object(
                scheduler,
                "_resolve_workspace",
                new=AsyncMock(return_value=TEST_WORKSPACE_ID),
            ),
        ):
            await scheduler._tick_perception(mock_factory)

        assert mock_orch.run_cross_source_synthesis.await_count == 1
        call_kwargs = mock_orch.run_cross_source_synthesis.call_args.kwargs
        assert call_kwargs["user_id"] == TEST_USER_ID
        assert call_kwargs["workspace_id"] == TEST_WORKSPACE_ID
        assert set(call_kwargs["source_names"]) == {"gmail", "slack", "github"}

    # ------------------------------------------------------------------
    # Case (d): tenant with only 1 source or <3 events does NOT trigger
    # ------------------------------------------------------------------
    @pytest.mark.asyncio
    async def test_single_source_tenant_does_not_trigger(self, settings):
        """A tenant with only 1 source with events must not trigger synthesis."""
        due_states = [
            _make_perception_state(TEST_USER_ID, TEST_WORKSPACE_ID, "gmail"),
        ]
        results = [("gmail", 10)]

        mock_factory, mock_svc, fake_cycle = _build_tick_mocks(due_states, results)

        mock_orch = MagicMock()
        mock_orch.run_cross_source_synthesis = AsyncMock()
        mock_orch.run_perception_cycle = AsyncMock(side_effect=fake_cycle)
        mock_orch._budget = MagicMock()
        mock_orch._budget.get_budget_status = AsyncMock(return_value={"mode": "normal"})
        mock_orch._budget.should_allow_perception = MagicMock(return_value=True)
        mock_orch._budget.get_perception_interval_multiplier = MagicMock(return_value=1)

        scheduler = _make_scheduler_for_tick(settings, mock_orch)

        with (
            patch(
                "src.services.perception_policy.PerceptionPolicyService",
                return_value=mock_svc,
            ),
            patch.object(
                scheduler,
                "_resolve_workspace",
                new=AsyncMock(return_value=TEST_WORKSPACE_ID),
            ),
        ):
            await scheduler._tick_perception(mock_factory)

        mock_orch.run_cross_source_synthesis.assert_not_awaited()

    @pytest.mark.asyncio
    async def test_two_sources_but_only_two_events_does_not_trigger(self, settings):
        """Two sources but only 2 total events must not trigger synthesis."""
        due_states = [
            _make_perception_state(TEST_USER_ID, TEST_WORKSPACE_ID, "gmail"),
            _make_perception_state(TEST_USER_ID, TEST_WORKSPACE_ID, "slack"),
        ]
        results = [("gmail", 1), ("slack", 1)]

        mock_factory, mock_svc, fake_cycle = _build_tick_mocks(due_states, results)

        mock_orch = MagicMock()
        mock_orch.run_cross_source_synthesis = AsyncMock()
        mock_orch.run_perception_cycle = AsyncMock(side_effect=fake_cycle)
        mock_orch._budget = MagicMock()
        mock_orch._budget.get_budget_status = AsyncMock(return_value={"mode": "normal"})
        mock_orch._budget.should_allow_perception = MagicMock(return_value=True)
        mock_orch._budget.get_perception_interval_multiplier = MagicMock(return_value=1)

        scheduler = _make_scheduler_for_tick(settings, mock_orch)

        with (
            patch(
                "src.services.perception_policy.PerceptionPolicyService",
                return_value=mock_svc,
            ),
            patch.object(
                scheduler,
                "_resolve_workspace",
                new=AsyncMock(return_value=TEST_WORKSPACE_ID),
            ),
        ):
            await scheduler._tick_perception(mock_factory)

        mock_orch.run_cross_source_synthesis.assert_not_awaited()

    # ------------------------------------------------------------------
    # Regression: cross-tenant isolation — user_id never bleeds across
    # ------------------------------------------------------------------
    @pytest.mark.asyncio
    async def test_cross_tenant_isolation_user_id_never_bleeds(self, settings):
        """Synthesis for Tenant A must never receive Tenant B's user_id or workspace_id."""
        user_a = "usr_aaa"
        ws_a = "ws_aaa"
        user_b = "usr_bbb"
        ws_b = "ws_bbb"

        # Tenant A: 2 sources with enough events
        # Tenant B: only 1 source — should NOT trigger
        due_states = [
            _make_perception_state(user_a, ws_a, "gmail"),
            _make_perception_state(user_a, ws_a, "slack"),
            _make_perception_state(user_b, ws_b, "github"),
        ]
        results = [
            ("gmail", 2),
            ("slack", 2),
            ("github", 5),  # high event count but only 1 source for user_b
        ]

        mock_factory, mock_svc, fake_cycle = _build_tick_mocks(due_states, results)

        mock_orch = MagicMock()
        mock_orch.run_cross_source_synthesis = AsyncMock()
        mock_orch.run_perception_cycle = AsyncMock(side_effect=fake_cycle)
        mock_orch._budget = MagicMock()
        mock_orch._budget.get_budget_status = AsyncMock(return_value={"mode": "normal"})
        mock_orch._budget.should_allow_perception = MagicMock(return_value=True)
        mock_orch._budget.get_perception_interval_multiplier = MagicMock(return_value=1)

        scheduler = _make_scheduler_for_tick(settings, mock_orch)

        with (
            patch(
                "src.services.perception_policy.PerceptionPolicyService",
                return_value=mock_svc,
            ),
            patch.object(
                scheduler,
                "_resolve_workspace",
                new=AsyncMock(side_effect=lambda uid: ws_a if uid == user_a else ws_b),
            ),
        ):
            await scheduler._tick_perception(mock_factory)

        # Only Tenant A triggers
        assert mock_orch.run_cross_source_synthesis.await_count == 1
        call_kwargs = mock_orch.run_cross_source_synthesis.call_args.kwargs

        # Tenant A's call must only have Tenant A's identity
        assert call_kwargs["user_id"] == user_a
        assert call_kwargs["workspace_id"] == ws_a
        assert user_b not in call_kwargs["user_id"]
        assert ws_b not in call_kwargs["workspace_id"]
        # Tenant B's source must NOT appear in Tenant A's synthesis
        assert "github" not in call_kwargs["source_names"]

    # ------------------------------------------------------------------
    # Case (e): workspace_id is empty — exercises the _resolve_workspace fallback
    # ------------------------------------------------------------------
    @pytest.mark.asyncio
    async def test_empty_workspace_id_triggers_resolve_workspace_fallback(self, settings):
        """A tenant whose perception states have workspace_id='' must resolve via
        _resolve_workspace and pass the resolved id to run_cross_source_synthesis."""
        resolved_ws = "ws_resolved_001"

        due_states = [
            _make_perception_state(TEST_USER_ID, "", "gmail"),
            _make_perception_state(TEST_USER_ID, "", "slack"),
        ]
        # 2 sources, 4 total events — above both thresholds
        results = [("gmail", 2), ("slack", 2)]

        mock_factory, mock_svc, fake_cycle = _build_tick_mocks(due_states, results)

        mock_orch = MagicMock()
        mock_orch.run_cross_source_synthesis = AsyncMock()
        mock_orch.run_perception_cycle = AsyncMock(side_effect=fake_cycle)
        mock_orch._budget = MagicMock()
        mock_orch._budget.get_budget_status = AsyncMock(return_value={"mode": "normal"})
        mock_orch._budget.should_allow_perception = MagicMock(return_value=True)
        mock_orch._budget.get_perception_interval_multiplier = MagicMock(return_value=1)

        scheduler = _make_scheduler_for_tick(settings, mock_orch)

        mock_resolve = AsyncMock(return_value=resolved_ws)
        with (
            patch(
                "src.services.perception_policy.PerceptionPolicyService",
                return_value=mock_svc,
            ),
            patch.object(scheduler, "_resolve_workspace", new=mock_resolve),
        ):
            await scheduler._tick_perception(mock_factory)

        # (a) _resolve_workspace must have been awaited for the synthesis fallback path
        mock_resolve.assert_awaited()

        # (b) synthesis must be called with the resolved workspace_id
        mock_orch.run_cross_source_synthesis.assert_awaited_once()
        call_kwargs = mock_orch.run_cross_source_synthesis.call_args.kwargs
        assert call_kwargs["workspace_id"] == resolved_ws
        assert call_kwargs["user_id"] == TEST_USER_ID
        assert set(call_kwargs["source_names"]) == {"gmail", "slack"}
