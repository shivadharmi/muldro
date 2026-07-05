"""Deferred-read tick: re-checks completed_unverified steps. Confirmed -> completed +
deferred trust increment (a direct record_approval_decision write); contradicted ->
partially_completed + async-divergence surface; still-unverified -> unchanged; past TTL
/ too-young -> not re-checked. Logic tested via the pure _should_recheck guard +
_apply_recheck with mocked collaborators (no DB, no scheduler bootstrap)."""

from types import SimpleNamespace
from unittest.mock import AsyncMock, MagicMock, patch

from src.services.scheduler.deferred_verification_tick import (
    DEFERRED_VERIFICATION_MIN_AGE_S,
    DEFERRED_VERIFICATION_TTL_S,
    _is_past_give_up_ttl,
    _should_recheck,
)
from src.services.verification.readback import VerifyVerdict

_TRUST_WRITE = "src.services.scheduler.deferred_verification_tick.record_approval_decision"


def _step(status="completed_unverified", age_s=120.0, verdict_meta=None):
    from datetime import datetime, timedelta, timezone

    completed_at = datetime.now(timezone.utc) - timedelta(seconds=age_s)
    return SimpleNamespace(
        step_id="stp_1",
        run_id="run_1",
        status=status,
        completed_at=completed_at,
        input_data={"capability": "calendar.create"},
        output_data={
            "verification": verdict_meta
            or {
                "capability": "calendar.create",
                "risk_level": "medium",
                "reversible": False,
                "blast_radius": "external_multiple",
                "verdict": "unverified",
                "attempts": 1,
                "artifact_ref": {"event_id": "evt_1"},
            }
        },
    )


def _mock_db():
    db = MagicMock()
    db.flush = AsyncMock()
    return db


def test_give_up_ttl_boundary():
    from datetime import datetime, timezone

    now = datetime.now(timezone.utc)
    fresh = _step(age_s=DEFERRED_VERIFICATION_TTL_S - 10)
    stale = _step(age_s=DEFERRED_VERIFICATION_TTL_S + 10)
    assert _is_past_give_up_ttl(fresh, now=now) is False
    assert _is_past_give_up_ttl(stale, now=now) is True


def test_should_recheck_min_age_boundary():
    from datetime import datetime, timezone

    now = datetime.now(timezone.utc)
    too_young = _step(age_s=DEFERRED_VERIFICATION_MIN_AGE_S - 5)
    ready = _step(age_s=DEFERRED_VERIFICATION_MIN_AGE_S + 5)
    # Inside the eventual-consistency window -> too soon to re-check.
    assert _should_recheck(too_young, now) is False
    # Past MIN_AGE but well before the TTL -> re-check.
    assert _should_recheck(ready, now) is True


def test_should_recheck_ttl_boundary():
    from datetime import datetime, timezone

    now = datetime.now(timezone.utc)
    fresh = _step(age_s=DEFERRED_VERIFICATION_TTL_S - 10)
    stale = _step(age_s=DEFERRED_VERIFICATION_TTL_S + 10)
    assert _should_recheck(fresh, now) is True
    assert _should_recheck(stale, now) is False  # gave up


async def test_confirmed_recheck_upgrades_and_increments_trust():
    from src.services.scheduler.deferred_verification_tick import _apply_recheck

    step = _step()
    db = _mock_db()
    notifier = MagicMock()
    notifier.notify = AsyncMock()
    run = SimpleNamespace(run_id="run_1", user_id="usr_1", workspace_id="ws_1")

    with patch(_TRUST_WRITE, new=AsyncMock()) as record_decision:
        await _apply_recheck(db, run, step, VerifyVerdict.CONFIRMED, notifier=notifier)

    assert step.status == "completed"
    # Deferred trust increment is a direct record_approval_decision "approved" write.
    record_decision.assert_awaited_once()
    args = record_decision.await_args.args
    assert args[0] is db  # DB-only write — no TrustGate/Anthropic client involved
    assert args[-1] == "approved"
    notifier.notify.assert_not_awaited()


async def test_contradicted_recheck_diverges_and_holds_for_briefing():
    from src.services.scheduler.deferred_verification_tick import _apply_recheck

    step = _step()
    db = _mock_db()
    notifier = MagicMock()
    notifier.notify = AsyncMock()
    run = SimpleNamespace(run_id="run_1", user_id="usr_1", workspace_id="ws_1")

    with patch(_TRUST_WRITE, new=AsyncMock()) as record_decision:
        await _apply_recheck(db, run, step, VerifyVerdict.CONTRADICTED, notifier=notifier)

    assert step.status == "partially_completed"
    record_decision.assert_not_awaited()  # divergence is NOT a positive trust outcome
    notifier.notify.assert_awaited_once()  # async-divergence surface raised
    # verification_divergence is a non-bypass type -> default priority 0.5 -> the
    # Notifier holds it for the next briefing rather than interrupting an absent user.
    assert notifier.notify.await_args.kwargs["notification_type"] == "verification_divergence"


async def test_still_unverified_recheck_leaves_status_unchanged():
    from src.services.scheduler.deferred_verification_tick import _apply_recheck

    step = _step()
    notifier = MagicMock()
    notifier.notify = AsyncMock()
    run = SimpleNamespace(run_id="run_1", user_id="usr_1", workspace_id="ws_1")

    with patch(_TRUST_WRITE, new=AsyncMock()) as record_decision:
        await _apply_recheck(_mock_db(), run, step, VerifyVerdict.UNVERIFIED, notifier=notifier)

    assert step.status == "completed_unverified"  # will retry next tick until TTL
    record_decision.assert_not_awaited()
    notifier.notify.assert_not_awaited()
