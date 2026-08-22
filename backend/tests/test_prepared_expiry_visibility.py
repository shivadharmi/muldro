"""Staged work must not disappear in silence.

A ``prepared_action`` approval is an external write a gate recorded instead of running,
because no human was reachable. When it ages out unanswered, the action is gone — and it
is the ONE approval outcome with nothing to show for it: it has no run, so nothing gets
cancelled with an explanatory error and nothing appears in the feed. Before this, the
whole event was a status flip: no audit row, no notification, and the briefing's pointer
line silently decrementing.

Two invariants are pinned here:

* every expiry — prepared or run-linked — leaves an ``approval_expired`` audit row, so
  the outcome nobody chose is at least as recorded as the two somebody did;
* the founder is told ONCE per cycle, and the message says plainly that the actions were
  NOT performed. A founder who assumes staged work eventually ran is the realistic
  failure this text exists to prevent.
"""

from datetime import datetime, timedelta, timezone
from unittest.mock import AsyncMock, MagicMock

from src.deep_runtime.middleware.approval_persistence import PREPARED_KEY
from src.models.approvals import Approval
from src.services.heartbeat import HeartbeatService
from src.services.prepared_expiry_notice import MAX_LISTED, expired_prepared_notice
from tests.conftest import make_mock_settings


def _make_approval(
    approval_id: str,
    *,
    title: str = "Send follow-up to Acme",
    prepared: bool = True,
    workspace_id: str = "ws_test",
) -> Approval:
    approval = Approval()
    approval.approval_id = approval_id
    approval.user_id = "usr_test"
    approval.workspace_id = workspace_id
    # A prepared action has no run and no step — that emptiness is the point.
    approval.execution_id = "" if prepared else "run_linked"
    approval.approval_type = "prepared_action" if prepared else "send_email"
    approval.title = title
    approval.risk_level = "high"
    approval.status = "pending"
    approval.expires_at = datetime.now(timezone.utc) - timedelta(hours=1)
    approval.artifact_refs = {PREPARED_KEY: True} if prepared else {}
    return approval


def _make_service(approvals, *, notifier=None, run=None):
    """A session whose only query is the pending-approval sweep, unless a run-linked
    approval forces a second one."""
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
    return db, HeartbeatService(settings=make_mock_settings(), db=db, notifier=notifier)


def _audit_entries(db):
    """The AuditLog rows the service added to the session."""
    return [call.args[0] for call in db.add.call_args_list]


async def test_any_expired_approval_is_audited():
    """Approve and reject are audited; the outcome nobody chose must be too."""
    prepared = _make_approval("apr_prepared")
    run_linked = _make_approval("apr_run", prepared=False)
    db, service = _make_service([prepared, run_linked], run=None)

    await service._expire_approvals("usr_test")

    entries = _audit_entries(db)
    expired = [e for e in entries if e.action_type == "approval_expired"]
    assert len(expired) == 2, "every expiry gets an audit row, prepared or not"

    by_approval = {e.approval_id: e for e in expired}
    assert set(by_approval) == {"apr_prepared", "apr_run"}
    assert by_approval["apr_prepared"].details["prepared"] is True
    assert by_approval["apr_run"].details["prepared"] is False
    assert by_approval["apr_prepared"].details["risk_level"] == "high"
    assert by_approval["apr_prepared"].details["expires_at"]
    assert by_approval["apr_prepared"].policy_decision == "expired_unanswered"
    assert "Send follow-up to Acme" in by_approval["apr_prepared"].summary


async def test_prepared_expiry_sends_exactly_one_notification():
    """Prepared work is never announced per item — expiry must not be the exception."""
    approvals = [
        _make_approval("apr_1", title="Send follow-up to Acme"),
        _make_approval("apr_2", title="Create calendar event"),
        _make_approval("apr_3", title="Post to #general"),
    ]
    notifier = MagicMock()
    notifier.notify = AsyncMock()
    _, service = _make_service(approvals, notifier=notifier)

    await service._expire_approvals("usr_test")

    assert notifier.notify.await_count == 1, "one message for the cycle, not one per item"
    kwargs = notifier.notify.await_args.kwargs
    assert kwargs["title"] == "3 staged actions expired unreviewed"
    assert kwargs["data"]["approval_ids"] == ["apr_1", "apr_2", "apr_3"]
    assert kwargs["data"]["urgency"] > 0.5, "dropped writes outrank a routine update"
    assert kwargs["workspace_id"] == "ws_test"


async def test_each_workspace_gets_its_own_notification():
    """A batch spanning two workspaces must not be collapsed into one message.

    ``_expire_approvals`` sweeps by ``user_id`` alone, and a founder can belong to
    several workspaces. One notification for the whole batch would carry one
    workspace's action titles into another's message — a leak that every
    single-workspace assertion in this file passes straight over.
    """
    approvals = [
        _make_approval("apr_a", title="Send to Acme", workspace_id="ws_a"),
        _make_approval("apr_b", title="Post to #general", workspace_id="ws_b"),
    ]
    notifier = MagicMock()
    notifier.notify = AsyncMock()
    _, service = _make_service(approvals, notifier=notifier)

    await service._expire_approvals("usr_test")

    assert notifier.notify.await_count == 2, "one message per workspace, not one per cycle"
    by_workspace = {
        call.kwargs["workspace_id"]: call.kwargs for call in notifier.notify.await_args_list
    }
    assert set(by_workspace) == {"ws_a", "ws_b"}
    for workspace_id, other_title in (("ws_a", "Post to #general"), ("ws_b", "Send to Acme")):
        assert other_title not in by_workspace[workspace_id]["body"]
        assert by_workspace[workspace_id]["title"] == "1 staged action expired unreviewed"


async def test_notification_says_the_actions_were_not_performed():
    """The single most important sentence: nothing ran."""
    approvals = [
        _make_approval("apr_1", title="Send follow-up to Acme"),
        _make_approval("apr_2", title="Create calendar event"),
    ]
    notifier = MagicMock()
    notifier.notify = AsyncMock()
    _, service = _make_service(approvals, notifier=notifier)

    await service._expire_approvals("usr_test")

    body = notifier.notify.await_args.kwargs["body"]
    assert "NOT performed" in body
    assert "Send follow-up to Acme" in body
    assert "Create calendar event" in body
    assert "cannot be" in body and "recovered" in body


async def test_run_linked_expiry_sends_no_prepared_notification():
    """A run-linked approval's cancelled run already surfaces in the feed."""
    approvals = [_make_approval("apr_run", prepared=False)]
    notifier = MagicMock()
    notifier.notify = AsyncMock()
    _, service = _make_service(approvals, notifier=notifier, run=None)

    await service._expire_approvals("usr_test")

    notifier.notify.assert_not_awaited()


async def test_no_prepared_expiry_sends_nothing():
    approvals = []
    notifier = MagicMock()
    notifier.notify = AsyncMock()
    _, service = _make_service(approvals, notifier=notifier)

    count = await service._expire_approvals("usr_test")

    assert count == 0
    notifier.notify.assert_not_awaited()


async def test_notifier_failure_does_not_cost_the_expiry():
    """The durable record must not be hostage to an unreachable notifier."""
    approvals = [_make_approval("apr_1")]
    notifier = MagicMock()
    notifier.notify = AsyncMock(side_effect=RuntimeError("redis down"))
    db, service = _make_service(approvals, notifier=notifier)

    count = await service._expire_approvals("usr_test")

    assert count == 1
    assert approvals[0].status == "expired"
    assert any(e.action_type == "approval_expired" for e in _audit_entries(db))


async def test_no_notifier_still_expires_and_audits():
    approvals = [_make_approval("apr_1")]
    db, service = _make_service(approvals, notifier=None)

    count = await service._expire_approvals("usr_test")

    assert count == 1
    assert approvals[0].status == "expired"
    assert any(e.action_type == "approval_expired" for e in _audit_entries(db))


async def test_notice_is_none_for_an_empty_batch():
    assert expired_prepared_notice([]) is None


async def test_notice_title_is_singular_for_one():
    title, body = expired_prepared_notice([_make_approval("apr_1", title="Send invoice")])

    assert title == "1 staged action expired unreviewed"
    assert "This action was NOT performed" in body
    assert "- Send invoice" in body


async def test_notice_caps_the_list_and_says_how_many_more():
    approvals = [_make_approval(f"apr_{i}", title=f"Action {i}") for i in range(1, 8)]

    title, body = expired_prepared_notice(approvals)

    assert title == "7 staged actions expired unreviewed"
    listed = [ln for ln in body.splitlines() if ln.startswith("- ") and "...and" not in ln]
    assert len(listed) == MAX_LISTED, "the list is capped"
    assert "- Action 5" in body
    assert "- Action 6" not in body
    assert "...and 2 more" in body
