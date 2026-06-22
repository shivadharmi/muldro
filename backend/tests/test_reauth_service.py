"""Tests for ReauthService — needs-reauth marking, perception pause/resume,
deferred-run requeue, and dedup'd notification.
"""

from contextlib import asynccontextmanager
from unittest.mock import AsyncMock, MagicMock

import pytest

from src.services.reauth_service import ReauthService
from tests.conftest import make_mock_settings


def _make_db():
    db = AsyncMock()
    db.execute = AsyncMock()
    db.commit = AsyncMock()
    db.add = MagicMock()
    return db


def _db_factory_for(db):
    @asynccontextmanager
    async def _factory():
        yield db

    return _factory


def _scalars_result(rows):
    res = MagicMock()
    scalars = MagicMock()
    scalars.all.return_value = rows
    res.scalars.return_value = scalars
    return res


def _make_redis(exists=0):
    redis = AsyncMock()
    redis.set = AsyncMock(return_value=True)
    redis.delete = AsyncMock(return_value=1)
    # set NX returns truthy only when key did not exist
    redis.set.return_value = None if exists else True
    return redis


def _make_inst(status="active", health_status="healthy"):
    inst = MagicMock()
    inst.status = status
    inst.health_status = health_status
    inst.server_name = "google-workspace"
    return inst


def _make_pstate(source="gmail", mode="poll", circuit_state="open", consecutive_failures=3):
    p = MagicMock()
    p.source = source
    p.mode = mode
    p.circuit_state = circuit_state
    p.consecutive_failures = consecutive_failures
    p.pending_run = False
    p.last_error = None
    return p


def _make_run(status="running", checkpoint=None, source="background"):
    run = MagicMock()
    run.run_id = "run_001"
    run.status = status
    run.checkpoint = checkpoint
    run.source = source
    return run


@pytest.mark.asyncio
async def test_mark_needs_reauth_sets_status_pauses_and_notifies():
    db = _make_db()
    inst = _make_inst()
    pstate = _make_pstate(source="gmail")

    # First execute: installation lookup; subsequent: perception state list.
    db.execute.side_effect = [
        _scalars_result([inst]),
        _scalars_result([pstate]),
    ]
    notifier = MagicMock()
    notifier.notify = AsyncMock(return_value={"status": "sent"})
    redis = _make_redis(exists=0)

    svc = ReauthService(
        db_factory=_db_factory_for(db),
        notifier=notifier,
        redis=redis,
        settings=make_mock_settings(),
    )
    await svc.mark_needs_reauth("u1", "google", "revoked", workspace_id="ws_1")

    assert inst.status == "needs_reauth"
    assert inst.health_status == "unavailable"
    assert pstate.mode == "paused"
    assert pstate.last_error == "needs_reauth"
    notifier.notify.assert_awaited_once()
    db.commit.assert_awaited()


@pytest.mark.asyncio
async def test_apply_needs_reauth_writes_only_on_passed_db_no_commit_no_notify():
    # apply_needs_reauth must do DB writes ONLY on the caller's session: no
    # commit, no notify, and it must NOT open a second db_factory() session.
    db = _make_db()
    inst = _make_inst()
    pstate = _make_pstate(source="gmail")
    db.execute.side_effect = [
        _scalars_result([inst]),  # installation lookup
        _scalars_result([pstate]),  # perception state list
    ]

    factory_calls = {"n": 0}

    def _counting_factory():
        factory_calls["n"] += 1
        return _db_factory_for(_make_db())()

    notifier = MagicMock()
    notifier.notify = AsyncMock()

    svc = ReauthService(
        db_factory=_counting_factory,
        notifier=notifier,
        redis=_make_redis(),
        settings=make_mock_settings(),
    )
    await svc.apply_needs_reauth(db, "u1", "google", "revoked")

    assert inst.status == "needs_reauth"
    assert inst.health_status == "unavailable"
    assert pstate.mode == "paused"
    assert pstate.last_error == "needs_reauth"
    # No second session opened, no commit, no notify.
    assert factory_calls["n"] == 0
    db.commit.assert_not_awaited()
    notifier.notify.assert_not_awaited()


@pytest.mark.asyncio
async def test_mark_needs_reauth_no_notify_when_flag_false():
    db = _make_db()
    db.execute.side_effect = [
        _scalars_result([_make_inst()]),
        _scalars_result([_make_pstate()]),
    ]
    notifier = MagicMock()
    notifier.notify = AsyncMock()
    svc = ReauthService(
        db_factory=_db_factory_for(db),
        notifier=notifier,
        redis=_make_redis(),
        settings=make_mock_settings(),
    )
    await svc.mark_needs_reauth("u1", "google", "revoked", workspace_id="ws_1", notify=False)
    notifier.notify.assert_not_awaited()


@pytest.mark.asyncio
async def test_notify_reauth_dedup_prevents_second_send():
    notifier = MagicMock()
    notifier.notify = AsyncMock(return_value={"status": "sent"})

    # Redis SET NX: first call succeeds (returns truthy), second returns None.
    redis = AsyncMock()
    redis.set = AsyncMock(side_effect=[True, None])

    svc = ReauthService(
        db_factory=_db_factory_for(_make_db()),
        notifier=notifier,
        redis=redis,
        settings=make_mock_settings(),
    )
    await svc.notify_reauth("u1", "google", "revoked", workspace_id="ws_1")
    await svc.notify_reauth("u1", "google", "revoked", workspace_id="ws_1")

    assert notifier.notify.await_count == 1


@pytest.mark.asyncio
async def test_pause_perception_sources():
    db = _make_db()
    p1 = _make_pstate(source="gmail", mode="poll")
    p2 = _make_pstate(source="calendar", mode="hybrid")
    db.execute.return_value = _scalars_result([p1, p2])

    svc = ReauthService(
        db_factory=_db_factory_for(db),
        notifier=MagicMock(),
        redis=_make_redis(),
        settings=make_mock_settings(),
    )
    await svc.pause_perception_sources(db, "u1", "google")
    assert p1.mode == "paused"
    assert p2.mode == "paused"
    assert p1.last_error == "needs_reauth"


@pytest.mark.asyncio
async def test_resume_perception_sources():
    db = _make_db()
    p1 = _make_pstate(source="gmail", mode="paused", circuit_state="open", consecutive_failures=5)
    db.execute.return_value = _scalars_result([p1])

    svc = ReauthService(
        db_factory=_db_factory_for(db),
        notifier=MagicMock(),
        redis=_make_redis(),
        settings=make_mock_settings(),
    )
    await svc.resume_perception_sources(db, "u1", "google")
    assert p1.mode == "poll"
    assert p1.pending_run is True
    assert p1.circuit_state == "closed"
    assert p1.consecutive_failures == 0


@pytest.mark.asyncio
async def test_defer_run_transitions_and_stores_provider():
    db = _make_db()
    run = _make_run(status="running", checkpoint=None)
    svc = ReauthService(
        db_factory=_db_factory_for(db),
        notifier=MagicMock(),
        redis=_make_redis(),
        settings=make_mock_settings(),
    )
    await svc.defer_run(db, run, "google")
    assert run.status == "awaiting_reauth"
    assert run.checkpoint["awaiting_provider"] == "google"


@pytest.mark.asyncio
async def test_requeue_deferred_runs_transitions_matching_to_pending():
    db = _make_db()
    match = _make_run(status="awaiting_reauth", checkpoint={"awaiting_provider": "google"})
    other = _make_run(status="awaiting_reauth", checkpoint={"awaiting_provider": "github"})
    db.execute.return_value = _scalars_result([match, other])

    svc = ReauthService(
        db_factory=_db_factory_for(db),
        notifier=MagicMock(),
        redis=_make_redis(),
        settings=make_mock_settings(),
    )
    count = await svc.requeue_deferred_runs(db, "u1", "google")
    assert match.status == "pending"
    assert other.status == "awaiting_reauth"  # unchanged
    assert count == 1


@pytest.mark.asyncio
async def test_requeue_deferred_plan_run_becomes_tick_visible():
    # A deferred autonomous run carries source="plan" (Governor.evaluate_plan).
    # The background-tasks tick only selects source in (background,
    # approval_resume), so requeue must flip the source to a tick-visible value
    # or the run is orphaned forever.
    db = _make_db()
    plan_run = _make_run(
        status="awaiting_reauth",
        checkpoint={"awaiting_provider": "google"},
        source="plan",
    )
    db.execute.return_value = _scalars_result([plan_run])

    svc = ReauthService(
        db_factory=_db_factory_for(db),
        notifier=MagicMock(),
        redis=_make_redis(),
        settings=make_mock_settings(),
    )
    count = await svc.requeue_deferred_runs(db, "u1", "google")
    assert plan_run.status == "pending"
    assert plan_run.source in ("background", "approval_resume")
    assert count == 1


@pytest.mark.asyncio
async def test_requeue_preserves_already_tick_visible_source():
    db = _make_db()
    bg_run = _make_run(
        status="awaiting_reauth",
        checkpoint={"awaiting_provider": "google"},
        source="background",
    )
    db.execute.return_value = _scalars_result([bg_run])

    svc = ReauthService(
        db_factory=_db_factory_for(db),
        notifier=MagicMock(),
        redis=_make_redis(),
        settings=make_mock_settings(),
    )
    await svc.requeue_deferred_runs(db, "u1", "google")
    assert bg_run.source == "background"


@pytest.mark.asyncio
async def test_clear_reauth_resumes_requeues_and_clears_dedup():
    db = _make_db()
    inst = _make_inst(status="needs_reauth", health_status="unavailable")
    pstate = _make_pstate(source="gmail", mode="paused")
    run = _make_run(status="awaiting_reauth", checkpoint={"awaiting_provider": "google"})

    db.execute.side_effect = [
        _scalars_result([inst]),  # installation lookup
        _scalars_result([pstate]),  # resume perception
        _scalars_result([run]),  # requeue deferred
    ]
    redis = AsyncMock()
    redis.delete = AsyncMock(return_value=1)

    svc = ReauthService(
        db_factory=_db_factory_for(db),
        notifier=MagicMock(),
        redis=redis,
        settings=make_mock_settings(),
    )
    await svc.clear_reauth("u1", "google", workspace_id="ws_1")

    assert inst.status == "active"
    assert inst.health_status == "healthy"
    assert pstate.mode == "poll"
    assert run.status == "pending"
    redis.delete.assert_awaited()
    db.commit.assert_awaited()
