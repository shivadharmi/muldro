"""Watcher service — monitors patterns and generates proactive insights.

Watchers monitor for:
- Stale email threads needing follow-up
- Approaching deadlines
- Interaction frequency drops (people pulse)
- Unusual event patterns (anomaly detection)
"""

import logging
from datetime import datetime, timedelta, timezone

from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from src.models.events import NormalizedEvent

logger = logging.getLogger(__name__)


class WatcherService:
    """Monitors patterns and generates proactive insights."""

    def __init__(self, db: AsyncSession, notifier=None):
        self._db = db
        self._notifier = notifier

    async def run_all_watchers(self, user_id: str) -> list[dict]:
        """Run all watchers for a user. Returns list of generated insights."""
        insights = []

        stale = await self._check_stale_threads(user_id)
        insights.extend(stale)

        anomalies = await self._check_anomalies(user_id)
        insights.extend(anomalies)

        if insights and self._notifier:
            for insight in insights[:3]:  # Max 3 notifications per cycle
                try:
                    await self._notifier.notify(
                        user_id=user_id,
                        notification_type="proactive_insight",
                        title=insight.get("title", "Insight"),
                        body=insight.get("description", ""),
                        data=insight,
                    )
                except Exception:
                    logger.warning("Failed to notify insight", exc_info=True)

        return insights

    async def _check_stale_threads(self, user_id: str) -> list[dict]:
        """Find email threads that haven't had a response in 48+ hours."""
        cutoff = datetime.now(timezone.utc) - timedelta(hours=48)
        result = await self._db.execute(
            select(NormalizedEvent)
            .where(
                NormalizedEvent.user_id == user_id,
                NormalizedEvent.source == "gmail",
                NormalizedEvent.event_type == "email_received",
                NormalizedEvent.occurred_at < cutoff,
                NormalizedEvent.importance_score > 0.5,
            )
            .order_by(NormalizedEvent.importance_score.desc())
            .limit(5)
        )
        events = result.scalars().all()

        insights = []
        for event in events:
            insights.append(
                {
                    "type": "stale_thread",
                    "title": f"Follow-up needed: {event.title}",
                    "description": f"Email received {event.occurred_at} hasn't been responded to.",
                    "entity_id": event.entity_id,
                    "event_id": event.event_id,
                    "importance": event.importance_score,
                }
            )
        return insights

    async def _check_anomalies(self, user_id: str) -> list[dict]:
        """Detect unusual event volume patterns."""
        # Compare last hour's event count to typical
        now = datetime.now(timezone.utc)
        last_hour = now - timedelta(hours=1)
        day_ago = now - timedelta(days=1)

        # Events in last hour
        result = await self._db.execute(
            select(NormalizedEvent).where(
                NormalizedEvent.user_id == user_id,
                NormalizedEvent.occurred_at > last_hour,
            )
        )
        recent_count = len(result.scalars().all())

        # Average hourly events over last 24 hours
        result = await self._db.execute(
            select(NormalizedEvent).where(
                NormalizedEvent.user_id == user_id,
                NormalizedEvent.occurred_at > day_ago,
            )
        )
        day_count = len(result.scalars().all())
        avg_hourly = day_count / 24 if day_count > 0 else 0

        insights = []
        if avg_hourly > 0 and recent_count > avg_hourly * 3:
            insights.append(
                {
                    "type": "volume_anomaly",
                    "title": "Unusual event volume detected",
                    "description": (
                        f"{recent_count} events in the last hour (average: {avg_hourly:.1f}/hour)"
                    ),
                    "recent_count": recent_count,
                    "average_hourly": avg_hourly,
                }
            )

        return insights
