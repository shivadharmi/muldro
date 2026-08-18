"""WorldModelExtractionMixin — entity/relationship extraction from events and text.

Extracted from ``world_model.py`` (split-class-via-inheritance) to keep that file
under the 800-line cap. ``WorldModel`` inherits this mixin; the methods below rely
on sibling methods/attributes (``self.upsert_entity``, ``self._create_relationship_by_name``,
``self._db``) that are defined on ``WorldModel`` and resolved via inheritance at
runtime.
"""

from __future__ import annotations

import json
import logging

from sqlalchemy import select

from src.llm.utility import complete_text_with_usage
from src.models.events import NormalizedEvent
from src.orchestrator.budget import record_token_span
from src.services.provenance import SourceRef

logger = logging.getLogger(__name__)

ENTITY_TYPES = frozenset(
    {
        # Work domain
        "person",
        "organization",
        "project",
        "meeting",
        "goal",
        "task",
        "document",
        "message_thread",
        "repository",
        "channel",
        "product",
        "investment",
        "website",
        "tool",
        "watcher",
        # Personal domain
        "location",
        "health_record",
        "hobby",
        "family_member",
        "financial_account",
        "media_item",
        "recipe",
        "course",
        "contact_group",
        # Financial / money-movement domain
        "financial_transaction",
        "merchant",
    }
)

RELATION_TYPES = frozenset(
    {
        # Work domain
        "works_on",
        "related_to",
        "scheduled_with",
        "reports_to",
        "owns",
        "member_of",
        "assigned_to",
        "mentioned_in",
        "depends_on",
        "attends",
        "authored",
        "invested_in",
        "blocked_by",
        "sent_by",
        "attached_to",
        "derived_from",
        "monitors",
        # Personal domain
        "lives_at",
        "prescribed_by",
        "enrolled_in",
        "follows",
        "subscribes_to",
        "shares_with",
        "cares_for",
        # Financial / money-movement domain
        "paid_to",
        "charged_to",
    }
)

ENTITY_EXTRACTION_PROMPT = """\
You are Muldro's entity extraction engine. Given an event, extract ALL entities \
and relationships mentioned.

You MUST respond with valid JSON matching this schema:
{
  "entities": [
    {
      "entity_type": "<type>",
      "canonical_name": "Full Name or Title",
      "aliases": ["email@addr", "nickname", "handle"],
      "attributes": {"role": "...", "company": "...", ...},
      "importance": float 0.0-1.0
    }
  ],
  "relationships": [
    {
      "from_name": "Entity A canonical name",
      "relation_type": "<relation>",
      "to_name": "Entity B canonical name"
    }
  ]
}

Entity types: person, organization, project, meeting, goal, task, document, \
message_thread, repository, channel, product, investment, website, tool, watcher, \
location, health_record, hobby, family_member, financial_account, media_item, \
recipe, course, contact_group, financial_transaction, merchant

Relation types: works_on, related_to, scheduled_with, reports_to, owns, \
member_of, assigned_to, mentioned_in, depends_on, attends, authored, invested_in, \
blocked_by, sent_by, attached_to, derived_from, monitors, lives_at, prescribed_by, \
enrolled_in, follows, subscribes_to, shares_with, cares_for, paid_to, charged_to

Rules:
- Always extract the sender/actor as a person entity
- Include email addresses as aliases, NOT as the canonical_name
- Privacy: canonical_name MUST be a human display name. If you cannot determine \
one, use a descriptive label such as "Sender (example.com)" derived from the \
domain, and keep the raw email address only in aliases. NEVER use a bare email \
address as canonical_name.
- For spend/charge/payment/transaction events (e.g. a card was charged, money was \
sent or received), extract a `financial_transaction` entity. Put structured detail \
in its `attributes` dict: amount (number), currency (ISO code like "INR"/"USD"), \
merchant (name), account_last4 (last 4 digits of the card/account), and direction \
("debit" for money out, "credit" for money in). Use a concise canonical_name like \
"INR 1087 at <merchant>" or "Card charge INR 1087". Also extract the `merchant` as \
its own entity when named, and link the transaction with `paid_to` (merchant) and \
`charged_to` (the financial_account / card).
- Only extract relationships you are reasonably confident about
- Set importance: 1.0 for key people/active projects, 0.5 for mentioned entities, \
0.2 for incidental references
- Extract document/repo/channel entities when they are directly referenced
"""


class WorldModelExtractionMixin:
    """Entity/relationship extraction methods, mixed into ``WorldModel``."""

    async def extract_from_event(
        self, event_id: str, user_id: str, workspace_id: str = ""
    ) -> list[str]:
        """Extract entities from a normalized event. Returns list of entity_ids."""
        result = await self._db.execute(
            select(NormalizedEvent).where(NormalizedEvent.event_id == event_id)
        )
        event = result.scalar_one_or_none()
        if not event:
            logger.warning("Event not found for extraction: %s", event_id)
            return []

        extracted = await self._call_extraction(event, workspace_id=workspace_id)
        entity_ids = []
        ref = SourceRef(source=event.source, event_id=event_id)

        for ent_data in extracted.get("entities", []):
            raw_type = ent_data.get("entity_type", "person")
            entity_type = raw_type if raw_type in ENTITY_TYPES else "person"
            importance = min(max(float(ent_data.get("importance", 0.5)), 0.0), 1.0)

            entity_id = await self.upsert_entity(
                user_id=user_id,
                entity_type=entity_type,
                canonical_name=ent_data.get("canonical_name", "Unknown"),
                attributes=ent_data.get("attributes"),
                aliases=ent_data.get("aliases"),
                importance=importance,
                workspace_id=workspace_id,
                origin="perception",
                source_ref=ref,
            )
            if entity_id:
                entity_ids.append(entity_id)

        for rel_data in extracted.get("relationships", []):
            raw_rel = rel_data.get("relation_type", "related_to")
            relation_type = raw_rel if raw_rel in RELATION_TYPES else "related_to"
            await self._create_relationship_by_name(
                user_id=user_id,
                from_name=rel_data.get("from_name", ""),
                relation_type=relation_type,
                to_name=rel_data.get("to_name", ""),
                workspace_id=workspace_id,
            )

        return entity_ids

    async def extract_from_text(
        self,
        text: str,
        user_id: str,
        workspace_id: str = "",
        source_ref: SourceRef | None = None,
    ) -> list[str]:
        """Extract entities from free text (e.g. user messages). Returns entity_ids."""
        try:
            llm_text, usage = await complete_text_with_usage(
                system=ENTITY_EXTRACTION_PROMPT,
                user=f"Source: user_message\nSummary: {text}",
                tier="resolved",
                max_tokens=1024,
                workspace_id=workspace_id,
            )
            await record_token_span(
                agent_name="world_model",
                model=usage.model,
                input_tokens=usage.input_tokens,
                output_tokens=usage.output_tokens,
                cache_creation_input_tokens=usage.cache_creation_input_tokens,
                cache_read_input_tokens=usage.cache_read_input_tokens,
                trigger="perception",
                workspace_id=workspace_id,
            )
            from src.llm_utils import coerce_to_object, parse_llm_json

            extracted = coerce_to_object(
                parse_llm_json(
                    llm_text,
                    default={"entities": [], "relationships": []},
                ),
                list_key="entities",
            )
        except Exception:
            logger.warning("Text entity extraction failed", exc_info=True)
            return []

        entity_ids = []
        for ent_data in extracted.get("entities", []):
            raw_type = ent_data.get("entity_type", "person")
            entity_type = raw_type if raw_type in ENTITY_TYPES else "person"
            importance = min(max(float(ent_data.get("importance", 0.5)), 0.0), 1.0)
            entity_id = await self.upsert_entity(
                user_id=user_id,
                entity_type=entity_type,
                canonical_name=ent_data.get("canonical_name", "Unknown"),
                attributes=ent_data.get("attributes"),
                aliases=ent_data.get("aliases"),
                importance=importance,
                workspace_id=workspace_id,
                origin="user_message",
                source_ref=source_ref,
            )
            if entity_id:
                entity_ids.append(entity_id)

        for rel_data in extracted.get("relationships", []):
            raw_rel = rel_data.get("relation_type", "related_to")
            relation_type = raw_rel if raw_rel in RELATION_TYPES else "related_to"
            await self._create_relationship_by_name(
                user_id=user_id,
                from_name=rel_data.get("from_name", ""),
                relation_type=relation_type,
                to_name=rel_data.get("to_name", ""),
                workspace_id=workspace_id,
            )

        return entity_ids

    async def _call_extraction(self, event: NormalizedEvent, workspace_id: str = "") -> dict:
        """Call Claude to extract entities from an event. Records a perception token span."""
        parts = [f"Event type: {event.event_type}", f"Source: {event.source}"]
        if event.title:
            parts.append(f"Title: {event.title}")
        if event.summary:
            parts.append(f"Summary: {event.summary}")
        if event.actor_entities:
            parts.append(f"Actors: {json.dumps(event.actor_entities)}")
        user_message = "\n".join(parts)

        try:
            llm_text, usage = await complete_text_with_usage(
                system=ENTITY_EXTRACTION_PROMPT,
                user=user_message,
                tier="resolved",
                max_tokens=1024,
                workspace_id=workspace_id,
            )
            await record_token_span(
                agent_name="world_model",
                model=usage.model,
                input_tokens=usage.input_tokens,
                output_tokens=usage.output_tokens,
                cache_creation_input_tokens=usage.cache_creation_input_tokens,
                cache_read_input_tokens=usage.cache_read_input_tokens,
                trigger="perception",
                workspace_id=workspace_id,
            )
            from src.llm_utils import coerce_to_object, parse_llm_json

            parsed = parse_llm_json(
                llm_text,
                default={"entities": [], "relationships": []},
            )
            return coerce_to_object(parsed, list_key="entities")
        except Exception:
            logger.warning("Entity extraction failed", exc_info=True)
            return {"entities": [], "relationships": []}
