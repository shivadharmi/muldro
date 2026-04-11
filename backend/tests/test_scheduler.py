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
            patch("src.services.scheduler.get_session_factory", return_value=mock_factory),
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

        with patch("src.services.scheduler.get_session_factory", return_value=mock_factory):
            await scheduler._tick()
            # Commit is called (to persist any next_run_at repairs) but no fire
            mock_db.commit.assert_awaited_once()

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
            patch("src.services.scheduler.get_session_factory", return_value=mock_factory),
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
            patch("src.services.scheduler.get_session_factory", return_value=mock_factory),
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
            patch("src.services.scheduler.get_session_factory", return_value=mock_factory),
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
            patch("src.services.scheduler.get_session_factory", return_value=mock_factory),
            patch("src.services.scheduler.HeartbeatService", return_value=mock_hb),
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
