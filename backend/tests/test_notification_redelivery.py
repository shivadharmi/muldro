"""An approval request must survive the user being offline.

Live evidence (2026-08-19): four ``approval_request`` notifications sat at
``status='pending'``, ``sent_at=NULL`` forever. ``approval_request`` correctly
bypasses the priority + rate-limit filters, but with no active surface
``notify()`` returned ``{"status": "queued"}`` and nothing ever retried it. The
approvals expired unanswered 24h later and their runs were cancelled.

The retry tick that should have covered this had three defects of its own:

1. it called ``notify()``, which INSERTS A NEW ROW rather than re-delivering the
   existing one — one duplicate per tick for a permanently offline user;
2. it set ``status = "sent"`` unconditionally, so an undelivered notification
   was falsely recorded as delivered and never retried again;
3. it had no terminal state, so rows outside its 24h window stayed ``pending``
   forever with nothing ever looking at them again.

These pin the honest behaviour: re-deliver the SAME row, mark it sent ONLY when
a surface actually took it, and give up explicitly rather than silently.
"""

from datetime import datetime, timezone
from unittest.mock import AsyncMock, MagicMock

from src.services.notifier import Notifier


def _make_record(channel: str = "approval_request"):
    record = MagicMock()
    record.notification_id = "notif_test"
    record.user_id = "usr_test"
    record.workspace_id = "ws_test"
    record.channel = channel
    record.title = "Approve: List unread Gmail"
    record.body = "Trust gate"
    record.payload_json = {"approval_id": "apr_x"}
    record.created_at = datetime.now(timezone.utc)
    record.status = "pending"
    record.sent_at = None
    return record


def _make_notifier(*, surfaces: list[str], deliver_result: dict | None = None):
    registry = MagicMock()
    registry.get_active_surfaces = AsyncMock(return_value=surfaces)
    registry.get_preferred_surface = AsyncMock(return_value=surfaces[0] if surfaces else None)

    notifier = Notifier(surface_registry=registry, redis=None, websocket_sender=None, db=None)
    notifier._deliver = AsyncMock(return_value=deliver_result or {"status": "sent"})
    return notifier


async def test_deliver_existing_does_not_create_a_new_row():
    """Re-delivery must reuse the persisted row — never insert a duplicate."""
    notifier = _make_notifier(surfaces=["web"])
    record = _make_record()

    result = await notifier.deliver_existing(record)

    assert result["status"] == "sent"
    delivered_notification = notifier._deliver.await_args.args[1]
    assert delivered_notification.notification_id == "notif_test", (
        "re-delivery must carry the ORIGINAL notification id, not a fresh one"
    )


async def test_deliver_existing_reports_queued_when_no_surface_is_active():
    """Offline user → queued, NOT sent. The row must stay retryable."""
    notifier = _make_notifier(surfaces=[])
    record = _make_record()

    result = await notifier.deliver_existing(record)

    assert result["status"] == "queued"
    notifier._deliver.assert_not_awaited()


async def test_deliver_existing_reports_failure_when_every_surface_refuses():
    """A surface that skips/errors is NOT a delivery."""
    notifier = _make_notifier(
        surfaces=["web"], deliver_result={"status": "skipped", "reason": "no_ws"}
    )
    record = _make_record()

    result = await notifier.deliver_existing(record)

    assert result["status"] == "failed"


async def test_approval_requests_broadcast_to_every_active_surface():
    """approval_request is a BROADCAST type — reach the user wherever they are."""
    notifier = _make_notifier(surfaces=["web", "slack"])
    record = _make_record(channel="approval_request")

    result = await notifier.deliver_existing(record)

    assert result["status"] == "sent"
    assert notifier._deliver.await_count == 2


async def test_non_broadcast_types_go_to_the_preferred_surface_only():
    notifier = _make_notifier(surfaces=["web", "slack"])
    record = _make_record(channel="info_update")

    await notifier.deliver_existing(record)

    assert notifier._deliver.await_count == 1


async def test_notify_marks_the_persisted_row_sent_on_real_delivery():
    """A delivered notification must not still read as 'pending' in the DB."""
    registry = MagicMock()
    registry.get_active_surfaces = AsyncMock(return_value=["web"])
    registry.get_preferred_surface = AsyncMock(return_value="web")

    db = MagicMock()
    db.add = MagicMock()
    db.flush = AsyncMock()
    db.execute = AsyncMock()

    notifier = Notifier(surface_registry=registry, redis=None, websocket_sender=None, db=db)
    notifier._deliver = AsyncMock(return_value={"status": "sent"})

    await notifier.notify(
        user_id="usr_test",
        notification_type="approval_request",
        title="Approve",
        body="body",
        data={},
        workspace_id="ws_test",
    )

    record = db.add.call_args.args[0]
    assert record.status == "sent"
    assert record.sent_at is not None
