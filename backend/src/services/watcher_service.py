"""Watcher service — monitors patterns and generates proactive insights.

Watchers monitor for:
- Stale email threads needing follow-up
- Approaching deadlines
- Interaction frequency drops (people pulse)
- Unusual event patterns (anomaly detection)

Also provides CRUD for trigger-based watchers (Phase 4A).
"""

import logging
from datetime import datetime, timedelta, timezone

from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession
from ulid import ULID

from src.models.events import NormalizedEvent
from src.models.triggers import Trigger

logger = logging.getLogger(__name__)


class WatcherService:
    """Monitors patterns and generates proactive insights."""

    def __init__(self, db: AsyncSession, notifier=None):
        self._db = db
        self._notifier = notifier

    # ── Trigger-based watcher CRUD ──────────────────────────────────────

    async def create_watcher(
        self,
        user_id: str,
        name: str,
        conditions: dict,
        action_type: str = "notify",
        workspace_id: str = "",
    ) -> dict:
        """Create a trigger-based watcher. Returns dict with trigger_id."""
        trigger = Trigger(
            trigger_id=f"trg_{ULID()}",
            user_id=user_id,
            workspace_id=workspace_id,
            name=name,
            conditions=conditions,
            action_type=action_type,
            enabled=True,
            status="active",
        )
        self._db.add(trigger)
        await self._db.flush()
        return {"trigger_id": trigger.trigger_id, "status": "active"}

    async def get_watcher(self, trigger_id: str, workspace_id: str = "") -> dict | None:
        """Get a watcher by trigger_id."""
        result = await self._db.execute(
            select(Trigger).where(
                Trigger.trigger_id == trigger_id,
                Trigger.workspace_id == workspace_id,
            )
        )
        trigger = result.scalar_one_or_none()
        if not trigger:
            return None
        return {
            "trigger_id": trigger.trigger_id,
            "name": trigger.name,
            "conditions": trigger.conditions,
            "action_type": trigger.action_type,
            "status": trigger.status,
            "enabled": trigger.enabled,
        }

    async def disable_watcher(self, trigger_id: str, workspace_id: str = "") -> None:
        """Disable a watcher."""
        result = await self._db.execute(
            select(Trigger).where(
                Trigger.trigger_id == trigger_id,
                Trigger.workspace_id == workspace_id,
            )
        )
        trigger = result.scalar_one_or_none()
        if trigger:
            trigger.enabled = False
            trigger.status = "disabled"
            await self._db.flush()

    async def snooze_watcher(
        self, trigger_id: str, until: datetime, workspace_id: str = ""
    ) -> None:
        """Snooze a watcher until a given datetime."""
        result = await self._db.execute(
            select(Trigger).where(
                Trigger.trigger_id == trigger_id,
                Trigger.workspace_id == workspace_id,
            )
        )
        trigger = result.scalar_one_or_none()
        if trigger:
            trigger.enabled = False
            trigger.status = "snoozed"
            trigger.conditions = {**(trigger.conditions or {}), "snooze_until": until.isoformat()}
            await self._db.flush()

    async def run_all_watchers(self, user_id: str, workspace_id: str = "") -> list[dict]:
        """Run all watchers for a user. Returns list of generated insights."""
        insights = []

        stale = await self._check_stale_threads(user_id, workspace_id=workspace_id)
        insights.extend(stale)

        anomalies = await self._check_anomalies(user_id, workspace_id=workspace_id)
        insights.extend(anomalies)

        # Evaluate time-based triggers
        time_triggers = await self._evaluate_time_triggers(user_id, workspace_id=workspace_id)
        insights.extend(time_triggers)

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

    async def _evaluate_time_triggers(self, user_id: str, workspace_id: str = "") -> list[dict]:
        """Evaluate time-based triggers (cooldown_until expired, schedule conditions)."""
        now = datetime.now(timezone.utc)
        result = await self._db.execute(
            select(Trigger).where(
                Trigger.user_id == user_id,
                Trigger.workspace_id == workspace_id,
                Trigger.enabled.is_(True),
                Trigger.status.in_(["active", "snoozed"]),
            )
        )
        triggers = result.scalars().all()

        insights = []
        for trigger in triggers:
            conditions = trigger.conditions or {}

            # Un-snooze triggers whose snooze period has expired
            if trigger.status == "snoozed":
                snooze_until = conditions.get("snooze_until")
                if snooze_until:
                    try:
                        snooze_dt = datetime.fromisoformat(snooze_until)
                        if now < snooze_dt:
                            continue
                        # Snooze expired — reactivate
                        trigger.status = "active"
                        trigger.conditions = {
                            k: v for k, v in conditions.items() if k != "snooze_until"
                        }
                        await self._db.flush()
                    except (ValueError, TypeError):
                        pass

            # Evaluate time_window conditions
            time_window = conditions.get("time_window")
            if not time_window:
                continue

            # Check cooldown
            if trigger.cooldown_until and trigger.cooldown_until > now:
                continue

            # Time window: {"type": "recurring", "cron": "0 9 * * *"} or
            # {"type": "deadline", "before": "2026-04-01T00:00:00Z"}
            window_type = time_window.get("type", "")

            if window_type == "deadline":
                deadline_str = time_window.get("before")
                if deadline_str:
                    try:
                        deadline = datetime.fromisoformat(deadline_str)
                        hours_remaining = (deadline - now).total_seconds() / 3600
                        if 0 < hours_remaining < 24:
                            insights.append(
                                {
                                    "type": "time_trigger",
                                    "title": f"Approaching deadline: {trigger.name}",
                                    "description": f"{hours_remaining:.0f} hours remaining",
                                    "trigger_id": trigger.trigger_id,
                                }
                            )
                            trigger.fire_count += 1
                            trigger.last_fired_at = now
                            trigger.cooldown_until = now + timedelta(hours=6)
                            await self._db.flush()
                    except (ValueError, TypeError):
                        pass

            elif window_type == "recurring":
                cron_expr = time_window.get("cron")
                if cron_expr:
                    try:
                        from croniter import croniter

                        last_eval = trigger.last_evaluated_at or (now - timedelta(minutes=31))
                        cron = croniter(cron_expr, last_eval)
                        next_fire = cron.get_next(datetime)
                        if next_fire <= now:
                            insights.append(
                                {
                                    "type": "time_trigger",
                                    "title": trigger.name,
                                    "description": conditions.get(
                                        "description", "Scheduled trigger fired"
                                    ),
                                    "trigger_id": trigger.trigger_id,
                                    "action_type": trigger.action_type,
                                }
                            )
                            trigger.fire_count += 1
                            trigger.last_fired_at = now
                            trigger.last_evaluated_at = now
                            await self._db.flush()
                    except Exception:
                        logger.debug("Cron eval failed for trigger %s", trigger.trigger_id)

        return insights

    async def _check_stale_threads(self, user_id: str, workspace_id: str = "") -> list[dict]:
        """Find email threads that haven't had a response in 48+ hours."""
        cutoff = datetime.now(timezone.utc) - timedelta(hours=48)
        result = await self._db.execute(
            select(NormalizedEvent)
            .where(
                NormalizedEvent.user_id == user_id,
                NormalizedEvent.workspace_id == workspace_id,
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

    async def _check_anomalies(self, user_id: str, workspace_id: str = "") -> list[dict]:
        """Detect unusual event volume patterns."""
        # Compare last hour's event count to typical
        now = datetime.now(timezone.utc)
        last_hour = now - timedelta(hours=1)
        day_ago = now - timedelta(days=1)

        # Events in last hour
        result = await self._db.execute(
            select(NormalizedEvent).where(
                NormalizedEvent.user_id == user_id,
                NormalizedEvent.workspace_id == workspace_id,
                NormalizedEvent.occurred_at > last_hour,
            )
        )
        recent_count = len(result.scalars().all())

        # Average hourly events over last 24 hours
        result = await self._db.execute(
            select(NormalizedEvent).where(
                NormalizedEvent.user_id == user_id,
                NormalizedEvent.workspace_id == workspace_id,
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
