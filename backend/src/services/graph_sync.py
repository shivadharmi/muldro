"""Graph sync — keeps Neo4j in sync with Postgres entity tables.

Subscribes to entity change events on the event bus and syncs
to Neo4j. Also runs periodic full reconciliation.
"""

import logging

from sqlalchemy import or_, select
from sqlalchemy.ext.asyncio import AsyncSession

from src.config.settings import Settings
from src.models.entities import Entity, EntityRelationship
from src.services.event_bus import BusEvent
from src.services.graph_engine import GraphEngine

logger = logging.getLogger(__name__)


class GraphSyncService:
    """Keeps Neo4j graph in sync with Postgres entity data."""

    def __init__(self, settings: Settings, db: AsyncSession):
        self._settings = settings
        self._db = db
        self._graph = GraphEngine(settings)
        self._sync_failures: int = 0
        self._last_sync_error: str | None = None

    async def on_entity_change(self, event: BusEvent) -> None:
        """Handle entity created/updated events."""
        payload = event.payload
        entity_id = payload.get("entity_id", "")
        if not entity_id:
            return

        result = await self._db.execute(select(Entity).where(Entity.entity_id == entity_id))
        entity = result.scalar_one_or_none()
        if not entity:
            return

        await self._graph.sync_entity(
            entity_id=entity.entity_id,
            entity_type=entity.entity_type,
            name=entity.canonical_name,
            user_id=entity.user_id,
            attributes=entity.attributes,
        )
        logger.debug("Synced entity %s to Neo4j", entity_id)

    async def on_relationship_change(self, event: BusEvent) -> None:
        """Handle relationship created/updated events."""
        payload = event.payload
        rel_id = payload.get("relationship_id", payload.get("relation_id", ""))
        if not rel_id:
            return

        result = await self._db.execute(
            select(EntityRelationship).where(EntityRelationship.relation_id == rel_id)
        )
        rel = result.scalar_one_or_none()
        if not rel:
            return

        await self._graph.sync_relationship(
            relation_id=rel.relation_id,
            from_entity_id=rel.from_entity_id,
            to_entity_id=rel.to_entity_id,
            relation_type=rel.relation_type,
            user_id=rel.user_id,
        )
        logger.debug("Synced relationship %s to Neo4j", rel_id)

    async def sync_entity_by_id(self, entity_id: str) -> None:
        """Directly sync a single entity to Neo4j by ID."""
        result = await self._db.execute(select(Entity).where(Entity.entity_id == entity_id))
        entity = result.scalar_one_or_none()
        if not entity:
            return

        await self._graph.sync_entity(
            entity_id=entity.entity_id,
            entity_type=entity.entity_type,
            name=entity.canonical_name,
            user_id=entity.user_id,
            attributes=entity.attributes,
        )

    async def sync_relationships_for_entity(self, entity_id: str) -> None:
        """Sync all relationships involving an entity to Neo4j."""
        result = await self._db.execute(
            select(EntityRelationship).where(
                or_(
                    EntityRelationship.from_entity_id == entity_id,
                    EntityRelationship.to_entity_id == entity_id,
                )
            )
        )
        rels = result.scalars().all()
        for rel in rels:
            await self._graph.sync_relationship(
                relation_id=rel.relation_id,
                from_entity_id=rel.from_entity_id,
                to_entity_id=rel.to_entity_id,
                relation_type=rel.relation_type,
                user_id=rel.user_id,
            )

    async def batch_sync_entities(self, entity_ids: list[str]) -> dict:
        """Sync multiple entities and their relationships in bulk queries."""
        # Batch-load all entities in one query
        result = await self._db.execute(select(Entity).where(Entity.entity_id.in_(entity_ids)))
        entities = result.scalars().all()

        synced = 0
        for entity in entities:
            try:
                await self._graph.sync_entity(
                    entity_id=entity.entity_id,
                    entity_type=entity.entity_type,
                    name=entity.canonical_name,
                    user_id=entity.user_id,
                    attributes=entity.attributes,
                )
                synced += 1
                self._sync_failures = max(0, self._sync_failures - 1)
            except Exception as exc:
                self._sync_failures += 1
                self._last_sync_error = str(exc)[:200]
                logger.warning("Batch entity sync failed for %s: %s", entity.entity_id, exc)

        # Batch-load relationships for all entities
        result = await self._db.execute(
            select(EntityRelationship).where(
                or_(
                    EntityRelationship.from_entity_id.in_(entity_ids),
                    EntityRelationship.to_entity_id.in_(entity_ids),
                )
            )
        )
        rels = result.scalars().all()
        rels_synced = 0
        for rel in rels:
            try:
                await self._graph.sync_relationship(
                    relation_id=rel.relation_id,
                    from_entity_id=rel.from_entity_id,
                    to_entity_id=rel.to_entity_id,
                    relation_type=rel.relation_type,
                    user_id=rel.user_id,
                )
                rels_synced += 1
            except Exception as exc:
                self._sync_failures += 1
                self._last_sync_error = str(exc)[:200]

        return {"entities_synced": synced, "relationships_synced": rels_synced}

    def get_sync_stats(self) -> dict:
        """Return sync health metrics."""
        return {
            "failures": self._sync_failures,
            "last_error": self._last_sync_error,
        }

    async def full_reconciliation(self, user_id: str) -> dict:
        """Full sync of all entities and relationships for a user."""
        entities_result = await self._db.execute(select(Entity).where(Entity.user_id == user_id))
        entities = entities_result.scalars().all()

        rels_result = await self._db.execute(
            select(EntityRelationship).where(EntityRelationship.user_id == user_id)
        )
        rels = rels_result.scalars().all()

        entity_count = 0
        for entity in entities:
            try:
                await self._graph.sync_entity(
                    entity_id=entity.entity_id,
                    entity_type=entity.entity_type,
                    name=entity.canonical_name,
                    user_id=entity.user_id,
                    attributes=entity.attributes,
                )
                entity_count += 1
            except Exception:
                logger.warning(
                    "Failed to sync entity %s",
                    entity.entity_id,
                    exc_info=True,
                )

        rel_count = 0
        for rel in rels:
            try:
                await self._graph.sync_relationship(
                    relation_id=rel.relation_id,
                    from_entity_id=rel.from_entity_id,
                    to_entity_id=rel.to_entity_id,
                    relation_type=rel.relation_type,
                    user_id=rel.user_id,
                )
                rel_count += 1
            except Exception:
                logger.warning(
                    "Failed to sync relationship %s",
                    rel.relation_id,
                    exc_info=True,
                )

        logger.info(
            "Full reconciliation for %s: %d entities, %d relationships",
            user_id,
            entity_count,
            rel_count,
        )
        return {"entities_synced": entity_count, "relationships_synced": rel_count}

    async def close(self) -> None:
        """Close the graph engine connection."""
        await self._graph.close()
