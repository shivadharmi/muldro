"""Event Processor — normalize, score, and deduplicate incoming events.

Responsibilities:
- Receive raw events from connectors
- Normalize to NormalizedEvent schema
- Score importance/urgency/confidence via Claude (context-aware)
- Deduplicate by idempotency key
- Store and trigger downstream processing (entity extraction, planning)
"""

from __future__ import annotations

import json
import logging
from dataclasses import dataclass
from datetime import datetime, timezone
from typing import TYPE_CHECKING

from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession
from ulid import ULID

from src.config.settings import Settings, get_anthropic_client
from src.models.events import NormalizedEvent

if TYPE_CHECKING:
    from src.services.dead_letter import DeadLetterService
    from src.services.event_bus import EventBus
    from src.services.goal_tracker import GoalTracker
    from src.services.memory_service import MemoryService
    from src.services.notifier import Notifier
    from src.services.planner import Planner
    from src.services.world_model import WorldModel

logger = logging.getLogger(__name__)


@dataclass
class RawEvent:
    source: str
    source_account_id: str
    event_type: str
    entity_type: str
    entity_id: str
    occurred_at: datetime | None = None
    title: str | None = None
    summary: str | None = None
    actor: dict | None = None
    raw_payload: dict | None = None
    correlation_id: str | None = None
    causation_id: str | None = None


SCORING_SYSTEM_PROMPT = """\
You are Jarvis's event scoring engine. Given an event and optional user context, \
evaluate its importance and urgency for this specific user.

You MUST respond with valid JSON matching this schema:
{
  "importance_score": float 0.0-1.0,
  "urgency_score": float 0.0-1.0,
  "confidence_score": float 0.0-1.0,
  "importance_signals": {
    "from_priority_person": bool,
    "contains_deadline": bool,
    "contains_question": bool,
    "related_to_active_project": bool
  },
  "summary": "1-sentence summary if the input lacks a clear summary, else repeat it"
}

Scoring guidelines:
- importance_score: How much does this matter to the user's goals? \
(investor emails=high, newsletters=low, team updates=medium)
- urgency_score: How time-sensitive? (deadline today=high, FYI=low)
- confidence_score: How confident are you in the scoring? \
(clear context=high, ambiguous=low)
- from_priority_person: investors, co-founders, direct reports, key partners
- contains_deadline: explicit dates, "by EOD", "ASAP", "urgent"
- contains_question: direct questions requiring response
- related_to_active_project: references to known projects or ongoing work

If user context is provided (known entities, preferences, active projects), \
use it to calibrate scores. A message from a known investor should score higher \
than one from an unknown sender.
"""

DEFAULT_SCORES = {
    "importance_score": 0.5,
    "urgency_score": 0.3,
    "confidence_score": 0.3,
    "importance_signals": {
        "from_priority_person": False,
        "contains_deadline": False,
        "contains_question": False,
        "related_to_active_project": False,
    },
}


class EventProcessor:
    """Process raw events into normalized, scored events."""

    def __init__(
        self,
        settings: Settings,
        db: AsyncSession,
        on_event_processed: list | None = None,
        world_model: WorldModel | None = None,
        memory_service: MemoryService | None = None,
        dead_letter: DeadLetterService | None = None,
        event_bus: EventBus | None = None,
        notifier: Notifier | None = None,
        planner: Planner | None = None,
        goal_tracker: GoalTracker | None = None,
    ):
        self._settings = settings
        self._db = db
        self._client = get_anthropic_client(settings)
        # Optional async callbacks: called with (event_id, user_id) after processing
        self._on_event_processed = on_event_processed or []
        # Optional context providers for enriched scoring
        self._world_model = world_model
        self._memory_service = memory_service
        self._dead_letter = dead_letter
        self._event_bus = event_bus
        self._notifier = notifier
        self._planner = planner
        self._goal_tracker = goal_tracker

    async def process(self, raw: RawEvent, user_id: str, workspace_id: str = "") -> str | None:
        """Process a raw event. Returns event_id if stored, None if duplicate."""
        idempotency_key = f"{raw.source}:{raw.entity_id}:{raw.event_type}"

        existing = await self._db.execute(
            select(NormalizedEvent.event_id).where(
                NormalizedEvent.idempotency_key == idempotency_key
            )
        )
        if existing.scalar_one_or_none():
            logger.debug("Duplicate event skipped: %s", idempotency_key)
            return None

        scores = await self._score_event(raw, user_id)

        event_id = f"evt_{ULID()}"
        event = NormalizedEvent(
            event_id=event_id,
            user_id=user_id,
            workspace_id=workspace_id,
            source=raw.source,
            source_account_id=raw.source_account_id,
            event_type=raw.event_type,
            entity_type=raw.entity_type,
            entity_id=raw.entity_id,
            occurred_at=raw.occurred_at or datetime.now(timezone.utc),
            title=raw.title,
            summary=scores.get("summary") or raw.summary,
            actor_entities=[raw.actor] if raw.actor else None,
            importance_signals=scores.get("importance_signals"),
            urgency_score=scores.get("urgency_score"),
            importance_score=scores.get("importance_score"),
            confidence_score=scores.get("confidence_score"),
            correlation_id=raw.correlation_id,
            causation_id=raw.causation_id,
            idempotency_key=idempotency_key,
            status="processed",
        )

        self._db.add(event)
        await self._db.commit()

        logger.info(
            "Event processed: %s importance=%.2f urgency=%.2f",
            event_id,
            event.importance_score or 0,
            event.urgency_score or 0,
        )

        # Record Prometheus metrics
        try:
            from src.services.metrics_service import MetricsService

            MetricsService.record_event_ingested(raw.source, raw.event_type)
        except Exception:
            pass

        # Publish to event bus for decoupled downstream processing
        if self._event_bus:
            try:
                await self._event_bus.publish(
                    self._event_bus.event_stream(user_id),
                    "event_processed",
                    {
                        "event_id": event_id,
                        "source": raw.source,
                        "event_type": raw.event_type,
                        "importance_score": event.importance_score or 0,
                        "urgency_score": event.urgency_score or 0,
                    },
                    user_id=user_id,
                )
            except Exception:
                logger.warning("Failed to publish to event bus", exc_info=True)

        # Evaluate triggers against this event
        await self._evaluate_triggers(event, user_id, workspace_id=workspace_id)

        # Initiative scoring — decide if Jarvis should proactively act
        await self._evaluate_initiative(event, user_id, workspace_id=workspace_id)

        # Fire legacy callbacks (kept for backward compatibility)
        for callback in self._on_event_processed:
            try:
                await callback(event_id, user_id)
            except Exception as exc:
                logger.warning(
                    "Post-process callback failed for %s: %s",
                    event_id,
                    str(exc)[:200],
                    exc_info=True,
                )
                if hasattr(self, "_dead_letter") and self._dead_letter:
                    await self._dead_letter.enqueue(
                        user_id=user_id,
                        operation_type=f"callback:{callback.__name__}",
                        error_type=type(exc).__name__,
                        error_message=str(exc),
                        source_id=event_id,
                        payload={"event_id": event_id, "callback": callback.__name__},
                    )

        return event_id

    async def _evaluate_triggers(
        self, event: NormalizedEvent, user_id: str, workspace_id: str = ""
    ) -> None:
        """Evaluate active triggers against a new event. Fire matching ones."""
        try:
            from src.models.triggers import Trigger

            now = datetime.now(timezone.utc)
            result = await self._db.execute(
                select(Trigger).where(
                    Trigger.user_id == user_id,
                    Trigger.enabled.is_(True),
                )
            )
            triggers = list(result.scalars().all())

            for trigger in triggers:
                # Skip if in cooldown
                if trigger.cooldown_until and trigger.cooldown_until > now:
                    continue

                if self._trigger_matches(trigger, event):
                    trigger.fire_count += 1
                    trigger.last_fired_at = now
                    trigger.last_evaluated_at = now

                    # Apply cooldown from action_config (default 5 min)
                    from datetime import timedelta

                    cooldown_secs = (trigger.action_config or {}).get("cooldown_seconds", 300)
                    trigger.cooldown_until = now + timedelta(seconds=cooldown_secs)

                    logger.info(
                        "Trigger fired: %s for event %s",
                        trigger.trigger_id,
                        event.event_id,
                    )

                    # Execute the trigger's action
                    try:
                        await self._execute_trigger_action(
                            trigger, event, user_id, workspace_id=workspace_id
                        )
                    except Exception:
                        logger.warning(
                            "Trigger action execution failed: %s",
                            trigger.trigger_id,
                            exc_info=True,
                        )

                    # Publish trigger fired event
                    if self._event_bus:
                        await self._event_bus.publish(
                            self._event_bus.event_stream(user_id),
                            "trigger.fired",
                            {
                                "trigger_id": trigger.trigger_id,
                                "trigger_name": trigger.name,
                                "event_id": event.event_id,
                                "action_type": trigger.action_type,
                                "action_config": trigger.action_config,
                            },
                            user_id=user_id,
                        )
                else:
                    trigger.last_evaluated_at = now

            await self._db.flush()
        except Exception:
            logger.debug("Trigger evaluation failed", exc_info=True)

    async def _execute_trigger_action(
        self, trigger, event: NormalizedEvent, user_id: str, workspace_id: str = ""
    ) -> None:
        """Execute the action associated with a fired trigger."""
        action_type = trigger.action_type
        action_config = trigger.action_config or {}

        if action_type == "notify" and self._notifier:
            await self._notifier.notify(
                user_id=user_id,
                notification_type="info_update",
                title=f"Trigger: {trigger.name}",
                body=action_config.get(
                    "message",
                    f"Trigger '{trigger.name}' fired for event: {event.title or event.event_type}",
                ),
                data={
                    "trigger_id": trigger.trigger_id,
                    "event_id": event.event_id,
                    "urgency": event.urgency_score or 0.5,
                },
            )
        elif action_type == "plan" and self._planner:
            context = (
                f"Triggered by: {trigger.name}\n"
                f"Event: {event.title or event.event_type}\n"
                f"Source: {event.source}\n"
                f"Summary: {event.summary or 'N/A'}"
            )
            instructions = action_config.get(
                "instructions",
                f"Handle event: {event.title or event.summary or event.event_type}",
            )
            await self._planner.plan_for_command(
                instructions, user_id, context=context, workspace_id=workspace_id
            )
        elif action_type == "escalate" and self._notifier:
            await self._notifier.notify(
                user_id=user_id,
                notification_type="critical_alert",
                title=f"ESCALATION: {trigger.name}",
                body=action_config.get(
                    "message",
                    f"Trigger '{trigger.name}' escalated: {event.title or event.event_type}",
                ),
                data={
                    "trigger_id": trigger.trigger_id,
                    "event_id": event.event_id,
                    "urgency": 1.0,
                },
            )
        else:
            logger.debug(
                "Trigger action '%s' not executed (missing service)",
                action_type,
            )

    async def _evaluate_initiative(
        self, event: NormalizedEvent, user_id: str, workspace_id: str = ""
    ) -> None:
        """Score event for proactive action. Auto-plan or notify if warranted."""
        try:
            from src.services.initiative_scorer import InitiativeScorer

            scorer = InitiativeScorer(
                db=self._db,
                world_model=self._world_model,
                memory_service=self._memory_service,
                goal_tracker=self._goal_tracker,
            )
            result = await scorer.score(event, user_id)

            if result.should_plan and self._planner:
                logger.info(
                    "Auto-planning for event %s (score=%.3f)",
                    event.event_id,
                    result.score,
                )
                await self._planner.plan_for_event(
                    event.event_id, user_id, workspace_id=workspace_id
                )

                if self._event_bus:
                    await self._event_bus.publish(
                        self._event_bus.event_stream(user_id),
                        "initiative.auto_plan",
                        {
                            "event_id": event.event_id,
                            "score": result.score,
                            "signals": result.signals,
                        },
                        user_id=user_id,
                    )

            elif result.should_notify and self._notifier:
                await self._notifier.notify(
                    user_id=user_id,
                    notification_type="info_update",
                    title=event.title or f"New {event.event_type}",
                    body=event.summary or f"From {event.source}",
                    data={
                        "event_id": event.event_id,
                        "urgency": event.urgency_score or 0.5,
                        "novelty": result.signals.get("novelty", 0.5),
                    },
                )
        except Exception:
            logger.debug("Initiative evaluation failed", exc_info=True)

    @staticmethod
    def _trigger_matches(trigger, event: NormalizedEvent) -> bool:
        """Check if a trigger's conditions match an event."""
        cond = trigger.conditions or {}

        # Match event_type
        if cond.get("event_type") and cond["event_type"] != event.event_type:
            return False

        # Match source
        if cond.get("source") and cond["source"] != event.source:
            return False

        # Match entity_type
        if cond.get("entity_type") and cond["entity_type"] != event.entity_type:
            return False

        # Match importance threshold
        threshold = cond.get("importance_threshold")
        if threshold is not None:
            if (event.importance_score or 0) < threshold:
                return False

        # Match entity pattern (substring in entity_id or title)
        entity_match = cond.get("entity_match")
        if entity_match:
            target = f"{event.entity_id or ''} {event.title or ''}".lower()
            if entity_match.lower() not in target:
                return False

        return True

    async def _score_event(self, raw: RawEvent, user_id: str) -> dict:
        """Score an event using Claude with user context. Falls back to defaults."""
        user_message = await self._build_scoring_message(raw, user_id)

        try:
            response = await self._client.messages.create(
                model=self._settings.resolved_model,
                max_tokens=512,
                system=SCORING_SYSTEM_PROMPT,
                messages=[{"role": "user", "content": user_message}],
            )
            text = response.content[0].text
            if text.startswith("```"):
                text = text.split("\n", 1)[1].rsplit("```", 1)[0]
            return json.loads(text)
        except Exception:
            logger.warning("Event scoring failed, using defaults", exc_info=True)
            return {**DEFAULT_SCORES, "summary": raw.summary}

    async def _build_scoring_message(self, raw: RawEvent, user_id: str) -> str:
        parts = [f"Source: {raw.source}", f"Type: {raw.event_type}"]
        if raw.title:
            parts.append(f"Title: {raw.title}")
        if raw.summary:
            parts.append(f"Summary: {raw.summary}")
        if raw.actor:
            actor_str = raw.actor.get("name", raw.actor.get("email", "unknown"))
            parts.append(f"From: {actor_str}")

        # Enrich with user context for better scoring
        context = await self._gather_scoring_context(raw, user_id)
        if context:
            parts.append(f"\n--- User Context ---\n{context}")

        return "\n".join(parts)

    async def _gather_scoring_context(self, raw: RawEvent, user_id: str) -> str | None:
        """Gather entity and preference context to improve scoring accuracy."""
        context_parts = []

        # Look up the sender/actor in the entity graph
        if self._world_model and raw.actor:
            actor_query = raw.actor.get("email") or raw.actor.get("name", "")
            if actor_query:
                entities = await self._world_model.find_entity(user_id, actor_query)
                if entities:
                    ent = entities[0]
                    attrs = ent.get("attributes") or {}
                    line = f"Known entity: {ent['canonical_name']} ({ent['entity_type']})"
                    if attrs.get("role"):
                        line += f", role: {attrs['role']}"
                    if attrs.get("company"):
                        line += f", company: {attrs['company']}"
                    context_parts.append(line)

        # Inject user preferences relevant to scoring
        if self._memory_service:
            prefs = await self._memory_service.get_user_preferences(user_id, max_results=5)
            if prefs:
                pref_lines = [p["fact_text"] for p in prefs[:5]]
                pref_text = "\n".join(f"- {p}" for p in pref_lines)
                context_parts.append(f"User preferences:\n{pref_text}")

        return "\n".join(context_parts) if context_parts else None
