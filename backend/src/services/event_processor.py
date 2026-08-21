"""Event Processor — normalize, score, and deduplicate incoming events.

Responsibilities:
- Receive raw events from connectors
- Normalize to NormalizedEvent schema
- Score importance/urgency/confidence via Claude (context-aware)
- Deduplicate by idempotency key
- Store and trigger downstream processing (entity extraction, planning)
"""

from __future__ import annotations

import asyncio
import logging
import time
from dataclasses import dataclass
from datetime import datetime, timezone
from typing import TYPE_CHECKING

from sqlalchemy import select
from sqlalchemy.exc import IntegrityError
from sqlalchemy.ext.asyncio import AsyncSession
from ulid import ULID

from src.config.settings import Settings
from src.llm.utility import complete_text
from src.models.events import NormalizedEvent

if TYPE_CHECKING:
    from src.services.dead_letter import DeadLetterService
    from src.services.embedding_service import EmbeddingService
    from src.services.event_bus import EventBus
    from src.services.memory_service import MemoryService
    from src.services.notifier import Notifier
    from src.services.vector_store import VectorStore
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


def make_idempotency_key(raw: RawEvent) -> str:
    """Build a unique idempotency key for an event.

    Includes message_id when available (e.g., Gmail) for per-message
    granularity within threads. Notion mixes in last_edited_time and GitHub
    mixes in updated_at (carried on ``occurred_at``) for one reason: their
    entity_id names a durable THING - a page, or a pull request rather than
    the notification about it - so with no per-event field every later event
    on it takes the key the first already claimed and is dropped as a
    duplicate, and the card never updates. updated_at is chosen over GitHub's
    notification id because GitHub REUSES a thread's id for new activity on
    the same subject: the id is stable in exactly the case that collides.
    Falls back to source:entity_id:event_type otherwise - a GitHub event with
    no parseable updated_at degrades to the collision, never to a now().
    """
    payload = raw.raw_payload or {}
    message_id = payload.get("message_id", "")
    if message_id:
        return f"{raw.source}:{raw.entity_id}:{message_id}:{raw.event_type}"
    if raw.source == "notion":
        last_edited_time = payload.get("last_edited_time", "")
        if last_edited_time:
            return f"{raw.source}:{raw.entity_id}:{last_edited_time}:{raw.event_type}"
    if raw.source == "github" and raw.occurred_at is not None:
        return f"{raw.source}:{raw.entity_id}:{raw.occurred_at.isoformat()}:{raw.event_type}"
    return f"{raw.source}:{raw.entity_id}:{raw.event_type}"


SCORING_SYSTEM_PROMPT = """\
You are Muldro's event scoring engine. Given an event and optional user context, \
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
        world_model: WorldModel | None = None,
        memory_service: MemoryService | None = None,
        dead_letter: DeadLetterService | None = None,
        event_bus: EventBus | None = None,
        notifier: Notifier | None = None,
        embedding_service: EmbeddingService | None = None,
        vector_store: VectorStore | None = None,
    ):
        self._settings = settings
        self._db = db
        # Optional context providers for enriched scoring
        self._world_model = world_model
        self._memory_service = memory_service
        self._dead_letter = dead_letter
        self._event_bus = event_bus
        self._notifier = notifier
        self._embedding_service = embedding_service
        self._vector_store = vector_store
        self._semaphore = asyncio.Semaphore(settings.event_processor_concurrency)

    async def process(self, raw: RawEvent, user_id: str, workspace_id: str = "") -> str | None:
        """Process a raw event. Returns event_id if stored, None if duplicate.

        Gated by a semaphore to limit concurrent Claude API scoring calls.
        """
        async with self._semaphore:
            start = time.monotonic()
            event_id = await self._process_inner(raw, user_id, workspace_id)
            # Perception-throughput latency: only for events actually stored
            # (skip duplicates, which return None without doing scoring work).
            if event_id is not None:
                self._record_processing_latency(raw.source, (time.monotonic() - start) * 1000)
            return event_id

    async def _process_inner(
        self, raw: RawEvent, user_id: str, workspace_id: str = ""
    ) -> str | None:
        """Inner event processing — dedup, score, store, trigger downstream."""
        idempotency_key = make_idempotency_key(raw)

        existing = await self._db.execute(
            select(NormalizedEvent.event_id).where(
                NormalizedEvent.workspace_id == workspace_id,
                NormalizedEvent.idempotency_key == idempotency_key,
            )
        )
        if existing.scalar_one_or_none():
            logger.debug("Duplicate event skipped: %s", idempotency_key)
            return None

        scores = await self._score_event(raw, user_id, workspace_id)

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

        try:
            self._db.add(event)
            await self._db.commit()
        except IntegrityError:
            # Concurrent ingestion lost the race on the idempotency_key unique
            # constraint. Another cycle already stored this event — treat it
            # as a duplicate and skip downstream work.
            await self._db.rollback()
            logger.debug("Concurrent duplicate event skipped: %s", idempotency_key)
            return None

        logger.info(
            "Event processed: %s importance=%.2f urgency=%.2f",
            event_id,
            event.importance_score or 0,
            event.urgency_score or 0,
        )

        # Record Prometheus metrics
        await self._record_ingestion_metrics(
            event_id, raw.source, raw.event_type, user_id, workspace_id
        )

        # Embed into Qdrant for vector search (importance >= 0.3 only)
        await self._embed_event(event, workspace_id)

        # Publish to event bus for decoupled downstream processing
        if self._event_bus:
            try:
                await self._event_bus.publish(
                    self._event_bus.event_stream(workspace_id),
                    "event_processed",
                    {
                        "event_id": event_id,
                        "source": raw.source,
                        "event_type": raw.event_type,
                        "importance_score": event.importance_score or 0,
                        "urgency_score": event.urgency_score or 0,
                    },
                    user_id=user_id,
                    workspace_id=workspace_id,
                )
            except Exception:
                logger.warning("Failed to publish to event bus", exc_info=True)

        # Evaluate triggers against this event
        await self._evaluate_triggers(event, user_id, workspace_id=workspace_id)

        # Initiative scoring — decide if Muldro should proactively act
        await self._evaluate_initiative(event, user_id, workspace_id=workspace_id)

        return event_id

    async def _record_ingestion_metrics(
        self,
        event_id: str,
        source: str,
        event_type: str,
        user_id: str,
        workspace_id: str,
    ) -> None:
        """Record the Prometheus event-ingested counter. On failure, DLQ-enqueue
        so the miss isn't silently dropped. Shared by process() and the batch
        path — same metric, same fallback semantics either way."""
        try:
            from src.services.metrics_service import MetricsService

            MetricsService.record_event_ingested(source, event_type)
        except Exception as exc:
            logger.warning("Metrics recording failed: %s", exc)
            if self._dead_letter:
                try:
                    await self._dead_letter.enqueue(
                        user_id=user_id,
                        operation_type="metrics_recording",
                        error_type=type(exc).__name__,
                        error_message=str(exc)[:500],
                        payload={"event_id": event_id, "source": source},
                        workspace_id=workspace_id,
                    )
                except Exception:
                    logger.debug("DLQ enqueue failed for metrics", exc_info=True)

    @staticmethod
    def _record_processing_latency(source: str, duration_ms: float) -> None:
        """Record the Prometheus event-processing latency histogram."""
        try:
            from src.services.metrics_service import MetricsService

            MetricsService.record_event_processing(source, duration_ms)
        except Exception:
            logger.debug("Failed to record event-processing latency", exc_info=True)

    async def _embed_event(self, event: NormalizedEvent, workspace_id: str) -> None:
        """Embed an event into Qdrant for vector search (importance >= 0.3 only).

        Shared by process() and the batch path so semantic search stays
        populated regardless of which ingestion path stored the event.
        """
        if (event.importance_score or 0) < 0.3:
            return
        if not self._embedding_service or not self._vector_store:
            return
        try:
            parts = [event.event_type, event.source, event.title or "", event.summary or ""]
            text = " ".join(p for p in parts if p)
            embedding = await self._embedding_service.embed_text(text)
            if embedding:
                await self._vector_store.upsert(
                    collection="events",
                    id=event.event_id,
                    vector=embedding,
                    payload={
                        "event_id": event.event_id,
                        "event_type": event.event_type,
                        "source": event.source,
                        "importance_score": event.importance_score,
                        "workspace_id": workspace_id,
                        "occurred_at": event.occurred_at.isoformat() if event.occurred_at else None,
                        "actor": (event.actor_entities[0] or {}).get("name")
                        if event.actor_entities
                        else None,
                    },
                    user_id=event.user_id,
                )
        except Exception:
            logger.debug("Event embedding failed for %s", event.event_id, exc_info=True)

    async def _evaluate_triggers(
        self, event: NormalizedEvent, user_id: str, workspace_id: str = ""
    ) -> None:
        """Evaluate active triggers against a new event. Fire matching ones."""
        # Fail-safe: never evaluate triggers without a concrete workspace —
        # an empty workspace_id would scope the query to workspace "" and could
        # match/fire triggers across tenant boundaries.
        if not workspace_id:
            logger.warning(
                "Skipping trigger evaluation: empty workspace_id (user=%s, event=%s)",
                user_id,
                getattr(event, "event_id", "?"),
            )
            return
        try:
            from src.models.triggers import Trigger

            now = datetime.now(timezone.utc)
            result = await self._db.execute(
                select(Trigger).where(
                    Trigger.user_id == user_id,
                    Trigger.workspace_id == workspace_id,
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
                            self._event_bus.event_stream(workspace_id),
                            "trigger.fired",
                            {
                                "trigger_id": trigger.trigger_id,
                                "trigger_name": trigger.name,
                                "event_id": event.event_id,
                                "action_type": trigger.action_type,
                                "action_config": trigger.action_config,
                            },
                            user_id=user_id,
                            workspace_id=workspace_id,
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
            )
            result = await scorer.score(event, user_id)

            if result.is_high_priority:
                logger.info(
                    "High-priority event %s (score=%.3f) — handled by perception cycle",
                    event.event_id,
                    result.score,
                )

                if self._event_bus:
                    await self._event_bus.publish(
                        self._event_bus.event_stream(workspace_id),
                        "initiative.high_priority",
                        {
                            "event_id": event.event_id,
                            "score": result.score,
                            "signals": result.signals,
                        },
                        user_id=user_id,
                        workspace_id=workspace_id,
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

    async def _score_event(self, raw: RawEvent, user_id: str, workspace_id: str = "") -> dict:
        """Score an event using Claude with user context. Falls back to defaults."""
        user_message = await self._build_scoring_message(raw, user_id)

        fallback = {**DEFAULT_SCORES, "summary": raw.summary}
        try:
            text = await complete_text(
                system=SCORING_SYSTEM_PROMPT,
                user=user_message,
                tier="resolved",
                max_tokens=512,
                workspace_id=workspace_id,
            )
        except Exception:
            logger.warning("Event scoring failed, using defaults", exc_info=True)
            return fallback
        # `parse_llm_object`, not `parse_llm_json`: a JSON ARRAY parses SUCCESSFULLY and
        # would escape to `process`, which calls `scores.get("summary")` on it.
        from src.llm_utils import parse_llm_object

        return parse_llm_object(text, default=fallback)

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

    # ------------------------------------------------------------------
    # Batch processing — score multiple events in a single Claude call
    # ------------------------------------------------------------------

    BATCH_SIZE = 10

    async def process_batch(
        self, events: list[RawEvent], user_id: str, workspace_id: str = ""
    ) -> list[str | None]:
        """Process multiple events with batch scoring. Returns list of event_ids.

        Groups up to BATCH_SIZE events into a single Claude scoring call,
        reducing API costs ~10x under burst ingestion.
        """
        if not events:
            return []

        # Process in chunks of BATCH_SIZE
        results: list[str | None] = []
        for i in range(0, len(events), self.BATCH_SIZE):
            chunk = events[i : i + self.BATCH_SIZE]
            chunk_results = await self._process_batch_chunk(chunk, user_id, workspace_id)
            results.extend(chunk_results)
        return results

    async def _process_batch_chunk(
        self, events: list[RawEvent], user_id: str, workspace_id: str
    ) -> list[str | None]:
        """Score and store a chunk of events via a single Claude call."""
        chunk_start = time.monotonic()

        # 1. Batch dedup check
        keys = [make_idempotency_key(r) for r in events]
        existing = await self._db.execute(
            select(NormalizedEvent.idempotency_key).where(
                NormalizedEvent.workspace_id == workspace_id,
                NormalizedEvent.idempotency_key.in_(keys),
            )
        )
        existing_keys = {row[0] for row in existing.all()}

        non_dupe_events = [(r, k) for r, k in zip(events, keys) if k not in existing_keys]
        if not non_dupe_events:
            return [None] * len(events)

        # 2. Batch score via single Claude call
        async with self._semaphore:
            scores_list = await self._score_events_batch(
                [r for r, _ in non_dupe_events], user_id, workspace_id
            )

        # 3. Store events + post-process
        results: list[str | None] = []
        score_idx = 0
        for raw, key in zip(events, keys):
            if key in existing_keys:
                results.append(None)
                continue
            scores = scores_list[score_idx] if score_idx < len(scores_list) else DEFAULT_SCORES
            score_idx += 1

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
                idempotency_key=key,
                status="processed",
            )
            self._db.add(event)
            results.append(event_id)

        try:
            await self._db.commit()
        except IntegrityError:
            # Concurrent ingestion lost the race on the idempotency_key unique
            # constraint for one (or more) events in this chunk. Unlike the
            # single-event path (process()/_process_inner), a chunk-wide
            # commit can't identify which row conflicted without re-running
            # the insert per-event, so we treat the whole chunk as a dedup
            # skip: rollback and report every event as None rather than
            # letting the IntegrityError crash the rest of the batch (and
            # the caller's remaining chunks). The next ingestion cycle will
            # naturally pick up any event that wasn't actually a duplicate,
            # since it re-reads from source.
            await self._db.rollback()
            logger.warning(
                "Batch commit hit IntegrityError (likely concurrent duplicate); "
                "treating %d event(s) in this chunk as skipped",
                len(non_dupe_events),
            )
            return [None] * len(events)

        # 4. Post-process (triggers + initiative) for stored events
        for raw, key in zip(events, keys):
            if key not in existing_keys:
                try:
                    result_ = await self._db.execute(
                        select(NormalizedEvent).where(
                            NormalizedEvent.workspace_id == workspace_id,
                            NormalizedEvent.idempotency_key == key,
                        )
                    )
                    ev = result_.scalar_one_or_none()
                    if ev:
                        # Record Prometheus metrics (mirrors process()/_process_inner).
                        await self._record_ingestion_metrics(
                            ev.event_id, raw.source, raw.event_type, user_id, workspace_id
                        )
                        self._record_processing_latency(
                            raw.source, (time.monotonic() - chunk_start) * 1000
                        )

                        # Embed into Qdrant for vector search (importance >= 0.3 only,
                        # mirrors process()/_process_inner — without this, semantic
                        # search silently stops being populated for batch-triaged events).
                        await self._embed_event(ev, workspace_id)

                        # Publish to event bus for decoupled downstream processing
                        # (mirrors process()/_process_inner — worker extraction
                        # consumers key off this exact event/stream shape).
                        if self._event_bus:
                            try:
                                await self._event_bus.publish(
                                    self._event_bus.event_stream(workspace_id),
                                    "event_processed",
                                    {
                                        "event_id": ev.event_id,
                                        "source": raw.source,
                                        "event_type": raw.event_type,
                                        "importance_score": ev.importance_score or 0,
                                        "urgency_score": ev.urgency_score or 0,
                                    },
                                    user_id=user_id,
                                    workspace_id=workspace_id,
                                )
                            except Exception:
                                logger.warning("Failed to publish to event bus", exc_info=True)

                        await self._evaluate_triggers(ev, user_id, workspace_id=workspace_id)
                        await self._evaluate_initiative(ev, user_id, workspace_id=workspace_id)
                except Exception:
                    logger.debug("Batch post-process failed for %s", key, exc_info=True)

        return results

    async def _score_events_batch(
        self, events: list[RawEvent], user_id: str, workspace_id: str = ""
    ) -> list[dict]:
        """Triage + score events in one batched call. Returns a per-event dict
        carrying scores AND triage fields (category/tier/actionable) in
        importance_signals. Triage is rules-first; only the ambiguous remainder
        hits Haiku. ``workspace_id`` attributes the triage token span."""
        from src.services.triage import TriageService

        triage_results = await TriageService().triage_batch(events, user_id, workspace_id)
        out: list[dict] = []
        for raw, tr in zip(events, triage_results):
            out.append(
                {
                    "importance_score": tr.importance_score,
                    "urgency_score": tr.urgency_score,
                    "confidence_score": tr.confidence_score,
                    "importance_signals": tr.to_signals(),
                    "summary": raw.summary,
                }
            )
        return out
