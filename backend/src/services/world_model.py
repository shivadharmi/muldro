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
import re
from datetime import datetime, timezone

from sqlalchemy import or_, select
from sqlalchemy.exc import IntegrityError
from sqlalchemy.ext.asyncio import AsyncSession
from ulid import ULID

from src.config.settings import Settings, get_anthropic_client
from src.models.entities import Entity, EntityAlias, EntityRelationship
from src.models.events import NormalizedEvent

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
You are Jarvis's entity extraction engine. Given an event, extract ALL entities \
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


# Simple, deterministic check for a bare email address used as a display name.
_EMAIL_RE = re.compile(r"^\s*[^@\s]+@[^@\s]+\.[^@\s]+\s*$")


def sanitize_canonical_name(
    canonical_name: str, aliases: list[str] | None
) -> tuple[str, list[str]]:
    """Privacy guard: never store a bare email address as an entity's canonical name.

    If ``canonical_name`` is a bare email address (PII), derive a privacy-preserving
    display label and push the raw email into ``aliases`` instead. A normal display
    name is returned unchanged.

    Returns a ``(canonical_name, aliases)`` tuple with a new aliases list (the input
    is never mutated). The raw email is preserved in aliases so lookups still work.
    """
    name = (canonical_name or "").strip()
    out_aliases = list(aliases or [])
    if not name or not _EMAIL_RE.match(name):
        return (name or "Unknown", out_aliases)

    email = name
    if email not in out_aliases:
        out_aliases.append(email)

    local, _, domain = email.partition("@")
    # Prefer a human-ish label from the local part; fall back to the domain.
    cleaned = re.sub(r"[._-]+", " ", local).strip()
    if cleaned and not cleaned.isdigit():
        label = cleaned.title()
    else:
        label = f"Sender ({domain})"
    return (label, out_aliases)


def _find_entity_stmt(user_id: str, query: str, workspace_id: str):
    """Build the find_entity SELECT. Extracted so isolation tests compile it."""
    pattern = f"%{query}%"
    return (
        select(Entity)
        .where(
            Entity.user_id == user_id,
            Entity.workspace_id == workspace_id,
            or_(
                Entity.canonical_name.ilike(pattern),
                Entity.entity_id.in_(
                    select(EntityAlias.entity_id).where(
                        EntityAlias.alias.ilike(pattern),
                        EntityAlias.workspace_id == workspace_id,
                    )
                ),
            ),
        )
        .order_by(Entity.importance_score.desc())
    )


class WorldModel:
    """Manage the entity graph."""

    def __init__(
        self,
        settings: Settings,
        db: AsyncSession,
        event_bus=None,
        embedding_service=None,
        vector_store=None,
        dead_letter=None,
    ):
        self._settings = settings
        self._db = db
        self._client = get_anthropic_client(settings)
        self._event_bus = event_bus
        self._embedding_service = embedding_service
        self._vector_store = vector_store
        self._dead_letter = dead_letter

    async def _enqueue_failed_embedding(
        self, record_id: str, user_id: str, collection: str = "entities"
    ) -> None:
        """Enqueue a failed embedding for retry via DLQ."""
        if not self._dead_letter:
            return
        try:
            await self._dead_letter.enqueue(
                user_id=user_id,
                operation_type="failed_embedding",
                error_type="EmbeddingFailure",
                error_message=f"Embedding/upsert failed for {collection}:{record_id}",
                payload={
                    "record_id": record_id,
                    "collection": collection,
                    "record_type": "entity",
                },
            )
        except Exception:
            logger.warning(
                "Failed to enqueue embedding retry for %s",
                record_id,
                exc_info=True,
            )

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

        extracted = await self._call_extraction(event)
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

    async def upsert_entity(
        self,
        user_id: str,
        entity_type: str,
        canonical_name: str,
        attributes: dict | None = None,
        aliases: list[str] | None = None,
        source_refs: list[dict] | None = None,
        importance: float | None = None,
        workspace_id: str = "",
    ) -> str:
        """Create or update an entity. Returns entity_id."""
        now = datetime.now(timezone.utc)
        # Privacy guard: never persist a bare email address as the canonical name.
        canonical_name, aliases = sanitize_canonical_name(canonical_name, aliases)
        existing = await self._find_by_name_or_alias(
            user_id, canonical_name, aliases, workspace_id=workspace_id
        )
        if existing:
            if attributes:
                merged = {**(existing.attributes or {}), **attributes}
                existing.attributes = merged
            if aliases:
                await self._add_aliases(existing.entity_id, aliases, workspace_id=workspace_id)
            # Update temporal tracking
            existing.last_seen_at = now
            existing.interaction_count = (existing.interaction_count or 0) + 1
            if importance is not None:
                existing.importance_score = max(existing.importance_score or 0.0, importance)
            await self._db.commit()
            await self._emit_event(
                "entity.updated",
                user_id,
                {"entity_id": existing.entity_id},
                workspace_id=workspace_id,
            )
            return existing.entity_id

        entity_id = f"ent_{ULID()}"
        embedding = None
        if self._embedding_service:
            try:
                embedding = await self._embedding_service.embed_text(canonical_name)
            except Exception:
                logger.debug("Failed to generate entity embedding", exc_info=True)
        entity = Entity(
            entity_id=entity_id,
            user_id=user_id,
            workspace_id=workspace_id,
            entity_type=entity_type,
            canonical_name=canonical_name,
            attributes=attributes,
            source_refs=source_refs,
            last_seen_at=now,
            interaction_count=1,
            importance_score=importance or 0.5,
        )
        self._db.add(entity)

        if aliases:
            for alias in aliases:
                self._db.add(
                    EntityAlias(
                        entity_id=entity_id,
                        alias=alias,
                        alias_type=self._guess_alias_type(alias),
                        workspace_id=workspace_id,
                    )
                )

        try:
            await self._db.commit()
        except IntegrityError:
            await self._db.rollback()
            logger.info(
                "Entity race condition, retrying lookup: name=%s",
                canonical_name,
            )
            retry = await self._find_by_name_or_alias(
                user_id, canonical_name, None, workspace_id=workspace_id
            )
            if retry:
                return retry.entity_id
            raise

        # Upsert entity vector to Qdrant
        emb = embedding
        if emb is None and self._embedding_service:
            try:
                emb = await self._embedding_service.embed_text(canonical_name)
            except Exception:
                logger.debug("Failed to generate entity embedding", exc_info=True)

        if emb:
            if self._vector_store:
                try:
                    await self._vector_store.upsert(
                        "entities",
                        entity_id,
                        emb,
                        {
                            "entity_type": entity_type,
                            "canonical_name": canonical_name,
                            "user_id": user_id,
                        },
                        user_id,
                    )
                except Exception:
                    logger.debug("Qdrant entity upsert failed for %s", entity_id, exc_info=True)
                    await self._enqueue_failed_embedding(entity_id, user_id)
            else:
                await self._enqueue_failed_embedding(entity_id, user_id)
        else:
            await self._enqueue_failed_embedding(entity_id, user_id)

        logger.info(
            "Entity created: %s type=%s name=%s",
            entity_id,
            entity_type,
            canonical_name,
        )
        await self._emit_event(
            "entity.created", user_id, {"entity_id": entity_id}, workspace_id=workspace_id
        )
        return entity_id

    async def add_relationship(
        self,
        user_id: str,
        from_entity_id: str,
        relation_type: str,
        to_entity_id: str,
        attributes: dict | None = None,
        workspace_id: str = "",
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
            workspace_id=workspace_id,
            from_entity_id=from_entity_id,
            relation_type=relation_type,
            to_entity_id=to_entity_id,
            attributes=attributes,
        )
        self._db.add(rel)
        await self._db.commit()
        await self._emit_event(
            "relationship.created",
            user_id,
            {"relation_id": relation_id, "relationship_id": relation_id},
            workspace_id=workspace_id,
        )
        return relation_id

    async def find_entity(self, user_id: str, query: str, workspace_id: str = "") -> list[dict]:
        """Search entities by name or alias. Ordered by importance."""
        result = await self._db.execute(_find_entity_stmt(user_id, query, workspace_id))
        entities = result.scalars().all()
        return [
            {
                "entity_id": e.entity_id,
                "entity_type": e.entity_type,
                "canonical_name": e.canonical_name,
                "attributes": e.attributes,
                "importance_score": e.importance_score,
                "interaction_count": e.interaction_count,
                "last_seen_at": (e.last_seen_at.isoformat() if e.last_seen_at else None),
            }
            for e in entities
        ]

    async def _find_by_name_or_alias(
        self,
        user_id: str,
        canonical_name: str,
        aliases: list[str] | None,
        workspace_id: str = "",
    ) -> Entity | None:
        """Find an existing entity by canonical name or any alias."""
        result = await self._db.execute(
            select(Entity).where(
                Entity.user_id == user_id,
                Entity.workspace_id == workspace_id,
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
                        Entity.workspace_id == workspace_id,
                        Entity.entity_id.in_(
                            select(EntityAlias.entity_id).where(
                                EntityAlias.alias == alias,
                                EntityAlias.workspace_id == workspace_id,
                            )
                        ),
                    )
                )
                entity = result.scalar_one_or_none()
                if entity:
                    return entity

        # Fuzzy match via Qdrant vector similarity
        if self._vector_store and self._embedding_service:
            try:
                embedding = await self._embedding_service.embed_text(canonical_name)
                if embedding:
                    similar = await self._vector_store.find_similar(
                        "entities",
                        embedding,
                        user_id,
                        threshold=0.92,
                        limit=1,
                        filters={"workspace_id": workspace_id} if workspace_id else None,
                    )
                    if similar:
                        eid = similar[0].get("payload", {}).get("_original_id") or similar[0]["id"]
                        result = await self._db.execute(
                            select(Entity).where(
                                Entity.entity_id == eid,
                                Entity.workspace_id == workspace_id,
                            )
                        )
                        return result.scalar_one_or_none()
            except Exception:
                logger.debug("Qdrant entity dedup failed", exc_info=True)

        return None

    async def _add_aliases(
        self, entity_id: str, aliases: list[str], workspace_id: str = ""
    ) -> None:
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
                        workspace_id=workspace_id,
                    )
                )

    async def _create_relationship_by_name(
        self,
        user_id: str,
        from_name: str,
        relation_type: str,
        to_name: str,
        workspace_id: str = "",
    ) -> None:
        """Create a relationship between entities identified by name."""
        from_entities = await self.find_entity(user_id, from_name, workspace_id=workspace_id)
        to_entities = await self.find_entity(user_id, to_name, workspace_id=workspace_id)
        if from_entities and to_entities:
            await self.add_relationship(
                user_id=user_id,
                from_entity_id=from_entities[0]["entity_id"],
                relation_type=relation_type,
                to_entity_id=to_entities[0]["entity_id"],
                workspace_id=workspace_id,
            )

    async def extract_from_text(self, text: str, user_id: str, workspace_id: str = "") -> list[str]:
        """Extract entities from free text (e.g. user messages). Returns entity_ids."""
        try:
            response = await self._client.messages.create(
                model=self._settings.resolved_model,
                max_tokens=1024,
                system=ENTITY_EXTRACTION_PROMPT,
                messages=[{"role": "user", "content": f"Source: user_message\nSummary: {text}"}],
            )
            from src.llm_utils import parse_llm_json

            extracted = parse_llm_json(
                response.content[0].text,
                default={"entities": [], "relationships": []},
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
                model=self._settings.resolved_model,
                max_tokens=1024,
                system=ENTITY_EXTRACTION_PROMPT,
                messages=[{"role": "user", "content": user_message}],
            )
            from src.llm_utils import parse_llm_json

            return parse_llm_json(
                response.content[0].text,
                default={"entities": [], "relationships": []},
            )
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

    async def _emit_event(
        self, event_type: str, user_id: str, payload: dict, workspace_id: str = ""
    ) -> None:
        """Publish a domain event (best-effort)."""
        if not self._event_bus:
            return
        try:
            stream = self._event_bus.agent_stream(workspace_id)
            await self._event_bus.publish(
                stream, event_type, payload, user_id, workspace_id=workspace_id
            )
        except Exception:
            logger.debug("Failed to emit %s event", event_type, exc_info=True)
