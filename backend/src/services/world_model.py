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

import logging
import re
from datetime import datetime, timezone

from sqlalchemy import or_, select
from sqlalchemy.exc import IntegrityError
from sqlalchemy.ext.asyncio import AsyncSession
from ulid import ULID

from src.config.settings import Settings
from src.models.entities import Entity, EntityAlias, EntityRelationship
from src.services.entity_facts.confidence import (
    compute_confidence,
    current_confidence,
    days_since,
)
from src.services.entity_resolver import EntityResolver
from src.services.provenance import SourceRef, merge_source_refs
from src.services.world_model_extraction import (
    ENTITY_EXTRACTION_PROMPT,  # noqa: F401 -- re-export for external importers
    ENTITY_TYPES,  # noqa: F401 -- re-export for external importers
    RELATION_TYPES,  # noqa: F401 -- re-export for external importers
    WorldModelExtractionMixin,
)

logger = logging.getLogger(__name__)


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


def _find_by_alias_stmt(user_id: str, alias: str, workspace_id: str):
    """Build the exact-alias lookup SELECT for _find_by_name_or_alias.

    Extracted so the isolation test can compile it and guard the alias
    subquery's workspace_id scoping against regression.
    """
    return (
        select(Entity)
        .where(
            Entity.user_id == user_id,
            Entity.workspace_id == workspace_id,
            Entity.entity_id.in_(
                select(EntityAlias.entity_id).where(
                    EntityAlias.alias == alias,
                    EntityAlias.workspace_id == workspace_id,
                )
            ),
        )
        # Oldest-first + limit(1): an alias can resolve to multiple (duplicate) entities;
        # converge deterministically and keep scalar_one_or_none from raising.
        .order_by(Entity.created_at)
        .limit(1)
    )


def _entity_vector_payload(
    entity_type: str, canonical_name: str, user_id: str, workspace_id: str
) -> dict:
    """Qdrant payload for an entity vector. Includes workspace_id so
    workspace-scoped vector search actually matches — it was previously omitted,
    silently breaking scoped entity resolution/dedup."""
    payload = {
        "entity_type": entity_type,
        "canonical_name": canonical_name,
        "user_id": user_id,
    }
    if workspace_id:
        payload["workspace_id"] = workspace_id
    return payload


class WorldModel(WorldModelExtractionMixin):
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

    async def upsert_entity(
        self,
        user_id: str,
        entity_type: str,
        canonical_name: str,
        attributes: dict | None = None,
        aliases: list[str] | None = None,
        source_ref: SourceRef | None = None,
        importance: float | None = None,
        workspace_id: str = "",
        origin: str = "unknown",
    ) -> str:
        """Create or update an entity. Returns entity_id.

        ``origin`` labels the provenance of this observation (e.g. ``user_message``,
        ``perception``); it drives per-attribute fact recording (bi-temporal
        supersede) and the evidence-derived ``confidence_score``.
        """
        now = datetime.now(timezone.utc)
        # Privacy guard: never persist a bare email address as the canonical name.
        canonical_name, aliases = sanitize_canonical_name(canonical_name, aliases)
        existing = await self._find_by_name_or_alias(
            user_id, canonical_name, aliases, workspace_id=workspace_id
        )
        if existing:
            if attributes:
                await self._record_attribute_facts(
                    existing.entity_id,
                    user_id,
                    workspace_id,
                    attributes,
                    origin,
                    now,
                    source_ref=source_ref,
                )
                # entities.attributes stays the denormalized current snapshot (D2).
                existing.attributes = {**(existing.attributes or {}), **attributes}
            if aliases:
                await self._add_aliases(existing.entity_id, aliases, workspace_id=workspace_id)
            if source_ref is not None:
                existing.source_refs = merge_source_refs(existing.source_refs, source_ref)
            # Update temporal tracking
            existing.last_seen_at = now
            existing.interaction_count = (existing.interaction_count or 0) + 1
            if importance is not None:
                existing.importance_score = max(existing.importance_score or 0.0, importance)
            existing.confidence_score = compute_confidence(
                origin=origin,
                corroboration_count=existing.interaction_count or 1,
                age_days=0.0,
            )
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
            source_refs=[source_ref.to_dict()] if source_ref else None,
            last_seen_at=now,
            interaction_count=1,
            importance_score=importance or 0.5,
            confidence_score=compute_confidence(origin=origin, corroboration_count=1, age_days=0.0),
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
            # Pass aliases too: the conflict may be a strong-identifier (email/handle)
            # unique-index violation rather than a name collision, so resolve to whoever
            # already owns the name OR the alias.
            retry = await self._find_by_name_or_alias(
                user_id, canonical_name, aliases, workspace_id=workspace_id
            )
            if retry:
                return retry.entity_id
            raise

        # Record each attribute as a bi-temporal fact now that the entity row exists.
        if attributes:
            await self._record_attribute_facts(
                entity_id, user_id, workspace_id, attributes, origin, now, source_ref=source_ref
            )
            await self._db.commit()

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
                        _entity_vector_payload(entity_type, canonical_name, user_id, workspace_id),
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

    async def _record_attribute_facts(
        self,
        entity_id: str,
        user_id: str,
        workspace_id: str,
        attributes: dict,
        origin: str,
        now: datetime,
        source_ref: SourceRef | None = None,
    ) -> None:
        """Record each attribute as a bi-temporal fact (supersede-on-change). The
        entities.attributes JSONB stays the denormalized current snapshot (D2)."""
        from src.services.entity_facts.store import EntityFactStore

        store = EntityFactStore(self._db)
        ref_dict = source_ref.to_dict() if source_ref else None
        for attr_key, attr_value in attributes.items():
            try:
                await store.record_fact(
                    entity_id=entity_id,
                    workspace_id=workspace_id,
                    user_id=user_id,
                    attr_key=str(attr_key),
                    attr_value=attr_value,
                    origin=origin,
                    source_ref=ref_dict,
                    now=now,
                )
            except Exception:
                logger.debug(
                    "entity_fact record failed: entity=%s key=%s",
                    entity_id,
                    attr_key,
                    exc_info=True,
                )

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
            select(EntityRelationship)
            .where(
                EntityRelationship.from_entity_id == from_entity_id,
                EntityRelationship.relation_type == relation_type,
                EntityRelationship.to_entity_id == to_entity_id,
            )
            # Existence check — limit(1) tolerates duplicate triples (no unique constraint;
            # concurrent adds can race) instead of crashing on scalar_one_or_none.
            .limit(1)
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
                "confidence": current_confidence(
                    e.confidence_score, age_days=days_since(e.last_seen_at)
                ),
                "provenance": {"origin_hint": e.entity_type},
            }
            for e in entities
        ]

    async def resolve_entities(
        self, user_id: str, text: str, workspace_id: str = "", limit: int = 10
    ) -> list[dict]:
        """Resolve entity mentions in free text via span extraction + exact + FTS
        + vector (replaces the ILIKE-on-raw-message find_entity path). Returns the
        same dict shape as find_entity so callers/ranking are unchanged."""
        resolver = EntityResolver(
            self._db,
            workspace_id,
            embedding_service=self._embedding_service,
            vector_store=self._vector_store,
        )
        return await resolver.resolve(user_id, text, limit=limit)

    async def _find_by_name_or_alias(
        self,
        user_id: str,
        canonical_name: str,
        aliases: list[str] | None,
        workspace_id: str = "",
    ) -> Entity | None:
        """Find an existing entity by canonical name or any alias."""
        result = await self._db.execute(
            select(Entity)
            .where(
                Entity.user_id == user_id,
                Entity.workspace_id == workspace_id,
                Entity.canonical_name == canonical_name,
            )
            # Entity dedup is best-effort — concurrent extraction can create duplicate
            # name/alias entities. order_by + limit(1) picks the OLDEST deterministically
            # (repeated upserts converge on one canonical entity) and keeps
            # scalar_one_or_none from raising MultipleResultsFound.
            .order_by(Entity.created_at)
            .limit(1)
        )
        entity = result.scalar_one_or_none()
        if entity:
            return entity

        if aliases:
            for alias in aliases:
                result = await self._db.execute(_find_by_alias_stmt(user_id, alias, workspace_id))
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
        """Add new aliases to an entity, skipping aliases already on this entity and any
        strong identifier (email/handle) already owned by a *different* entity — the
        partial unique index would reject the latter, and the existing ownership means
        dedup should target that other entity, not duplicate the mapping here."""
        result = await self._db.execute(
            select(EntityAlias.alias).where(EntityAlias.entity_id == entity_id)
        )
        existing_aliases = set(result.scalars().all())

        for alias in aliases:
            if alias in existing_aliases:
                continue
            alias_type = self._guess_alias_type(alias)
            if alias_type in ("email", "handle"):
                owner = await self._db.execute(
                    select(EntityAlias.entity_id)
                    .where(
                        EntityAlias.workspace_id == workspace_id,
                        EntityAlias.alias == alias,
                        EntityAlias.alias_type == alias_type,
                    )
                    .limit(1)
                )
                if owner.scalar_one_or_none() not in (None, entity_id):
                    logger.debug(
                        "Strong alias already owned by another entity; skipping: %s", alias
                    )
                    continue
            self._db.add(
                EntityAlias(
                    entity_id=entity_id,
                    alias=alias,
                    alias_type=alias_type,
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
