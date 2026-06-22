"""Follow-up notification re-queue and pending-notification delivery."""

import logging
from datetime import datetime, timedelta, timezone

from sqlalchemy import select

logger = logging.getLogger(__name__)


class NotificationTickMixin:
    """Re-queues due follow-ups and re-delivers pending notifications."""

    async def _check_follow_ups(self, factory) -> None:
        """Re-queue notifications whose follow_up_at has passed."""
        try:
            from src.models.notifications import Notification as NotifModel

            async with factory() as db:
                now = datetime.now(timezone.utc)
                result = await db.execute(
                    select(NotifModel)
                    .where(
                        NotifModel.follow_up_at <= now,
                        NotifModel.status.in_(["sent", "pending"]),
                    )
                    .limit(10)
                )
                due = result.scalars().all()
                for n in due:
                    n.follow_up_at = None
                    n.status = "pending"
                if due:
                    await db.commit()
                    logger.info("Re-queued %d follow-up notifications", len(due))
        except Exception:
            logger.debug("Follow-up check failed", exc_info=True)

    async def _tick_pending_notifications(self, factory) -> None:
        """Deliver pending notifications that were re-queued by _check_follow_ups."""
        try:
            from src.models.notifications import Notification as NotifModel

            async with factory() as db:
                now = datetime.now(timezone.utc)
                result = await db.execute(
                    select(NotifModel)
                    .where(
                        NotifModel.status == "pending",
                        NotifModel.follow_up_at.is_(None),
                        NotifModel.created_at >= now - timedelta(hours=24),
                    )
                    .limit(10)
                )
                pending = result.scalars().all()
                if not pending or not self._orchestrator:
                    return

                notifier = getattr(self._orchestrator, "_notifier", None)
                if not notifier:
                    return

                for n in pending:
                    try:
                        await notifier.notify(
                            user_id=n.user_id,
                            notification_type=n.channel,
                            title=n.title,
                            body=n.body,
                            data=n.payload_json or {},
                            workspace_id=n.workspace_id or "",
                        )
                        n.status = "sent"
                    except Exception:
                        logger.debug(
                            "Failed to re-deliver notification %s",
                            n.notification_id,
                            exc_info=True,
                        )
                await db.commit()
        except Exception:
            logger.debug("Pending notification tick failed", exc_info=True)
