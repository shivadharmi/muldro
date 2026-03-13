"""Event Processor — normalize, score, and deduplicate incoming events.

Responsibilities:
- Receive raw events from connectors
- Normalize to NormalizedEvent schema
- Score importance/urgency/confidence via Claude
- Deduplicate by idempotency key
- Store and trigger downstream processing (entity extraction, planning)
"""

import json
import logging
from dataclasses import dataclass
from datetime import datetime, timezone

import anthropic
from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession
from ulid import ULID

from src.config.settings import Settings
from src.models.events import NormalizedEvent

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


SCORING_SYSTEM_PROMPT = """\
You are Jarvis's event scoring engine. Given an event, evaluate its importance \
and urgency for a busy founder.

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

    def __init__(self, settings: Settings, db: AsyncSession):
        self._settings = settings
        self._db = db
        self._client = anthropic.AsyncAnthropic(api_key=settings.anthropic_api_key)

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

        scores = await self._score_event(raw)

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
        return event_id

    async def _score_event(self, raw: RawEvent) -> dict:
        """Score an event using Claude. Falls back to defaults on failure."""
        user_message = self._build_scoring_message(raw)

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

    def _build_scoring_message(self, raw: RawEvent) -> str:
        parts = [f"Source: {raw.source}", f"Type: {raw.event_type}"]
        if raw.title:
            parts.append(f"Title: {raw.title}")
        if raw.summary:
            parts.append(f"Summary: {raw.summary}")
        if raw.actor:
            actor_str = raw.actor.get("name", raw.actor.get("email", "unknown"))
            parts.append(f"From: {actor_str}")
        return "\n".join(parts)
