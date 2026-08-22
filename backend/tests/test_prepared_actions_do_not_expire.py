"""Staged work waits for a human, and a timer is not one.

A ``prepared_action`` approval is an external write a gate RECORDED instead of running,
because nobody was reachable. It used to carry a 7-day deadline, after which
``HeartbeatService._expire_approvals`` flipped it to ``expired`` — deleting a
fully-derived write with nobody having decided anything, no audit row and no notice.

Making that drop *visible* was the wrong fix. A deadline is only meaningful when
something is parked on the answer, which is true of a run-linked approval and false of
staged work: nothing waits on it, the founder simply has not looked yet.

Two invariants:

* a prepared approval is created with NO ``expires_at``, and therefore can never be
  selected by the expiry sweep, which filters on ``expires_at IS NOT NULL``;
* every approval that DOES expire — run-linked ones still do — leaves an
  ``approval_expired`` audit row, so the outcome nobody chose is at least as recorded
  as the two somebody did.

The trap this pins: ``create_approval`` reads ``expires_at=None`` as "use the 24h
default". Expressing "never" by passing None would give staged work a life FOUR TIMES
SHORTER than the TTL it replaced. The exemption therefore lives in the factory, keyed
on the approval type, not at either gate.
"""

from datetime import datetime, timedelta, timezone
from unittest.mock import AsyncMock, MagicMock

from src.deep_runtime.middleware.approval_persistence import (
    PREPARED_KEY,
    prepared_approval_overrides,
)
from src.models.approvals import PREPARED_APPROVAL_TYPE, Approval
from src.services.approval_service import NON_EXPIRING_TYPES, create_approval
from src.services.heartbeat import HeartbeatService
from tests.conftest import make_mock_settings


def _make_approval(approval_id: str, *, prepared: bool = False) -> Approval:
    approval = Approval()
    approval.approval_id = approval_id
    approval.user_id = "usr_test"
    approval.workspace_id = "ws_test"
    approval.execution_id = "" if prepared else "run_linked"
    approval.approval_type = PREPARED_APPROVAL_TYPE if prepared else "send_email"
    approval.title = "Send follow-up to Acme"
    approval.risk_level = "high"
    approval.status = "pending"
    approval.expires_at = datetime.now(timezone.utc) - timedelta(hours=1)
    approval.artifact_refs = {PREPARED_KEY: True} if prepared else {}
    return approval


def _make_service(approvals, *, run=None):
    db = MagicMock()
    db.flush = AsyncMock()
    approvals_result = MagicMock()
    approvals_result.scalars.return_value.all.return_value = list(approvals)
    results = [approvals_result]
    for approval in approvals:
        if approval.execution_id:
            run_result = MagicMock()
            run_result.scalar_one_or_none.return_value = run
            results.append(run_result)
    db.execute = AsyncMock(side_effect=results)
    return db, HeartbeatService(settings=make_mock_settings(), db=db)


def _audit_entries(db):
    return [call.args[0] for call in db.add.call_args_list]


def test_the_prepared_override_carries_no_deadline():
    approval_type, expires_at = prepared_approval_overrides(True)
    assert approval_type == PREPARED_APPROVAL_TYPE
    assert expires_at is None, "staged work waits for a human, not for a clock"


def test_an_interrupted_write_keeps_todays_defaults():
    """Only the PREPARE path is exempt — a live approval still gets its deadline."""
    assert prepared_approval_overrides(False) == (None, None)


async def test_the_factory_refuses_to_default_a_prepared_approval_to_24h():
    """The whole trap: `expires_at=None` means "use the default" for every other type."""
    db = MagicMock()
    db.add = MagicMock()

    prepared = await create_approval(
        db,
        user_id="usr_test",
        workspace_id="ws_test",
        approval_type=PREPARED_APPROVAL_TYPE,
        title="Send follow-up to Acme",
        requested_by="usr_test",
        expires_at=None,
    )
    assert prepared.expires_at is None

    live = await create_approval(
        db,
        user_id="usr_test",
        workspace_id="ws_test",
        approval_type="send_email",
        title="Send follow-up to Acme",
        requested_by="usr_test",
        expires_at=None,
    )
    assert live.expires_at is not None, "a run-linked approval still has a deadline"


def test_the_exemption_names_the_prepared_type():
    assert PREPARED_APPROVAL_TYPE in NON_EXPIRING_TYPES


async def test_a_row_with_no_deadline_cannot_be_swept():
    """The sweep filters `expires_at IS NOT NULL`, so a prepared row is unreachable.

    Asserted against the emitted SQL rather than the result set: the query is the thing
    that protects staged work, and a fixture returning nothing would pass either way.
    """
    _, service = _make_service([])
    await service._expire_approvals("usr_test")
    sql = str(service._db.execute.await_args.args[0])
    assert "expires_at IS NOT NULL" in sql


async def test_every_expiry_is_audited():
    approvals = [_make_approval("apr_1"), _make_approval("apr_2")]
    db, service = _make_service(approvals)

    count = await service._expire_approvals("usr_test")

    assert count == 2
    assert all(a.status == "expired" for a in approvals)
    audited = _audit_entries(db)
    assert [e.action_type for e in audited] == ["approval_expired", "approval_expired"]
    assert {e.approval_id for e in audited} == {"apr_1", "apr_2"}
    assert all(e.policy_decision == "expired_unanswered" for e in audited)


async def test_the_audit_records_what_expired():
    approvals = [_make_approval("apr_1")]
    db, service = _make_service(approvals)

    await service._expire_approvals("usr_test")

    entry = _audit_entries(db)[0]
    assert "Send follow-up to Acme" in (entry.summary or "")
    assert entry.details["approval_type"] == "send_email"
    assert entry.details["risk_level"] == "high"
    assert entry.details["expires_at"] is not None
    assert entry.details["prepared"] is False
