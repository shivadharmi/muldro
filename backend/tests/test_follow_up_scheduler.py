"""Tests for Phase 3D: follow_up_at scheduler logic."""

from datetime import datetime, timedelta, timezone
from unittest.mock import AsyncMock, MagicMock

from tests.conftest import make_mock_settings


def _make_notification(notif_id, status="sent", follow_up_at=None):
    n = MagicMock()
    n.notification_id = notif_id
    n.status = status
    n.follow_up_at = follow_up_at
    return n


class TestFollowUpScheduler:
    async def test_due_follow_ups_requeued(self):
        """Notifications with past follow_up_at are re-queued as pending."""
        from src.services.scheduler import SchedulerLoop

        settings = make_mock_settings()
        scheduler = SchedulerLoop(settings=settings)

        past = datetime.now(timezone.utc) - timedelta(hours=1)
        notif = _make_notification("n1", "sent", follow_up_at=past)

        mock_db = AsyncMock()
        mock_result = MagicMock()
        mock_result.scalars.return_value.all.return_value = [notif]
        mock_db.execute = AsyncMock(return_value=mock_result)
        mock_db.commit = AsyncMock()

        mock_factory = MagicMock()
        mock_factory.return_value.__aenter__ = AsyncMock(return_value=mock_db)
        mock_factory.return_value.__aexit__ = AsyncMock(return_value=False)

        await scheduler._check_follow_ups(mock_factory)

        assert notif.status == "pending"
        assert notif.follow_up_at is None
        mock_db.commit.assert_called_once()

    async def test_no_due_follow_ups(self):
        """No due follow-ups → no commit."""
        from src.services.scheduler import SchedulerLoop

        settings = make_mock_settings()
        scheduler = SchedulerLoop(settings=settings)

        mock_db = AsyncMock()
        mock_result = MagicMock()
        mock_result.scalars.return_value.all.return_value = []
        mock_db.execute = AsyncMock(return_value=mock_result)
        mock_db.commit = AsyncMock()

        mock_factory = MagicMock()
        mock_factory.return_value.__aenter__ = AsyncMock(return_value=mock_db)
        mock_factory.return_value.__aexit__ = AsyncMock(return_value=False)

        await scheduler._check_follow_ups(mock_factory)

        mock_db.commit.assert_not_called()

    async def test_follow_up_error_handled(self):
        """Errors in follow-up check are handled gracefully."""
        from src.services.scheduler import SchedulerLoop

        settings = make_mock_settings()
        scheduler = SchedulerLoop(settings=settings)

        mock_factory = MagicMock()
        mock_factory.return_value.__aenter__ = AsyncMock(side_effect=RuntimeError("DB down"))
        mock_factory.return_value.__aexit__ = AsyncMock(return_value=False)

        # Should not raise
        await scheduler._check_follow_ups(mock_factory)

    async def test_multiple_follow_ups_requeued(self):
        """Multiple due notifications are all re-queued."""
        from src.services.scheduler import SchedulerLoop

        settings = make_mock_settings()
        scheduler = SchedulerLoop(settings=settings)

        past = datetime.now(timezone.utc) - timedelta(minutes=30)
        notifs = [_make_notification(f"n{i}", "sent", follow_up_at=past) for i in range(3)]

        mock_db = AsyncMock()
        mock_result = MagicMock()
        mock_result.scalars.return_value.all.return_value = notifs
        mock_db.execute = AsyncMock(return_value=mock_result)
        mock_db.commit = AsyncMock()

        mock_factory = MagicMock()
        mock_factory.return_value.__aenter__ = AsyncMock(return_value=mock_db)
        mock_factory.return_value.__aexit__ = AsyncMock(return_value=False)

        await scheduler._check_follow_ups(mock_factory)

        for n in notifs:
            assert n.status == "pending"
            assert n.follow_up_at is None
        mock_db.commit.assert_called_once()

    async def test_pending_notifications_also_requeued(self):
        """Pending notifications with due follow_up_at also get cleared."""
        from src.services.scheduler import SchedulerLoop

        settings = make_mock_settings()
        scheduler = SchedulerLoop(settings=settings)

        past = datetime.now(timezone.utc) - timedelta(hours=2)
        notif = _make_notification("n1", "pending", follow_up_at=past)

        mock_db = AsyncMock()
        mock_result = MagicMock()
        mock_result.scalars.return_value.all.return_value = [notif]
        mock_db.execute = AsyncMock(return_value=mock_result)
        mock_db.commit = AsyncMock()

        mock_factory = MagicMock()
        mock_factory.return_value.__aenter__ = AsyncMock(return_value=mock_db)
        mock_factory.return_value.__aexit__ = AsyncMock(return_value=False)

        await scheduler._check_follow_ups(mock_factory)

        assert notif.status == "pending"
        assert notif.follow_up_at is None
