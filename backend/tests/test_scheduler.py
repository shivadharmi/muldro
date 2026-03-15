"""Tests for SchedulerLoop — backend-owned dynamic scheduling."""

from datetime import datetime, timedelta, timezone
from unittest.mock import AsyncMock, MagicMock, patch

import pytest

from src.services.scheduler import SchedulerLoop, compute_next_run
from tests.conftest import make_mock_settings


@pytest.fixture
def settings():
    return make_mock_settings()


def _make_schedule(**overrides):
    """Factory for mock Schedule objects."""
    now = datetime.now(timezone.utc)
    defaults = dict(
        schedule_id="sched_test_001",
        user_id="usr_default",
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
            mock_db.commit.assert_awaited_once()

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
    @pytest.mark.asyncio
    async def test_fire_observe_source_calls_agent(self, settings):
        """observe_source should call run_agent_turn with source-specific message."""
        sched = _make_schedule(
            action_type="observe_source",
            action_config={"source": "gmail"},
        )

        mock_openclaw = MagicMock()
        mock_openclaw.run_agent_turn = AsyncMock()

        scheduler = SchedulerLoop(settings)
        await scheduler._fire(sched, mock_openclaw)

        mock_openclaw.run_agent_turn.assert_awaited_once()
        call_args = mock_openclaw.run_agent_turn.call_args[0][0]
        assert "[SCHEDULED:observe-gmail]" in call_args
        assert "jarvis_ingest_event" in call_args

    @pytest.mark.asyncio
    async def test_fire_generate_briefing_calls_agent(self, settings):
        """generate_briefing should call run_agent_turn."""
        sched = _make_schedule(
            action_type="generate_briefing",
            action_config={},
        )

        mock_openclaw = MagicMock()
        mock_openclaw.run_agent_turn = AsyncMock()

        scheduler = SchedulerLoop(settings)
        await scheduler._fire(sched, mock_openclaw)

        mock_openclaw.run_agent_turn.assert_awaited_once()
        call_args = mock_openclaw.run_agent_turn.call_args[0][0]
        assert "[SCHEDULED:briefing]" in call_args

    @pytest.mark.asyncio
    async def test_fire_heartbeat_runs_directly(self, settings):
        """heartbeat should run HeartbeatService directly, not via agent."""
        sched = _make_schedule(
            action_type="heartbeat",
            action_config={},
        )

        mock_openclaw = MagicMock()
        mock_openclaw.run_agent_turn = AsyncMock()

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
            await scheduler._fire(sched, mock_openclaw)

            mock_hb.run.assert_awaited_once_with(sched.user_id)
            mock_openclaw.run_agent_turn.assert_not_awaited()

    @pytest.mark.asyncio
    async def test_fire_custom_agent_task(self, settings):
        """custom_agent_task should pass instructions to agent."""
        sched = _make_schedule(
            action_type="custom_agent_task",
            action_config={"instructions": "Review open PRs and summarize"},
        )

        mock_openclaw = MagicMock()
        mock_openclaw.run_agent_turn = AsyncMock()

        scheduler = SchedulerLoop(settings)
        await scheduler._fire(sched, mock_openclaw)

        call_args = mock_openclaw.run_agent_turn.call_args[0][0]
        assert "[SCHEDULED:custom]" in call_args
        assert "Review open PRs" in call_args

    @pytest.mark.asyncio
    async def test_fire_wake_agent(self, settings):
        """wake_agent should call wake_agent with message."""
        sched = _make_schedule(
            action_type="wake_agent",
            action_config={"message": "Time for standup"},
        )

        mock_openclaw = MagicMock()
        mock_openclaw.wake_agent = AsyncMock()

        scheduler = SchedulerLoop(settings)
        await scheduler._fire(sched, mock_openclaw)

        mock_openclaw.wake_agent.assert_awaited_once_with("Time for standup")
