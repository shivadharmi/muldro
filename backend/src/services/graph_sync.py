"""Graph sync — keeps Neo4j in sync with Postgres entity tables.

Subscribes to entity change events on the event bus and syncs
to Neo4j. Also runs periodic full reconciliation.
"""

import logging

from sqlalchemy import select
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
        from sqlalchemy import or_

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
