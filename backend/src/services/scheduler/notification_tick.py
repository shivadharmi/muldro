"""Follow-up notification re-queue and pending-notification delivery."""

import logging
from datetime import datetime, timedelta, timezone

from sqlalchemy import select

logger = logging.getLogger(__name__)

# How long an undelivered notification stays worth retrying. Deliberately LONGER than
# the 24h default approval deadline: a retry window shorter than the thing it is trying
# to deliver would stop chasing an approval while that approval was still answerable.
_GIVE_UP_AFTER = timedelta(days=3)

# Per-tick batch. The tick runs on the shared scheduler loop, so this bounds one pass
# rather than the backlog — a large backlog drains over successive ticks.
_RETRY_BATCH = 25


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
        """Re-deliver notifications that never reached the user.

        This is the ONLY thing standing between an offline founder and a silently
        lost approval request: ``Notifier.notify`` returns ``queued`` when no
        surface is active, and a queued approval that is never retried expires
        unanswered — cancelling the run that was waiting on it.

        Three rules, each of which was previously broken:

        * **Re-deliver, never re-create.** ``deliver_existing`` pushes the SAME
          row; ``notify()`` would insert a fresh one every tick.
        * **Mark sent only on real delivery.** A ``queued``/``failed`` attempt
          leaves the row ``pending`` so the next tick tries again. Recording an
          undelivered notification as sent retires it permanently.
        * **Give up explicitly.** Past the retry window the row becomes
          ``expired`` rather than dropping out of the query still ``pending``,
          which left rows nothing would ever look at again.
        """
        try:
            from src.models.notifications import Notification as NotifModel

            notifier = (
                getattr(self._orchestrator, "_notifier", None) if self._orchestrator else None
            )
            if not notifier:
                return

            async with factory() as db:
                now = datetime.now(timezone.utc)
                result = await db.execute(
                    select(NotifModel)
                    .where(
                        NotifModel.status == "pending",
                        NotifModel.follow_up_at.is_(None),
                        NotifModel.created_at >= now - _GIVE_UP_AFTER,
                    )
                    .limit(_RETRY_BATCH)
                )
                pending = list(result.scalars().all())
                if not pending:
                    return

                delivered = retried = abandoned = 0
                for n in pending:
                    # Belt and braces: the query bounds the window, but a row whose
                    # created_at is stale relative to THIS tick must still terminate
                    # rather than be retried forever. ``isinstance`` guards the case
                    # where created_at is absent — an unknown age must not be read as
                    # "too old" and silently discard an approval the user still owes
                    # an answer to.
                    if isinstance(n.created_at, datetime) and now - n.created_at >= _GIVE_UP_AFTER:
                        n.status = "expired"
                        abandoned += 1
                        continue
                    try:
                        outcome = await notifier.deliver_existing(n)
                    except Exception:
                        logger.debug(
                            "Failed to re-deliver notification %s",
                            n.notification_id,
                            exc_info=True,
                        )
                        retried += 1
                        continue
                    if (outcome or {}).get("status") == "sent":
                        n.status = "sent"
                        n.sent_at = now
                        delivered += 1
                    else:
                        # Left ``pending`` on purpose — still owed to the user.
                        retried += 1

                await db.commit()
                if delivered or abandoned:
                    logger.info(
                        "Notification retry: %d delivered, %d still pending, %d given up",
                        delivered,
                        retried,
                        abandoned,
                    )
        except Exception:
            logger.debug("Pending notification tick failed", exc_info=True)
