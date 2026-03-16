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
    from src.services.memory_service import MemoryService
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

    async def process(self, raw: RawEvent, user_id: str) -> str | None:
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

    async def _score_event(self, raw: RawEvent, user_id: str) -> dict:
        """Score an event using Claude with user context. Falls back to defaults."""
        user_message = await self._build_scoring_message(raw, user_id)

        try:
            response = await self._client.messages.create(
                model=self._settings.anthropic_model,
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
