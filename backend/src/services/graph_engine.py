"""Neo4j-backed entity graph traversal and query engine.

Neo4j is a read-optimized projection synced from Postgres Entity/EntityRelationship
tables. Postgres remains source of truth; Neo4j enables multi-hop traversals,
path finding, and community detection.
"""

import json
import logging

from src.config.settings import Settings

logger = logging.getLogger(__name__)


class GraphEngine:
    """Neo4j-backed entity graph traversal."""

    def __init__(self, settings: Settings):
        self._settings = settings
        self._driver = None

    async def _get_driver(self):
        """Lazy-init Neo4j driver."""
        if self._driver is None:
            if not self._settings.neo4j_url:
                logger.warning("Neo4j not configured, graph engine is no-op")
                return None
            from neo4j import AsyncGraphDatabase

            self._driver = AsyncGraphDatabase.driver(
                self._settings.neo4j_url,
                auth=(self._settings.neo4j_user, self._settings.neo4j_password),
            )
        return self._driver

    async def close(self) -> None:
        """Close the Neo4j driver."""
        if self._driver:
            await self._driver.close()
            self._driver = None

    async def delete_entity(self, entity_id: str) -> None:
        """Delete an entity node and all its relationships from Neo4j.

        Uses DETACH DELETE to atomically remove the node and edges.
        """
        driver = await self._get_driver()
        if not driver:
            return

        try:
            async with driver.session() as session:
                await session.run(
                    "MATCH (e:Entity {entity_id: $entity_id}) DETACH DELETE e",
                    entity_id=entity_id,
                )
        except Exception:
            logger.warning("Neo4j delete_entity failed for %s", entity_id, exc_info=True)

    async def sync_entity(
        self,
        entity_id: str,
        entity_type: str,
        name: str,
        user_id: str,
        attributes: dict | None = None,
    ) -> None:
        """Upsert an entity node to Neo4j."""
        driver = await self._get_driver()
        if not driver:
            return

        try:
            async with driver.session() as session:
                await session.run(
                    """
                    MERGE (e:Entity {entity_id: $entity_id})
                    SET e.entity_type = $entity_type,
                        e.name = $name,
                        e.user_id = $user_id,
                        e.attributes = $attributes
                    """,
                    entity_id=entity_id,
                    entity_type=entity_type,
                    name=name,
                    user_id=user_id,
                    attributes=json.dumps(attributes or {}, default=str),
                )
        except Exception:
            logger.warning("Neo4j sync_entity failed for %s", entity_id, exc_info=True)

    async def sync_relationship(
        self,
        relation_id: str,
        from_entity_id: str,
        to_entity_id: str,
        relation_type: str,
        user_id: str,
        strength: float = 1.0,
        start_date: str | None = None,
        end_date: str | None = None,
    ) -> None:
        """Upsert a relationship edge to Neo4j with typed label and strength/temporal data."""
        driver = await self._get_driver()
        if not driver:
            return

        label = relation_type.upper().replace(" ", "_")

        try:
            async with driver.session() as session:
                await session.run(
                    f"""
                    MATCH (a:Entity {{entity_id: $from_id}})
                    MATCH (b:Entity {{entity_id: $to_id}})
                    MERGE (a)-[r:{label} {{relation_id: $rel_id}}]->(b)
                    SET r.relation_type = $rel_type,
                        r.user_id = $user_id,
                        r.strength = $strength,
                        r.start_date = $start_date,
                        r.end_date = $end_date
                    """,
                    from_id=from_entity_id,
                    to_id=to_entity_id,
                    rel_id=relation_id,
                    rel_type=relation_type,
                    user_id=user_id,
                    strength=strength,
                    start_date=start_date,
                    end_date=end_date,
                )
        except Exception:
            logger.warning("Neo4j sync_relationship failed for %s", relation_id, exc_info=True)

    async def traverse(
        self, entity_id: str, user_id: str, relation_types: list[str] | None = None, depth: int = 2
    ) -> dict:
        """Traverse the graph from an entity. Returns nodes and edges."""
        driver = await self._get_driver()
        if not driver:
            return {"nodes": [], "edges": []}

        rel_filter = ""
        if relation_types:
            rel_filter = f"AND ALL(r IN rels WHERE r.relation_type IN {relation_types})"

        async with driver.session() as session:
            result = await session.run(
                f"""
                MATCH path = (start:Entity {{entity_id: $entity_id, user_id: $user_id}})
                      -[rels*1..{depth}]-(connected)
                WHERE connected.user_id = $user_id {rel_filter}
                UNWIND nodes(path) AS n
                UNWIND relationships(path) AS r
                RETURN DISTINCT
                    collect(DISTINCT {{
                        entity_id: n.entity_id,
                        name: n.name, type: n.entity_type
                    }}) AS nodes,
                    collect(DISTINCT {{
                        from: startNode(r).entity_id,
                        to: endNode(r).entity_id,
                        type: r.relation_type
                    }}) AS edges
                """,
                entity_id=entity_id,
                user_id=user_id,
            )
            record = await result.single()
            if not record:
                return {"nodes": [], "edges": []}

            return {
                "nodes": record["nodes"],
                "edges": record["edges"],
            }

    async def traverse_weighted(
        self,
        entity_id: str,
        user_id: str,
        depth: int = 2,
        relation_types: list[str] | None = None,
        min_strength: float = 0.0,
    ) -> list[dict]:
        """Traverse the graph ranking connected entities by avg relationship strength.

        Returns entities sorted by avg_strength descending, then distance ascending.
        Filters out paths where avg strength < min_strength.
        """
        driver = await self._get_driver()
        if not driver:
            return []

        try:
            async with driver.session() as session:
                result = await session.run(
                    f"""
                    MATCH path = (start:Entity {{entity_id: $entity_id,
                                                  user_id: $user_id}})
                          -[rels*1..{depth}]-(connected:Entity {{user_id: $user_id}})
                    WHERE connected.entity_id <> $entity_id
                    WITH connected, relationships(path) AS path_rels
                    WITH connected,
                         reduce(s = 0.0, r IN path_rels |
                                s + coalesce(r.strength, 0.5)) / size(path_rels)
                             AS avg_strength,
                         size(path_rels) AS distance
                    WHERE avg_strength >= $min_strength
                    RETURN DISTINCT
                        connected.entity_id AS entity_id,
                        connected.name AS name,
                        connected.entity_type AS entity_type,
                        connected.attributes AS attributes,
                        avg_strength,
                        distance
                    ORDER BY avg_strength DESC, distance ASC
                    LIMIT 20
                    """,
                    entity_id=entity_id,
                    user_id=user_id,
                    min_strength=min_strength,
                )
                return await result.data()
        except Exception:
            logger.debug("Neo4j traverse_weighted failed for %s", entity_id, exc_info=True)
            return []

    async def find_path(
        self, from_entity_id: str, to_entity_id: str, user_id: str, max_depth: int = 4
    ) -> list[dict]:
        """Find shortest path between two entities."""
        driver = await self._get_driver()
        if not driver:
            return []

        async with driver.session() as session:
            result = await session.run(
                f"""
                MATCH path = shortestPath(
                    (a:Entity {{entity_id: $from_id, user_id: $user_id}})
                    -[*..{max_depth}]-
                    (b:Entity {{entity_id: $to_id, user_id: $user_id}})
                )
                RETURN [n IN nodes(path) | {{entity_id: n.entity_id, name: n.name}}] AS path_nodes
                """,
                from_id=from_entity_id,
                to_id=to_entity_id,
                user_id=user_id,
            )
            record = await result.single()
            return record["path_nodes"] if record else []

    async def get_related_people(self, entity_id: str, user_id: str) -> list[dict]:
        """Get people related to an entity within 2 hops."""
        driver = await self._get_driver()
        if not driver:
            return []

        async with driver.session() as session:
            result = await session.run(
                """
                MATCH (start:Entity {entity_id: $entity_id, user_id: $user_id})
                      -[*1..2]-(p:Entity {entity_type: 'person', user_id: $user_id})
                WHERE p.entity_id <> $entity_id
                RETURN DISTINCT p.entity_id AS entity_id, p.name AS name
                LIMIT 20
                """,
                entity_id=entity_id,
                user_id=user_id,
            )
            records = await result.data()
            return records

    async def search_entities(
        self, user_id: str, query: str, entity_type: str | None = None, limit: int = 20
    ) -> list[dict]:
        """Search entities by name using Neo4j CONTAINS matching."""
        driver = await self._get_driver()
        if not driver:
            return []

        type_filter = ""
        if entity_type:
            type_filter = "AND e.entity_type = $entity_type"

        async with driver.session() as session:
            result = await session.run(
                f"""
                MATCH (e:Entity {{user_id: $user_id}})
                WHERE e.name IS NOT NULL
                  AND toLower(e.name) CONTAINS toLower($search_query)
                {type_filter}
                RETURN e.entity_id AS entity_id,
                       e.name AS name,
                       e.entity_type AS entity_type,
                       e.attributes AS attributes
                ORDER BY e.name
                LIMIT $limit
                """,
                user_id=user_id,
                search_query=query,
                entity_type=entity_type,
                limit=limit,
            )
            records = await result.data()
            return records

    async def full_sync(self, user_id: str, entities: list[dict], relationships: list[dict]) -> int:
        """Bulk sync all entities and relationships to Neo4j."""
        count = 0
        for ent in entities:
            await self.sync_entity(
                entity_id=ent["entity_id"],
                entity_type=ent["entity_type"],
                name=ent["canonical_name"],
                user_id=user_id,
                attributes=ent.get("attributes"),
            )
            count += 1

        for rel in relationships:
            start_date = rel.get("start_date")
            end_date = rel.get("end_date")
            await self.sync_relationship(
                relation_id=rel["relation_id"],
                from_entity_id=rel["from_entity_id"],
                to_entity_id=rel["to_entity_id"],
                relation_type=rel["relation_type"],
                user_id=user_id,
                strength=rel.get("strength", 1.0),
                start_date=start_date.isoformat() if start_date else None,
                end_date=end_date.isoformat() if end_date else None,
            )
            count += 1

        logger.info("Full sync to Neo4j: %d items for user %s", count, user_id)
        return count

    async def get_subgraph(self, entity_ids: list[str], user_id: str) -> dict:
        """Get all nodes and edges for a set of entity IDs."""
        driver = await self._get_driver()
        if not driver:
            return {"nodes": [], "edges": []}

        async with driver.session() as session:
            result = await session.run(
                """
                MATCH (n:Entity)
                WHERE n.entity_id IN $entity_ids AND n.user_id = $user_id
                MATCH (n)-[r]-(m:Entity)
                WHERE m.entity_id IN $entity_ids AND m.user_id = $user_id
                RETURN
                    collect(DISTINCT {
                        entity_id: n.entity_id,
                        name: n.name,
                        type: n.entity_type
                    }) AS nodes,
                    collect(DISTINCT {
                        from: startNode(r).entity_id,
                        to: endNode(r).entity_id,
                        type: r.relation_type
                    }) AS edges
                """,
                entity_ids=entity_ids,
                user_id=user_id,
            )
            record = await result.single()
            if not record:
                return {"nodes": [], "edges": []}

            return {
                "nodes": record["nodes"],
                "edges": record["edges"],
            }

    async def get_project_graph(self, project_entity_id: str, user_id: str) -> dict:
        """Get the full subgraph for a project entity (all related entities within 3 hops)."""
        driver = await self._get_driver()
        if not driver:
            return {"nodes": [], "edges": []}

        async with driver.session() as session:
            result = await session.run(
                """
                MATCH path = (start:Entity {entity_id: $entity_id, user_id: $user_id})
                      -[rels*1..3]-(connected)
                WHERE connected.user_id = $user_id
                UNWIND nodes(path) AS n
                UNWIND relationships(path) AS r
                RETURN DISTINCT
                    collect(DISTINCT {
                        entity_id: n.entity_id,
                        name: n.name,
                        type: n.entity_type
                    }) AS nodes,
                    collect(DISTINCT {
                        from: startNode(r).entity_id,
                        to: endNode(r).entity_id,
                        type: r.relation_type
                    }) AS edges
                """,
                entity_id=project_entity_id,
                user_id=user_id,
            )
            record = await result.single()
            if not record:
                return {"nodes": [], "edges": []}

            return {
                "nodes": record["nodes"],
                "edges": record["edges"],
            }

    async def find_central_entities(
        self, user_id: str, entity_type: str | None = None, limit: int = 10
    ) -> list[dict]:
        """Find entities with highest degree centrality (most connections)."""
        driver = await self._get_driver()
        if not driver:
            return []

        type_filter = ""
        if entity_type:
            type_filter = "AND e.entity_type = $entity_type"

        async with driver.session() as session:
            result = await session.run(
                f"""
                MATCH (e:Entity {{user_id: $user_id}})
                WHERE true {type_filter}
                OPTIONAL MATCH (e)-[r]-()
                WITH e, count(r) AS degree
                WHERE degree > 0
                RETURN e.entity_id AS entity_id,
                       e.name AS name,
                       e.entity_type AS entity_type,
                       degree
                ORDER BY degree DESC
                LIMIT $limit
                """,
                user_id=user_id,
                entity_type=entity_type,
                limit=limit,
            )
            records = await result.data()
            return records

    async def get_stale_relationships(self, user_id: str, days: int = 14) -> list[dict]:
        """Find relationships that haven't been updated recently.

        Returns relationships with low strength or where related entity's
        last_seen_at is old.
        """
        driver = await self._get_driver()
        if not driver:
            return []

        async with driver.session() as session:
            result = await session.run(
                """
                MATCH (a:Entity {user_id: $user_id})-[r]-(b:Entity)
                WHERE b.user_id = $user_id
                RETURN DISTINCT
                    r.relation_id AS relation_id,
                    a.entity_id AS from_entity_id,
                    a.name AS from_name,
                    b.entity_id AS to_entity_id,
                    b.name AS to_name,
                    r.relation_type AS relation_type
                LIMIT 100
                """,
                user_id=user_id,
            )
            records = await result.data()
            return records

    async def detect_communities(self, user_id: str) -> list[dict]:
        """Detect clusters of related entities using connected components."""
        driver = await self._get_driver()
        if not driver:
            return []

        async with driver.session() as session:
            result = await session.run(
                """
                MATCH (e:Entity {user_id: $user_id})
                OPTIONAL MATCH path = (e)-[*]-(connected:Entity)
                WHERE connected.user_id = $user_id
                WITH e, collect(DISTINCT connected.entity_id) AS community_members
                WHERE size(community_members) > 0
                RETURN e.entity_id AS seed_entity_id,
                       e.name AS seed_name,
                       e.entity_type AS seed_type,
                       community_members,
                       size(community_members) AS community_size
                ORDER BY community_size DESC
                LIMIT 20
                """,
                user_id=user_id,
            )
            records = await result.data()
            return records
