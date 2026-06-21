"""Resume reaper + stuck-resume metric (run_health_tick).

Closes the P0 no-recovery gap: a run the user already approved
(``source='approval_resume'``) but that the background tick never resumed has
ZERO recovery path today, because the existing health tick deliberately skips
``approval_resume`` runs. The reaper re-drives stale ones through the same
idempotent ``resume_run`` path and, after an attempt cap, fails them to DLQ.
"""

from datetime import datetime, timedelta, timezone
from unittest.mock import AsyncMock, MagicMock, patch

import pytest

from src.services.scheduler import SchedulerLoop
from tests.conftest import make_mock_settings


def _make_run(**overrides):
    now = datetime.now(timezone.utc)
    defaults = dict(
        run_id="run_stale_001",
        status="awaiting_approval",
        source="approval_resume",
        updated_at=now - timedelta(minutes=10),
        workspace_id="ws_1",
        user_id="usr_1",
        plan_id="plan_1",
        retry_count=0,
        max_retries=3,
        error=None,
        completed_at=None,
    )
    defaults.update(overrides)
    run = MagicMock()
    for k, v in defaults.items():
        setattr(run, k, v)
    return run


def _factory_yielding(runs, count_value=0):
    """A session factory whose db.execute returns `runs` for SELECTs and a
    scalar count for the gauge query."""
    db = AsyncMock()

    def _execute(stmt, *a, **kw):
        result = MagicMock()
        result.scalars.return_value.all.return_value = runs
        result.scalar.return_value = count_value
        result.scalar_one_or_none.return_value = None
        return result

    db.execute = AsyncMock(side_effect=_execute)
    db.commit = AsyncMock()
    db.flush = AsyncMock()
    db.get = AsyncMock(return_value=None)
    factory = MagicMock()
    factory.return_value.__aenter__ = AsyncMock(return_value=db)
    factory.return_value.__aexit__ = AsyncMock(return_value=False)
    return factory, db


def _scheduler_with_orch():
    settings = make_mock_settings()
    settings.resume_reaper_stale_after_s = 300.0
    settings.resume_reaper_max_attempts = 5
    orch = MagicMock()
    orch._execute_tool = AsyncMock()
    orch._budget = MagicMock()
    orch._circuit_breaker = MagicMock()
    sched = SchedulerLoop(settings, orchestrator=orch, user_ids=["usr_1"])
    return sched


@pytest.mark.asyncio
async def test_stale_approval_resume_run_is_redriven():
    """A stale approval_resume run gets re-driven through resume_run."""
    sched = _scheduler_with_orch()
    stale = _make_run()
    factory, _db = _factory_yielding([stale])

    fake_executor = MagicMock()
    completed = MagicMock()
    completed.status = "completed"
    fake_executor.resume_run = AsyncMock(return_value=completed)

    with patch(
        "src.services.graph_executor.create_graph_executor",
        AsyncMock(return_value=fake_executor),
    ):
        await sched._reap_stale_resume_runs(factory)

    fake_executor.resume_run.assert_awaited_once_with("run_stale_001")


@pytest.mark.asyncio
async def test_fresh_background_awaiting_approval_is_not_touched():
    """A run paused at a NEW gate (source='background') must NOT be auto-resumed."""
    sched = _scheduler_with_orch()
    # Query is the discriminator: the reaper SELECTs only source='approval_resume'.
    # Simulate the DB returning nothing for that filter.
    factory, _db = _factory_yielding([])

    create_mock = AsyncMock()
    with patch("src.services.graph_executor.create_graph_executor", create_mock):
        await sched._reap_stale_resume_runs(factory)

    create_mock.assert_not_called()


@pytest.mark.asyncio
async def test_reaper_no_op_without_orchestrator():
    """Guard: no orchestrator → no work (like the background tick)."""
    settings = make_mock_settings()
    sched = SchedulerLoop(settings, orchestrator=None)
    factory, _db = _factory_yielding([_make_run()])

    create_mock = AsyncMock()
    with patch("src.services.graph_executor.create_graph_executor", create_mock):
        await sched._reap_stale_resume_runs(factory)
    create_mock.assert_not_called()


@pytest.mark.asyncio
async def test_reaper_fails_run_after_max_attempts():
    """After resume_reaper_max_attempts, the run is failed (not hot-looped)."""
    sched = _scheduler_with_orch()
    # retry_count already at the cap; reaper should fail it instead of resuming.
    exhausted = _make_run(retry_count=5)
    factory, _db = _factory_yielding([exhausted])

    create_mock = AsyncMock()
    with patch("src.services.graph_executor.create_graph_executor", create_mock):
        await sched._reap_stale_resume_runs(factory)

    # No resume attempted; run forced to failed with an error dict.
    create_mock.assert_not_called()
    assert exhausted.status == "failed"
    assert isinstance(exhausted.error, dict)


@pytest.mark.asyncio
async def test_reaper_select_is_bounded_and_lock_safe():
    """The reaper SELECT must apply .limit() and FOR UPDATE SKIP LOCKED so it is
    bounded (cannot starve the sub-tick) and lock-safe (no double-drive)."""
    sched = _scheduler_with_orch()
    sched._settings.resume_reaper_batch_limit = 5
    stale = _make_run()

    captured = {}

    db = AsyncMock()

    def _execute(stmt, *a, **kw):
        # Capture the run SELECT construct (the one carrying FOR UPDATE / LIMIT).
        for_update = getattr(stmt, "_for_update_arg", None)
        limit = getattr(stmt, "_limit", None)
        if for_update is not None or limit is not None:
            captured["for_update"] = for_update
            captured["limit"] = limit
        result = MagicMock()
        result.scalars.return_value.all.return_value = [stale]
        result.scalar.return_value = 0
        result.scalar_one_or_none.return_value = None
        return result

    db.execute = AsyncMock(side_effect=_execute)
    db.commit = AsyncMock()
    db.flush = AsyncMock()
    db.get = AsyncMock(return_value=None)
    factory = MagicMock()
    factory.return_value.__aenter__ = AsyncMock(return_value=db)
    factory.return_value.__aexit__ = AsyncMock(return_value=False)

    fake_executor = MagicMock()
    completed = MagicMock()
    completed.status = "completed"
    fake_executor.resume_run = AsyncMock(return_value=completed)

    with patch(
        "src.services.graph_executor.create_graph_executor",
        AsyncMock(return_value=fake_executor),
    ):
        await sched._reap_stale_resume_runs(factory)

    # Bounded: limit matches the configured batch size.
    assert captured.get("limit") == 5
    # Lock-safe: SELECT … FOR UPDATE SKIP LOCKED.
    for_update = captured.get("for_update")
    assert for_update is not None
    assert getattr(for_update, "skip_locked", False) is True


@pytest.mark.asyncio
async def test_earlier_exhausted_run_persists_when_later_run_raises():
    """Per-run transaction isolation: run A (exhausted → failed+DLQ) must be
    durably committed BEFORE run B is driven, so run B raising + rolling back
    cannot discard run A's failed transition or DLQ enqueue.
    """
    sched = _scheduler_with_orch()

    run_a = _make_run(run_id="run_A", retry_count=5)  # exhausted → fail+DLQ
    run_b = _make_run(run_id="run_B", retry_count=0)  # will raise on resume

    # Track commit/rollback ordering and that A was committed before B failed.
    events: list[str] = []

    db = AsyncMock()

    def _execute(stmt, *a, **kw):
        result = MagicMock()
        result.scalars.return_value.all.return_value = [run_a, run_b]
        result.scalar.return_value = 0
        result.scalar_one_or_none.return_value = None
        return result

    db.execute = AsyncMock(side_effect=_execute)

    async def _commit():
        events.append("commit")

    async def _rollback():
        events.append("rollback")

    db.commit = AsyncMock(side_effect=_commit)
    db.rollback = AsyncMock(side_effect=_rollback)
    db.flush = AsyncMock()
    db.get = AsyncMock(return_value=run_b)
    factory = MagicMock()
    factory.return_value.__aenter__ = AsyncMock(return_value=db)
    factory.return_value.__aexit__ = AsyncMock(return_value=False)

    # Spy on the DLQ enqueue for run A.
    dlq_calls: list[str] = []

    async def _dlq(db_, run_id, ws_id, user_id):
        dlq_calls.append(run_id)

    sched._dlq_stale_resume = _dlq  # type: ignore[assignment]

    # Executor whose resume_run RAISES for run B.
    fake_executor = MagicMock()
    fake_executor.resume_run = AsyncMock(side_effect=RuntimeError("boom"))

    with patch(
        "src.services.graph_executor.create_graph_executor",
        AsyncMock(return_value=fake_executor),
    ):
        await sched._reap_stale_resume_runs(factory)

    # Run A was failed + DLQ'd and committed.
    assert run_a.status == "failed"
    assert isinstance(run_a.error, dict)
    assert dlq_calls == ["run_A"]
    # The first commit (run A) happened BEFORE run B's rollback — proving A's
    # outcome is durable independent of B's failure.
    assert "commit" in events
    assert events.index("commit") < events.index("rollback")


@pytest.mark.asyncio
async def test_stuck_resume_gauge_counts_stale_runs():
    """The loop gauges emit a count of stale approval_resume runs."""
    sched = _scheduler_with_orch()
    db = AsyncMock()
    count_result = MagicMock()
    count_result.scalar.return_value = 3
    db.execute = AsyncMock(return_value=count_result)

    with patch("src.services.metrics_service.MetricsService.set_stuck_resume_runs") as set_gauge:
        # _update_loop_gauges also queries running/pending; reuse the same
        # count result for all three SELECTs (value 3 is fine for the assertion
        # on the stuck-resume gauge specifically).
        await sched._update_loop_gauges(db)

    set_gauge.assert_called_once_with(3)
