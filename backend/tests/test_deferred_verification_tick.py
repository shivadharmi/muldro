"""Deferred-read tick: re-checks completed_unverified steps. Confirmed -> completed +
deferred trust increment; contradicted -> partially_completed + async-divergence
surface; past TTL -> give up. Logic tested via the pure _resolve_recheck helper +
_apply_recheck with mocked collaborators (no DB, no scheduler bootstrap)."""

from types import SimpleNamespace
from unittest.mock import AsyncMock, MagicMock

from src.services.scheduler.deferred_verification_tick import (
    DEFERRED_VERIFICATION_TTL_S,
    _is_past_give_up_ttl,
)
from src.services.verification.readback import VerifyVerdict


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


def test_give_up_ttl_boundary():
    from datetime import datetime, timezone

    now = datetime.now(timezone.utc)
    fresh = _step(age_s=DEFERRED_VERIFICATION_TTL_S - 10)
    stale = _step(age_s=DEFERRED_VERIFICATION_TTL_S + 10)
    assert _is_past_give_up_ttl(fresh, now=now) is False
    assert _is_past_give_up_ttl(stale, now=now) is True


async def test_confirmed_recheck_upgrades_and_increments_trust():
    from src.services.scheduler.deferred_verification_tick import _apply_recheck

    step = _step()
    db = MagicMock()
    trust_gate = MagicMock()
    trust_gate.record_auto_execution_outcome = AsyncMock()
    notifier = MagicMock()
    notifier.notify = AsyncMock()
    run = SimpleNamespace(run_id="run_1", user_id="usr_1", workspace_id="ws_1")

    await _apply_recheck(
        db, run, step, VerifyVerdict.CONFIRMED, trust_gate=trust_gate, notifier=notifier
    )
    assert step.status == "completed"
    trust_gate.record_auto_execution_outcome.assert_awaited_once()
    notifier.notify.assert_not_awaited()


async def test_contradicted_recheck_diverges_and_holds_for_briefing():
    from src.services.scheduler.deferred_verification_tick import _apply_recheck

    step = _step()
    db = MagicMock()
    trust_gate = MagicMock()
    trust_gate.record_auto_execution_outcome = AsyncMock()
    notifier = MagicMock()
    notifier.notify = AsyncMock()
    run = SimpleNamespace(run_id="run_1", user_id="usr_1", workspace_id="ws_1")

    await _apply_recheck(
        db, run, step, VerifyVerdict.CONTRADICTED, trust_gate=trust_gate, notifier=notifier
    )
    assert step.status == "partially_completed"
    trust_gate.record_auto_execution_outcome.assert_not_awaited()
    notifier.notify.assert_awaited_once()  # async-divergence surface raised


async def test_still_unverified_recheck_leaves_status_unchanged():
    from src.services.scheduler.deferred_verification_tick import _apply_recheck

    step = _step()
    run = SimpleNamespace(run_id="run_1", user_id="usr_1", workspace_id="ws_1")
    await _apply_recheck(
        MagicMock(),
        run,
        step,
        VerifyVerdict.UNVERIFIED,
        trust_gate=MagicMock(),
        notifier=MagicMock(),
    )
    assert step.status == "completed_unverified"  # will retry next tick until TTL
