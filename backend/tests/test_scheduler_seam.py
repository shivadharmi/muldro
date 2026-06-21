"""Characterization (seam) tests for the SchedulerLoop decomposition (SVC-P2-2c).

These pin the behavior that MUST survive splitting ``scheduler.py`` into a
package, and are written structure-agnostically (exercising ``SchedulerLoop``
and ``compute_next_run`` through their public import path) so they stay valid
before and after the file→package refactor:

- the tick-cadence gating inside ``_tick`` (which sub-ticks run on which tick N),
- the full public / ``_tick_*`` method surface of ``SchedulerLoop``,
- ``compute_next_run`` cron behavior.

The one structure-sensitive detail is ``_GSF`` — the lookup location of
``get_session_factory`` as seen by ``_tick``. It moves with ``_tick`` into the
``_base`` submodule during the refactor; the constant is updated in lockstep.
"""

import asyncio
from datetime import datetime, timezone
from unittest.mock import AsyncMock, MagicMock, patch

import pytest

from src.services.scheduler import SchedulerLoop, compute_next_run
from tests.conftest import make_mock_settings

# Sub-ticks that must run on EVERY tick.
_EVERY_TICK = [
    "_tick_perception",
    "_check_follow_ups",
    "_tick_pending_notifications",
    "_tick_background_tasks",
    "_tick_run_health_check",
]
# Sub-ticks gated to every 5th tick (~150s).
_EVERY_5TH = ["_tick_eviction", "_tick_dlq_retry", "_tick_memory_expiration"]
# Sub-ticks gated to every 120th tick AND hour == 2 UTC (~nightly).
_EVERY_120TH = ["_tick_consolidation", "_tick_stability_refresh"]

# Where _tick looks up get_session_factory. Updated to the _base submodule
# when _tick moves there during the structure-only decomposition.
_GSF = "src.services.scheduler._base.get_session_factory"


def _empty_schedule_factory() -> MagicMock:
    """A get_session_factory replacement whose db yields no due schedules."""
    mock_db = AsyncMock()
    mock_result = MagicMock()
    mock_result.scalars.return_value.all.return_value = []
    mock_db.execute = AsyncMock(return_value=mock_result)
    mock_db.commit = AsyncMock()
    mock_factory = MagicMock()
    mock_factory.return_value.__aenter__ = AsyncMock(return_value=mock_db)
    mock_factory.return_value.__aexit__ = AsyncMock(return_value=False)
    return mock_factory


def _mocked_scheduler() -> SchedulerLoop:
    """A SchedulerLoop with every sub-tick replaced by an AsyncMock."""
    settings = make_mock_settings()
    settings.qdrant_url = None  # skip VectorStore construction inside _tick
    settings.neo4j_url = None
    scheduler = SchedulerLoop(settings, user_ids=["usr_test"])
    for name in _EVERY_TICK + _EVERY_5TH + _EVERY_120TH + ["_tick_persona_batch"]:
        setattr(scheduler, name, AsyncMock())
    return scheduler


class TestSchedulerApiSurface:
    """The composed SchedulerLoop must keep its full callable surface."""

    def test_compute_next_run_importable_and_correct(self):
        base = datetime(2026, 3, 15, 10, 0, tzinfo=timezone.utc)
        assert compute_next_run("*/15 * * * *", base) == datetime(
            2026, 3, 15, 10, 15, tzinfo=timezone.utc
        )
        assert compute_next_run("0 * * * *", base) == datetime(
            2026, 3, 15, 11, 0, tzinfo=timezone.utc
        )

    def test_scheduler_retains_full_method_surface(self):
        expected = [
            "run",
            "stop",
            "_tick",
            *_EVERY_TICK,
            *_EVERY_5TH,
            *_EVERY_120TH,
            "_tick_persona_batch",
            "_dispatch_dlq_entry",
            "_get_observation_sources",
            "_get_authorized_providers",
            "_resolve_workspace",
            "_fire",
        ]
        for name in expected:
            assert callable(getattr(SchedulerLoop, name, None)), f"missing {name}"
        assert SchedulerLoop.POLL_INTERVAL == 30

    def test_constructor_signature_preserved(self):
        scheduler = SchedulerLoop(make_mock_settings(), orchestrator="orch", user_ids=["u"])
        assert scheduler._orchestrator == "orch"
        assert scheduler._user_ids == ["u"]
        assert scheduler._running is False


class TestTickCadence:
    """Pin the modulo gating in _tick — the highest-value behavior to preserve."""

    @pytest.mark.asyncio
    async def test_every_tick_methods_run_each_tick(self):
        scheduler = _mocked_scheduler()
        with patch(_GSF, return_value=_empty_schedule_factory()):
            for _ in range(4):
                await scheduler._tick()
        for name in _EVERY_TICK:
            assert getattr(scheduler, name).await_count == 4, name
        # Persona batch is dispatched every tick; it self-gates on _tick_count % 10.
        assert scheduler._tick_persona_batch.await_count == 4

    @pytest.mark.asyncio
    async def test_fifth_tick_methods_gated_to_multiples_of_5(self):
        scheduler = _mocked_scheduler()
        with patch(_GSF, return_value=_empty_schedule_factory()):
            for _ in range(10):
                await scheduler._tick()
        # eviction / dlq_retry / memory_expiration fire on tick 5 and 10 only.
        for name in _EVERY_5TH:
            assert getattr(scheduler, name).await_count == 2, name

    @pytest.mark.asyncio
    async def test_not_fired_before_fifth_tick(self):
        scheduler = _mocked_scheduler()
        with patch(_GSF, return_value=_empty_schedule_factory()):
            for _ in range(4):
                await scheduler._tick()
        for name in _EVERY_5TH:
            assert getattr(scheduler, name).await_count == 0, name

    @pytest.mark.asyncio
    async def test_nightly_methods_not_fired_on_ordinary_ticks(self):
        # 120th-tick + hour-gated work must never fire within the first 15 ticks,
        # regardless of wall-clock hour (% 120 is never 0 for ticks 1..15).
        scheduler = _mocked_scheduler()
        with patch(_GSF, return_value=_empty_schedule_factory()):
            for _ in range(15):
                await scheduler._tick()
        for name in _EVERY_120TH:
            assert getattr(scheduler, name).await_count == 0, name

    @pytest.mark.asyncio
    async def test_tick_count_increments_per_tick(self):
        scheduler = _mocked_scheduler()
        with patch(_GSF, return_value=_empty_schedule_factory()):
            for _ in range(7):
                await scheduler._tick()
        assert scheduler._tick_count == 7


class TestSubTickIsolation:
    """A single hung sub-tick must not starve later sub-ticks (resume/health)."""

    @pytest.mark.asyncio
    async def test_hung_subtick_does_not_block_later_subticks(self):
        scheduler = _mocked_scheduler()
        # Make the perception sub-tick (step 1) hang forever.
        hang_started = {"flag": False}

        async def _hang(*_a, **_kw):
            hang_started["flag"] = True
            await asyncio.sleep(3600)

        scheduler._tick_perception = AsyncMock(side_effect=_hang)
        # Tighten the per-sub-tick timeout so the test runs fast.
        scheduler._settings.scheduler_subtick_timeout_s = 0.05

        with patch(_GSF, return_value=_empty_schedule_factory()):
            await scheduler._tick()

        # The hung perception tick was entered but timed out...
        assert hang_started["flag"] is True
        # ...and later sub-ticks (background-resume + health) still ran.
        assert scheduler._tick_background_tasks.await_count == 1
        assert scheduler._tick_run_health_check.await_count == 1
