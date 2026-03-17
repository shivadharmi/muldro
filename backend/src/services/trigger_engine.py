"""Trigger engine — user-configurable reactive triggers on event patterns."""

import logging
from datetime import datetime, timezone

from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession
from ulid import ULID

from src.models.triggers import Trigger
from src.services.event_bus import BusEvent

logger = logging.getLogger(__name__)


class TriggerEngine:
    """Evaluates events against user-defined trigger rules."""

    def __init__(self, db: AsyncSession, event_bus=None, notifier=None):
        self._db = db
        self._event_bus = event_bus
        self._notifier = notifier

    async def create_trigger(
        self,
        user_id: str,
        name: str,
        conditions: dict,
        action_type: str,
        action_config: dict | None = None,
        description: str | None = None,
        workspace_id: str = "",
    ) -> Trigger:
        """Create a new trigger rule."""
        trigger = Trigger(
            trigger_id=f"trig_{ULID()}",
            user_id=user_id,
            workspace_id=workspace_id,
            name=name,
            description=description,
            conditions=conditions,
            action_type=action_type,
            action_config=action_config or {},
            enabled=True,
        )
        self._db.add(trigger)
        await self._db.commit()
        logger.info("Trigger created: %s (%s) for user %s", trigger.trigger_id, name, user_id)
        return trigger

    async def get_triggers(self, user_id: str, workspace_id: str = "") -> list[Trigger]:
        """Get all triggers for a user."""
        result = await self._db.execute(
            select(Trigger).where(
                Trigger.user_id == user_id,
                Trigger.workspace_id == workspace_id,
            ).order_by(Trigger.created_at.desc())
        )
        return list(result.scalars().all())

    async def update_trigger(
        self, trigger_id: str, user_id: str, workspace_id: str = "", **kwargs
    ) -> Trigger | None:
        """Update a trigger."""
        result = await self._db.execute(
            select(Trigger).where(
                Trigger.trigger_id == trigger_id,
                Trigger.user_id == user_id,
                Trigger.workspace_id == workspace_id,
            )
        )
        trigger = result.scalar_one_or_none()
        if not trigger:
            return None

        for key, value in kwargs.items():
            if hasattr(trigger, key):
                setattr(trigger, key, value)

        # Sync status with enabled flag
        if "enabled" in kwargs:
            trigger.status = "active" if kwargs["enabled"] else "disabled"

        await self._db.commit()
        return trigger

    async def delete_trigger(self, trigger_id: str, user_id: str, workspace_id: str = "") -> bool:
        """Delete a trigger."""
        result = await self._db.execute(
            select(Trigger).where(
                Trigger.trigger_id == trigger_id,
                Trigger.user_id == user_id,
                Trigger.workspace_id == workspace_id,
            )
        )
        trigger = result.scalar_one_or_none()
        if not trigger:
            return False
        await self._db.delete(trigger)
        await self._db.commit()
        return True

    async def evaluate(self, event: BusEvent, workspace_id: str = "") -> list[dict]:
        """Evaluate an event against all enabled triggers for the user."""
        result = await self._db.execute(
            select(Trigger).where(
                Trigger.user_id == event.user_id,
                Trigger.workspace_id == workspace_id,
                Trigger.enabled.is_(True),
            )
        )
        triggers = result.scalars().all()

        fired = []
        for trigger in triggers:
            trigger.status = "evaluating"
            trigger.last_evaluated_at = datetime.now(timezone.utc)
            if self._matches(trigger, event):
                await self._fire_trigger(trigger, event)
                fired.append(
                    {
                        "trigger_id": trigger.trigger_id,
                        "name": trigger.name,
                        "action_type": trigger.action_type,
                    }
                )
            else:
                # Emit evaluation event for non-matches too (observability)
                trigger.status = "active"
                if self._event_bus:
                    try:
                        await self._event_bus.publish(
                            self._event_bus.agent_stream(event.user_id),
                            "trigger.evaluated",
                            {
                                "trigger_id": trigger.trigger_id,
                                "matched": False,
                                "event_type": event.event_type,
                            },
                            user_id=event.user_id,
                        )
                    except Exception:
                        logger.debug("Failed to emit trigger.evaluated (no-match)", exc_info=True)

        return fired

    def _matches(self, trigger: Trigger, event: BusEvent) -> bool:
        """Check if an event matches trigger conditions."""
        conditions = trigger.conditions or {}

        # Check cooldown
        cooldown = getattr(trigger, "cooldown_until", None)
        if cooldown and isinstance(cooldown, datetime):
            if datetime.now(timezone.utc) < cooldown:
                return False

        # Match event_type
        if "event_type" in conditions:
            expected = conditions["event_type"]
            if isinstance(expected, list):
                if event.event_type not in expected:
                    return False
            elif event.event_type != expected:
                return False

        # Match source
        if "source" in conditions:
            source = event.payload.get("source", "")
            if source != conditions["source"]:
                return False

        # Match importance threshold
        if "importance_threshold" in conditions:
            importance = event.payload.get("importance_score", 0)
            if importance < conditions["importance_threshold"]:
                return False

        # Match entity pattern
        if "entity_match" in conditions:
            entity_id = event.payload.get("entity_id", "")
            entity_type = event.payload.get("entity_type", "")
            match = conditions["entity_match"]
            if match.get("entity_type") and entity_type != match["entity_type"]:
                return False
            if match.get("entity_id") and entity_id != match["entity_id"]:
                return False

        # Match keyword in payload
        if "keyword_match" in conditions:
            keyword = conditions["keyword_match"]
            payload_str = str(event.payload)
            if keyword.lower() not in payload_str.lower():
                return False

        # Match minimum confidence
        if "min_confidence" in conditions:
            confidence = event.payload.get("confidence_score", 0)
            if confidence < conditions["min_confidence"]:
                return False

        # Match actor entity type
        if "actor_entity_type" in conditions:
            actor_type = event.payload.get("actor_entity_type", "")
            if actor_type != conditions["actor_entity_type"]:
                return False

        return True

    async def _fire_trigger(self, trigger: Trigger, event: BusEvent) -> None:
        """Execute the trigger action."""
        trigger.fire_count += 1
        trigger.last_fired_at = datetime.now(timezone.utc)
        trigger.last_evaluated_at = datetime.now(timezone.utc)
        trigger.status = "triggered"

        # Apply cooldown if configured
        cooldown_seconds = (trigger.conditions or {}).get("cooldown_seconds")
        if cooldown_seconds:
            from datetime import timedelta

            trigger.cooldown_until = datetime.now(timezone.utc) + timedelta(
                seconds=cooldown_seconds
            )

        await self._db.flush()

        action_type = trigger.action_type
        config = trigger.action_config or {}

        if action_type == "notify" and self._notifier:
            await self._notifier.notify(
                user_id=event.user_id,
                notification_type="trigger_fired",
                title=f"Trigger: {trigger.name}",
                body=config.get("message", f"Event matched trigger '{trigger.name}'"),
                data={"trigger_id": trigger.trigger_id, "event_type": event.event_type},
            )
        elif action_type == "plan" and self._event_bus:
            await self._event_bus.publish(
                self._event_bus.agent_stream(event.user_id),
                "trigger_plan_request",
                {
                    "trigger_id": trigger.trigger_id,
                    "trigger_name": trigger.name,
                    "event_type": event.event_type,
                    "payload": event.payload,
                    "action_config": config,
                },
                user_id=event.user_id,
            )

        logger.info(
            "Trigger fired: %s (%s) action=%s",
            trigger.trigger_id,
            trigger.name,
            action_type,
        )

        # Emit domain event
        if self._event_bus:
            try:
                await self._event_bus.publish(
                    self._event_bus.agent_stream(event.user_id),
                    "trigger.evaluated",
                    {
                        "trigger_id": trigger.trigger_id,
                        "action_type": action_type,
                        "event_type": event.event_type,
                    },
                    user_id=event.user_id,
                )
            except Exception:
                logger.debug("Failed to emit trigger.evaluated event", exc_info=True)
