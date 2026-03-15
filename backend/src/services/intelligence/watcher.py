"""WatcherService — monitors patterns and generates proactive insights.

Watchers monitor for:
- Stale email threads needing follow-up (48+ hours)
- Approaching deadlines (next 24 hours)
- Interaction frequency drops with important contacts (people pulse)
- Project activity changes (project pulse)
"""

import logging
from datetime import datetime, timedelta, timezone

from sqlalchemy import func, select
from sqlalchemy.ext.asyncio import AsyncSession

from src.models.entities import Entity
from src.models.events import NormalizedEvent

logger = logging.getLogger(__name__)


class WatcherService:
    """Monitors patterns and generates proactive insights."""

    def __init__(self, db: AsyncSession):
        self._db = db

    async def run_watchers(self, user_id: str) -> list[dict]:
        """Run all watcher checks. Returns list of insights."""
        insights = []
        insights.extend(await self._check_stale_threads(user_id))
        insights.extend(await self._check_approaching_deadlines(user_id))
        insights.extend(await self._check_people_pulse(user_id))
        insights.extend(await self._check_project_pulse(user_id))
        return insights

    async def _check_stale_threads(self, user_id: str) -> list[dict]:
        """Find email threads with no activity in 48+ hours where user was involved."""
        cutoff = datetime.now(timezone.utc) - timedelta(hours=48)

        stmt = (
            select(NormalizedEvent)
            .where(
                NormalizedEvent.user_id == user_id,
                NormalizedEvent.source == "gmail",
                NormalizedEvent.event_type.in_(["email_received", "email_sent"]),
                NormalizedEvent.occurred_at < cutoff,
                NormalizedEvent.status == "processed",
            )
            .order_by(NormalizedEvent.occurred_at.desc())
            .limit(10)
        )

        result = await self._db.execute(stmt)
        events = result.scalars().all()

        # Group by entity_id (thread) and find threads with no recent activity
        thread_map: dict[str, NormalizedEvent] = {}
        for event in events:
            if event.entity_id not in thread_map:
                thread_map[event.entity_id] = event

        insights = []
        for entity_id, latest_event in thread_map.items():
            # Check if there's any activity after this event for this thread
            recent_stmt = select(NormalizedEvent).where(
                NormalizedEvent.user_id == user_id,
                NormalizedEvent.entity_id == entity_id,
                NormalizedEvent.occurred_at > latest_event.occurred_at,
            )
            recent_result = await self._db.execute(recent_stmt)
            has_recent = recent_result.scalar_one_or_none() is not None

            if (
                not has_recent
                and latest_event.importance_score
                and (latest_event.importance_score > 0.5)
            ):
                # Extract entity names from actor_entities
                entities = []
                if latest_event.actor_entities:
                    for actor in latest_event.actor_entities.get("actors", []):
                        if actor.get("name"):
                            entities.append(actor["name"])

                insights.append(
                    {
                        "type": "stale_thread",
                        "title": f"Stale thread: {latest_event.title or 'Untitled'}",
                        "description": (
                            f"No activity since "
                            f"{latest_event.occurred_at.strftime('%Y-%m-%d %H:%M')}"
                        ),
                        "entities": entities,
                        "priority": "high" if latest_event.importance_score > 0.7 else "medium",
                    }
                )

        return insights[:5]

    async def _check_approaching_deadlines(self, user_id: str) -> list[dict]:
        """Look at calendar events in the next 24 hours that might need prep."""
        now = datetime.now(timezone.utc)
        tomorrow = now + timedelta(hours=24)

        stmt = (
            select(NormalizedEvent)
            .where(
                NormalizedEvent.user_id == user_id,
                NormalizedEvent.source == "calendar",
                NormalizedEvent.event_type.in_(["event_created", "event_upcoming"]),
                NormalizedEvent.occurred_at.between(now, tomorrow),
                NormalizedEvent.status == "processed",
            )
            .order_by(NormalizedEvent.occurred_at)
            .limit(5)
        )

        result = await self._db.execute(stmt)
        events = result.scalars().all()

        insights = []
        for event in events:
            entities = []
            if event.actor_entities:
                for actor in event.actor_entities.get("actors", []):
                    if actor.get("name"):
                        entities.append(actor["name"])

            time_until = event.occurred_at - now
            hours_until = int(time_until.total_seconds() / 3600)

            insights.append(
                {
                    "type": "deadline",
                    "title": f"Upcoming: {event.title or 'Untitled event'}",
                    "description": f"In {hours_until} hours",
                    "entities": entities,
                    "priority": "high" if hours_until < 2 else "medium",
                }
            )

        return insights

    async def _check_people_pulse(self, user_id: str) -> list[dict]:
        """Detect interaction frequency drops with important contacts."""
        # Get entities of type 'person' with high importance and recent activity
        stmt = (
            select(Entity)
            .where(
                Entity.user_id == user_id,
                Entity.entity_type == "person",
                Entity.importance_score > 0.6,
                Entity.interaction_count > 5,
            )
            .order_by(Entity.importance_score.desc())
            .limit(20)
        )

        result = await self._db.execute(stmt)
        entities = result.scalars().all()

        insights = []
        now = datetime.now(timezone.utc)
        week_ago = now - timedelta(days=7)
        two_weeks_ago = now - timedelta(days=14)

        for entity in entities:
            # Count interactions in last week
            last_week_stmt = select(func.count(NormalizedEvent.event_id)).where(
                NormalizedEvent.user_id == user_id,
                NormalizedEvent.occurred_at.between(week_ago, now),
                NormalizedEvent.actor_entities.isnot(None),
            )
            last_week_result = await self._db.execute(last_week_stmt)
            last_week_count = last_week_result.scalar_one() or 0

            # Count interactions in previous week
            prev_week_stmt = select(func.count(NormalizedEvent.event_id)).where(
                NormalizedEvent.user_id == user_id,
                NormalizedEvent.occurred_at.between(two_weeks_ago, week_ago),
                NormalizedEvent.actor_entities.isnot(None),
            )
            prev_week_result = await self._db.execute(prev_week_stmt)
            prev_week_count = prev_week_result.scalar_one() or 0

            # Flag if drop > 50% and previous week had activity
            if prev_week_count > 2 and last_week_count < prev_week_count * 0.5:
                insights.append(
                    {
                        "type": "people_pulse",
                        "title": f"Reduced contact with {entity.canonical_name}",
                        "description": (
                            f"Interactions dropped from {prev_week_count} "
                            f"to {last_week_count} this week"
                        ),
                        "entities": [entity.canonical_name],
                        "priority": "medium",
                    }
                )

        return insights[:3]

    async def _check_project_pulse(self, user_id: str) -> list[dict]:
        """Aggregate recent activity per project entity and flag activity changes."""
        # Get project entities
        stmt = (
            select(Entity)
            .where(
                Entity.user_id == user_id,
                Entity.entity_type == "project",
                Entity.interaction_count > 3,
            )
            .order_by(Entity.last_seen_at.desc())
            .limit(10)
        )

        result = await self._db.execute(stmt)
        entities = result.scalars().all()

        insights = []
        now = datetime.now(timezone.utc)
        last_24h = now - timedelta(hours=24)
        prev_24h = now - timedelta(hours=48)

        for entity in entities:
            # Count events mentioning this project in last 24h
            last_24h_stmt = select(func.count(NormalizedEvent.event_id)).where(
                NormalizedEvent.user_id == user_id,
                NormalizedEvent.occurred_at.between(last_24h, now),
                NormalizedEvent.title.ilike(f"%{entity.canonical_name}%"),
            )
            last_24h_result = await self._db.execute(last_24h_stmt)
            last_24h_count = last_24h_result.scalar_one() or 0

            # Count events in previous 24h
            prev_24h_stmt = select(func.count(NormalizedEvent.event_id)).where(
                NormalizedEvent.user_id == user_id,
                NormalizedEvent.occurred_at.between(prev_24h, last_24h),
                NormalizedEvent.title.ilike(f"%{entity.canonical_name}%"),
            )
            prev_24h_result = await self._db.execute(prev_24h_stmt)
            prev_24h_count = prev_24h_result.scalar_one() or 0

            # Flag if sudden spike (3x increase) or drop (to zero)
            if prev_24h_count > 0 and last_24h_count > prev_24h_count * 3:
                insights.append(
                    {
                        "type": "project_pulse",
                        "title": f"Spike in {entity.canonical_name} activity",
                        "description": (
                            f"Activity increased from {prev_24h_count} to {last_24h_count} events"
                        ),
                        "entities": [entity.canonical_name],
                        "priority": "medium",
                    }
                )
            elif prev_24h_count > 2 and last_24h_count == 0:
                insights.append(
                    {
                        "type": "project_pulse",
                        "title": f"{entity.canonical_name} activity dropped",
                        "description": "No events in the last 24 hours",
                        "entities": [entity.canonical_name],
                        "priority": "low",
                    }
                )

        return insights[:3]
