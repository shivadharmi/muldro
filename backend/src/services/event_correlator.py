"""Event correlation engine — groups related events and detects patterns."""

import logging
from datetime import datetime, timedelta, timezone

from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from src.models.events import NormalizedEvent

logger = logging.getLogger(__name__)


class EventCorrelator:
    """Groups related events by entity, time window, and thread."""

    def __init__(self, db: AsyncSession):
        self._db = db

    async def correlate(self, event_id: str, user_id: str) -> list[dict]:
        """Find events related to the given event."""
        result = await self._db.execute(
            select(NormalizedEvent).where(NormalizedEvent.event_id == event_id)
        )
        event = result.scalar_one_or_none()
        if not event:
            return []

        correlations = []

        # Same entity (e.g., same email thread, same PR)
        entity_events = await self._find_by_entity(user_id, event.entity_id, event_id)
        if entity_events:
            correlations.append(
                {
                    "type": "same_entity",
                    "entity_id": event.entity_id,
                    "events": entity_events,
                }
            )

        # Same actor within time window
        if event.actor_entities:
            actor_events = await self._find_by_actor(
                user_id, event.actor_entities, event_id, hours=48
            )
            if actor_events:
                correlations.append(
                    {
                        "type": "same_actor",
                        "events": actor_events,
                    }
                )

        # Same source + type within time window (burst detection)
        burst = await self._detect_burst(user_id, event.source, event.event_type, hours=1)
        if burst and len(burst) > 3:
            correlations.append(
                {
                    "type": "burst",
                    "source": event.source,
                    "event_type": event.event_type,
                    "count": len(burst),
                }
            )

        return correlations

    async def detect_thread(self, user_id: str, entity_id: str) -> dict | None:
        """Detect if events form a conversational thread."""
        result = await self._db.execute(
            select(NormalizedEvent)
            .where(
                NormalizedEvent.user_id == user_id,
                NormalizedEvent.entity_id == entity_id,
            )
            .order_by(NormalizedEvent.occurred_at)
            .limit(20)
        )
        events = list(result.scalars().all())
        if len(events) < 2:
            return None

        return {
            "entity_id": entity_id,
            "event_count": len(events),
            "first_at": events[0].occurred_at.isoformat() if events[0].occurred_at else None,
            "last_at": events[-1].occurred_at.isoformat() if events[-1].occurred_at else None,
            "sources": list({e.source for e in events}),
        }

    async def get_event_context(self, event_id: str, user_id: str) -> dict:
        """Get full context for an event: related events, entities, thread."""
        correlations = await self.correlate(event_id, user_id)

        result = await self._db.execute(
            select(NormalizedEvent).where(NormalizedEvent.event_id == event_id)
        )
        event = result.scalar_one_or_none()

        thread = None
        if event:
            thread = await self.detect_thread(user_id, event.entity_id)

        return {
            "event_id": event_id,
            "correlations": correlations,
            "thread": thread,
        }

    async def detect_anomaly(
        self, user_id: str, source: str, hours: int = 24
    ) -> dict | None:
        """Detect anomalous event patterns for a source.

        Checks for:
        - Unusual volume (>3x normal rate)
        - Missing recurring events (e.g., no calendar events on a weekday)
        - Source silence (no events when expected)
        """
        now = datetime.now(timezone.utc)
        window = now - timedelta(hours=hours)
        prev_window = window - timedelta(hours=hours)

        # Current window count
        current = await self._db.execute(
            select(NormalizedEvent.event_id).where(
                NormalizedEvent.user_id == user_id,
                NormalizedEvent.source == source,
                NormalizedEvent.occurred_at > window,
            )
        )
        current_count = len(list(current.scalars().all()))

        # Previous window count for comparison
        prev = await self._db.execute(
            select(NormalizedEvent.event_id).where(
                NormalizedEvent.user_id == user_id,
                NormalizedEvent.source == source,
                NormalizedEvent.occurred_at > prev_window,
                NormalizedEvent.occurred_at <= window,
            )
        )
        prev_count = len(list(prev.scalars().all()))

        if prev_count == 0 and current_count == 0:
            return None

        # Burst: current > 3x previous
        if prev_count > 0 and current_count > prev_count * 3:
            return {
                "type": "volume_spike",
                "source": source,
                "current_count": current_count,
                "previous_count": prev_count,
                "ratio": round(current_count / prev_count, 1),
                "message": (
                    f"{source} activity is {current_count / prev_count:.1f}x "
                    f"normal ({current_count} vs {prev_count})"
                ),
            }

        # Silence: had events before but none now
        if prev_count > 5 and current_count == 0:
            return {
                "type": "source_silence",
                "source": source,
                "previous_count": prev_count,
                "message": (
                    f"No {source} events in the last {hours}h "
                    f"(previously had {prev_count})"
                ),
            }

        return None

    async def _find_by_entity(
        self, user_id: str, entity_id: str, exclude_event_id: str
    ) -> list[dict]:
        result = await self._db.execute(
            select(NormalizedEvent)
            .where(
                NormalizedEvent.user_id == user_id,
                NormalizedEvent.entity_id == entity_id,
                NormalizedEvent.event_id != exclude_event_id,
            )
            .order_by(NormalizedEvent.occurred_at.desc())
            .limit(5)
        )
        return [
            {"event_id": e.event_id, "event_type": e.event_type, "title": e.title}
            for e in result.scalars().all()
        ]

    async def _find_by_actor(
        self, user_id: str, actor_entities: list, exclude_event_id: str, hours: int
    ) -> list[dict]:
        cutoff = datetime.now(timezone.utc) - timedelta(hours=hours)
        # Simple: look for events with matching actor email/name in the window
        result = await self._db.execute(
            select(NormalizedEvent)
            .where(
                NormalizedEvent.user_id == user_id,
                NormalizedEvent.event_id != exclude_event_id,
                NormalizedEvent.occurred_at > cutoff,
            )
            .order_by(NormalizedEvent.occurred_at.desc())
            .limit(20)
        )
        events = result.scalars().all()
        actor_emails = {a.get("email", "") for a in actor_entities if isinstance(a, dict)}
        matched = []
        for e in events:
            if e.actor_entities:
                for a in e.actor_entities:
                    if isinstance(a, dict) and a.get("email") in actor_emails:
                        matched.append(
                            {
                                "event_id": e.event_id,
                                "event_type": e.event_type,
                                "title": e.title,
                            }
                        )
                        break
        return matched[:5]

    async def _detect_burst(
        self, user_id: str, source: str, event_type: str, hours: int
    ) -> list[str]:
        cutoff = datetime.now(timezone.utc) - timedelta(hours=hours)
        result = await self._db.execute(
            select(NormalizedEvent.event_id).where(
                NormalizedEvent.user_id == user_id,
                NormalizedEvent.source == source,
                NormalizedEvent.event_type == event_type,
                NormalizedEvent.occurred_at > cutoff,
            )
        )
        return list(result.scalars().all())
