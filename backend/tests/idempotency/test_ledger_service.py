"""IdempotencyLedger reserve/record semantics, on a mocked async session
(no live DB — matches the codebase's mocked-session test convention)."""

from types import SimpleNamespace
from unittest.mock import AsyncMock, MagicMock

from sqlalchemy.exc import IntegrityError

from src.services.idempotency.ledger import IdempotencyLedger


def _factory_for(session):
    cm = MagicMock()
    cm.__aenter__ = AsyncMock(return_value=session)
    cm.__aexit__ = AsyncMock(return_value=False)
    return MagicMock(return_value=cm)


def _session():
    s = AsyncMock()
    s.add = MagicMock()
    s.commit = AsyncMock()
    s.rollback = AsyncMock()
    s.flush = AsyncMock()
    return s


async def test_first_reserve_inserts_in_flight_and_proceeds():
    s = _session()
    ledger = IdempotencyLedger(_factory_for(s))
    out = await ledger.reserve(
        workspace_id="ws",
        run_id="r",
        step_id="st",
        capability="email.send",
        identity_key="r:st:email.send:sem:1",
    )
    assert out.already_done is False and out.in_flight_conflict is False
    s.add.assert_called_once()
    s.commit.assert_awaited()


async def test_resume_with_completed_entry_short_circuits():
    """Second reserve of the same identity -> IntegrityError -> read existing
    completed row -> already_done with the stored result (caller skips the call)."""
    s = _session()
    s.commit = AsyncMock(side_effect=IntegrityError("dup", {}, Exception()))
    existing = SimpleNamespace(
        status="completed",
        result_json={"status": "sent", "id": "msg_1"},
        ledger_id="idem_x",
    )
    result = MagicMock()
    result.scalar_one_or_none.return_value = existing
    s.execute = AsyncMock(return_value=result)

    ledger = IdempotencyLedger(_factory_for(s))
    out = await ledger.reserve(
        workspace_id="ws",
        run_id="r",
        step_id="st",
        capability="email.send",
        identity_key="r:st:email.send:sem:1",
    )
    assert out.already_done is True
    assert out.result == {"status": "sent", "id": "msg_1"}
    s.rollback.assert_awaited()


async def test_resume_with_in_flight_entry_reports_conflict():
    s = _session()
    s.commit = AsyncMock(side_effect=IntegrityError("dup", {}, Exception()))
    existing = SimpleNamespace(status="in_flight", result_json=None, ledger_id="idem_y")
    result = MagicMock()
    result.scalar_one_or_none.return_value = existing
    s.execute = AsyncMock(return_value=result)

    ledger = IdempotencyLedger(_factory_for(s))
    out = await ledger.reserve(
        workspace_id="ws",
        run_id="r",
        step_id="st",
        capability="email.send",
        identity_key="r:st:email.send:sem:1",
    )
    assert out.in_flight_conflict is True
    assert out.already_done is False


async def test_record_success_updates_row():
    s = _session()
    row = SimpleNamespace(status="in_flight", result_json=None, completed_at=None)
    s.get = AsyncMock(return_value=row)
    ledger = IdempotencyLedger(_factory_for(s))
    await ledger.record_success("idem_x", {"status": "sent"})
    assert row.status == "completed"
    assert row.result_json == {"status": "sent"}
    s.commit.assert_awaited()
