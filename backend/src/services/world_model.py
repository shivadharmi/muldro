"""World Model — maintains entities and relationships.

The structured representation of everything Jarvis knows about:
people, projects, tasks, meetings, organizations, goals.

Responsibilities:
- Upsert entities from events
- Extract entities using Claude
- Maintain relationships between entities
- Provide lookup APIs for planner and presenter
- Merge duplicates and resolve aliases
"""

import json
import logging

import anthropic
from sqlalchemy import or_, select
from sqlalchemy.ext.asyncio import AsyncSession
from ulid import ULID

from src.config.settings import Settings
from src.models.entities import Entity, EntityAlias, EntityRelationship
from src.models.events import NormalizedEvent

logger = logging.getLogger(__name__)

ENTITY_EXTRACTION_PROMPT = """\
You are Jarvis's entity extraction engine. Given an event, extract the people, \
organizations, and projects mentioned.

You MUST respond with valid JSON matching this schema:
{
  "entities": [
    {
      "entity_type": "person" | "organization" | "project" | "meeting",
      "canonical_name": "Full Name or Title",
      "aliases": ["email@addr", "nickname", "handle"],
      "attributes": {"role": "...", "company": "...", ...}
    }
  ],
  "relationships": [
    {
      "from_name": "Entity A canonical name",
      "relation_type": "works_on" | "related_to" | "scheduled_with" | "reports_to" | "owns",
      "to_name": "Entity B canonical name"
    }
  ]
}

Rules:
- Always extract the sender/actor as a person entity
- Include email addresses as aliases
- If you cannot determine a canonical name, use the email address
- Only extract relationships you are reasonably confident about
- Keep entity_type to: person, organization, project, meeting
"""


class WorldModel:
    """Manage the entity graph."""

    def __init__(self, settings: Settings, db: AsyncSession):
        self._settings = settings
        self._db = db
        self._client = anthropic.AsyncAnthropic(api_key=settings.anthropic_api_key)

    async def extract_from_event(self, event_id: str, user_id: str) -> list[str]:
        """Extract entities from a normalized event. Returns list of entity_ids."""
        result = await self._db.execute(
            select(NormalizedEvent).where(NormalizedEvent.event_id == event_id)
        )
        event = result.scalar_one_or_none()
        if not event:
            logger.warning("Event not found for extraction: %s", event_id)
            return []

        extracted = await self._call_extraction(event)
        entity_ids = []

        for ent_data in extracted.get("entities", []):
            entity_id = await self.upsert_entity(
                user_id=user_id,
                entity_type=ent_data.get("entity_type", "person"),
                canonical_name=ent_data.get("canonical_name", "Unknown"),
                attributes=ent_data.get("attributes"),
                aliases=ent_data.get("aliases"),
            )
            if entity_id:
                entity_ids.append(entity_id)

        for rel_data in extracted.get("relationships", []):
            await self._create_relationship_by_name(
                user_id=user_id,
                from_name=rel_data.get("from_name", ""),
                relation_type=rel_data.get("relation_type", "related_to"),
                to_name=rel_data.get("to_name", ""),
            )

        return entity_ids

    async def upsert_entity(
        self,
        user_id: str,
        entity_type: str,
        canonical_name: str,
        attributes: dict | None = None,
        aliases: list[str] | None = None,
        source_refs: list[dict] | None = None,
    ) -> str:
        """Create or update an entity. Returns entity_id."""
        existing = await self._find_by_name_or_alias(user_id, canonical_name, aliases)
        if existing:
            if attributes:
                merged = {**(existing.attributes or {}), **attributes}
                existing.attributes = merged
            if aliases:
                await self._add_aliases(existing.entity_id, aliases)
            await self._db.commit()
            return existing.entity_id

        entity_id = f"ent_{ULID()}"
        entity = Entity(
            entity_id=entity_id,
            user_id=user_id,
            entity_type=entity_type,
            canonical_name=canonical_name,
            attributes=attributes,
            source_refs=source_refs,
        )
        self._db.add(entity)

        if aliases:
            for alias in aliases:
                self._db.add(
                    EntityAlias(
                        entity_id=entity_id,
                        alias=alias,
                        alias_type=self._guess_alias_type(alias),
                    )
                )

        await self._db.commit()
        logger.info("Entity created: %s type=%s name=%s", entity_id, entity_type, canonical_name)
        return entity_id

    async def add_relationship(
        self,
        user_id: str,
        from_entity_id: str,
        relation_type: str,
        to_entity_id: str,
        attributes: dict | None = None,
    ) -> str:
        """Add a relationship between entities. Returns relation_id."""
        existing = await self._db.execute(
            select(EntityRelationship).where(
                EntityRelationship.from_entity_id == from_entity_id,
                EntityRelationship.relation_type == relation_type,
                EntityRelationship.to_entity_id == to_entity_id,
            )
        )
        if existing.scalar_one_or_none():
            return ""

        relation_id = f"rel_{ULID()}"
        rel = EntityRelationship(
            relation_id=relation_id,
            user_id=user_id,
            from_entity_id=from_entity_id,
            relation_type=relation_type,
            to_entity_id=to_entity_id,
            attributes=attributes,
        )
        self._db.add(rel)
        await self._db.commit()
        return relation_id

    async def find_entity(self, user_id: str, query: str) -> list[dict]:
        """Search entities by name or alias."""
        pattern = f"%{query}%"
        result = await self._db.execute(
            select(Entity).where(
                Entity.user_id == user_id,
                or_(
                    Entity.canonical_name.ilike(pattern),
                    Entity.entity_id.in_(
                        select(EntityAlias.entity_id).where(EntityAlias.alias.ilike(pattern))
                    ),
                ),
            )
        )
        entities = result.scalars().all()
        return [
            {
                "entity_id": e.entity_id,
                "entity_type": e.entity_type,
                "canonical_name": e.canonical_name,
                "attributes": e.attributes,
            }
            for e in entities
        ]

    async def _find_by_name_or_alias(
        self, user_id: str, canonical_name: str, aliases: list[str] | None
    ) -> Entity | None:
        """Find an existing entity by canonical name or any alias."""
        result = await self._db.execute(
            select(Entity).where(
                Entity.user_id == user_id,
                Entity.canonical_name == canonical_name,
            )
        )
        entity = result.scalar_one_or_none()
        if entity:
            return entity

        if aliases:
            for alias in aliases:
                result = await self._db.execute(
                    select(Entity).where(
                        Entity.user_id == user_id,
                        Entity.entity_id.in_(
                            select(EntityAlias.entity_id).where(EntityAlias.alias == alias)
                        ),
                    )
                )
                entity = result.scalar_one_or_none()
                if entity:
                    return entity
        return None

    async def _add_aliases(self, entity_id: str, aliases: list[str]) -> None:
        """Add new aliases to an entity (skip existing)."""
        result = await self._db.execute(
            select(EntityAlias.alias).where(EntityAlias.entity_id == entity_id)
        )
        existing_aliases = set(result.scalars().all())

        for alias in aliases:
            if alias not in existing_aliases:
                self._db.add(
                    EntityAlias(
                        entity_id=entity_id,
                        alias=alias,
                        alias_type=self._guess_alias_type(alias),
                    )
                )

    async def _create_relationship_by_name(
        self, user_id: str, from_name: str, relation_type: str, to_name: str
    ) -> None:
        """Create a relationship between entities identified by name."""
        from_entities = await self.find_entity(user_id, from_name)
        to_entities = await self.find_entity(user_id, to_name)
        if from_entities and to_entities:
            await self.add_relationship(
                user_id=user_id,
                from_entity_id=from_entities[0]["entity_id"],
                relation_type=relation_type,
                to_entity_id=to_entities[0]["entity_id"],
            )

    async def _call_extraction(self, event: NormalizedEvent) -> dict:
        """Call Claude to extract entities from an event."""
        parts = [f"Event type: {event.event_type}", f"Source: {event.source}"]
        if event.title:
            parts.append(f"Title: {event.title}")
        if event.summary:
            parts.append(f"Summary: {event.summary}")
        if event.actor_entities:
            parts.append(f"Actors: {json.dumps(event.actor_entities)}")
        user_message = "\n".join(parts)

        try:
            response = await self._client.messages.create(
                model=self._settings.anthropic_model,
                max_tokens=1024,
                system=ENTITY_EXTRACTION_PROMPT,
                messages=[{"role": "user", "content": user_message}],
            )
            text = response.content[0].text
            if text.startswith("```"):
                text = text.split("\n", 1)[1].rsplit("```", 1)[0]
            return json.loads(text)
        except Exception:
            logger.warning("Entity extraction failed", exc_info=True)
            return {"entities": [], "relationships": []}

    @staticmethod
    def _guess_alias_type(alias: str) -> str:
        if alias.startswith("@"):
            return "handle"
        if "@" in alias:
            return "email"
        return "name"
