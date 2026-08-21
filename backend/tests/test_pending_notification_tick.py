"""The pending-notification tick must retry honestly.

``_tick_pending_notifications`` was the safety net that should have caught the
four ``approval_request`` notifications stranded at ``pending`` on 2026-08-19.
It could not, because it:

1. called ``notify()`` — which INSERTS A NEW ROW — instead of re-delivering the
   existing one, so a permanently offline user accrued a duplicate per tick;
2. set ``status = "sent"`` regardless of the return value, so a notification that
   was merely ``queued`` (no active surface) was recorded as delivered and never
   looked at again;
3. had no terminal state, so rows aging out of its 24h window stayed ``pending``
   forever with nothing left to examine them.

The rule these pin: **a notification is marked sent only when a surface actually
took it, and is given up on explicitly rather than silently.**
"""

from datetime import datetime, timedelta, timezone
from unittest.mock import AsyncMock, MagicMock

from src.services.scheduler.notification_tick import NotificationTickMixin


class _Ticker(NotificationTickMixin):
    def __init__(self, notifier):
        self._orchestrator = MagicMock()
        self._orchestrator._notifier = notifier


def _make_record(*, age_hours: float = 1.0):
    record = MagicMock()
    record.notification_id = "notif_pending"
    record.user_id = "usr_test"
    record.workspace_id = "ws_test"
    record.channel = "approval_request"
    record.title = "Approve: List unread Gmail"
    record.body = "Trust gate"
    record.payload_json = {"approval_id": "apr_x"}
    record.created_at = datetime.now(timezone.utc) - timedelta(hours=age_hours)
    record.status = "pending"
    record.sent_at = None
    return record


def _make_factory(records):
    db = MagicMock()
    db.commit = AsyncMock()
    result = MagicMock()
    result.scalars.return_value.all.return_value = records
    db.execute = AsyncMock(return_value=result)

    class _Factory:
        def __call__(self):
            return self

        async def __aenter__(self):
            return db

        async def __aexit__(self, *_a):
            return False

    return _Factory(), db


async def test_delivered_notification_is_marked_sent():
    notifier = MagicMock()
    notifier.deliver_existing = AsyncMock(return_value={"status": "sent", "surfaces": ["web"]})
    record = _make_record()
    factory, db = _make_factory([record])

    await _Ticker(notifier)._tick_pending_notifications(factory)

    assert record.status == "sent"
    assert record.sent_at is not None
    notifier.deliver_existing.assert_awaited_once_with(record)


async def test_queued_notification_stays_pending_for_the_next_tick():
    """Nobody was reachable — the row must remain retryable, NOT read as delivered."""
    notifier = MagicMock()
    notifier.deliver_existing = AsyncMock(return_value={"status": "queued", "surfaces": []})
    record = _make_record()
    factory, _ = _make_factory([record])

    await _Ticker(notifier)._tick_pending_notifications(factory)

    assert record.status == "pending"
    assert record.sent_at is None


async def test_retry_never_creates_a_duplicate_row():
    """notify() inserts; deliver_existing does not. The tick must use the latter."""
    notifier = MagicMock()
    notifier.notify = AsyncMock()
    notifier.deliver_existing = AsyncMock(return_value={"status": "queued", "surfaces": []})
    factory, _ = _make_factory([_make_record()])

    await _Ticker(notifier)._tick_pending_notifications(factory)

    notifier.notify.assert_not_awaited()


async def test_notification_past_the_retry_window_is_given_up_on_explicitly():
    """Giving up must be a visible terminal state, not an invisible query boundary."""
    notifier = MagicMock()
    notifier.deliver_existing = AsyncMock(return_value={"status": "queued", "surfaces": []})
    record = _make_record(age_hours=200)
    factory, _ = _make_factory([record])

    await _Ticker(notifier)._tick_pending_notifications(factory)

    assert record.status == "expired"
    notifier.deliver_existing.assert_not_awaited()


async def test_no_notifier_is_a_no_op():
    ticker = _Ticker(None)
    factory, _ = _make_factory([_make_record()])

    await ticker._tick_pending_notifications(factory)
