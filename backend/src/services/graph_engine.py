"""Neo4j-backed entity graph traversal and query engine.

Neo4j is a read-optimized projection synced from Postgres Entity/EntityRelationship
tables. Postgres remains source of truth; Neo4j enables multi-hop traversals,
path finding, and community detection.
"""

import json
import logging
import time
from datetime import datetime, timezone

from src.config.settings import Settings
from src.errors import classify
from src.services.world_model import RELATION_TYPES

logger = logging.getLogger(__name__)


class _Neo4jCircuit:
    """Simple circuit breaker for Neo4j connections."""

    FAILURE_THRESHOLD = 5
    COOLDOWN_SECONDS = 120

    def __init__(self) -> None:
        self._failures = 0
        self._state = "closed"  # closed, open, half_open
        self._opened_at: float = 0

    def record_success(self) -> None:
        self._failures = 0
        self._state = "closed"

    def record_failure(self) -> None:
        self._failures += 1
        if self._failures >= self.FAILURE_THRESHOLD:
            self._state = "open"
            self._opened_at = time.monotonic()
            logger.warning("neo4j_circuit_opened after %d consecutive failures", self._failures)

    def allow_request(self) -> bool:
        if self._state == "closed":
            return True
        if self._state == "open":
            if time.monotonic() - self._opened_at >= self.COOLDOWN_SECONDS:
                self._state = "half_open"
                logger.info("neo4j_circuit_half_open: allowing probe request")
                return True
            return False
        return True  # half_open: allow one probe


class GraphEngine:
    """Neo4j-backed entity graph traversal."""

    def __init__(self, settings: Settings):
        self._settings = settings
        self._driver = None
        self._circuit = _Neo4jCircuit()
        self._metrics: dict = {
            "sync_success": 0,
            "sync_failure": 0,
            "last_failure_at": None,
            "last_failure_error": None,
        }

    async def _get_driver(self):
        """Lazy-init Neo4j driver."""
        if self._driver is None:
            if not self._settings.neo4j_url:
                logger.warning("Neo4j not configured, graph engine is no-op")
                return None
            from neo4j import AsyncGraphDatabase, NotificationDisabledClassification

            self._driver = AsyncGraphDatabase.driver(
                self._settings.neo4j_url,
                auth=(self._settings.neo4j_user, self._settings.neo4j_password),
                # Several traversal/temporal queries intentionally reference
                # optional relationship properties (e.g. ``r.start_date IS NULL
                # OR ...``). Neo4j emits a benign 01N52 "property key does not
                # exist" notification (classification UNRECOGNIZED) for each,
                # which floods the logs. The IS NULL branch handles absent
                # properties correctly, so suppress just that classification.
                notifications_disabled_classifications=[
                    NotificationDisabledClassification.UNRECOGNIZED
                ],
            )
        return self._driver

    async def health(self) -> dict:
        """Lightweight health check — runs RETURN 1."""
        if not self._settings.neo4j_url:
            return {"status": "disabled", "configured": False}
        try:
            driver = await self._get_driver()
            if not driver:
                return {"status": "unreachable", "configured": True}
            async with driver.session() as session:
                await session.run("RETURN 1")
            return {
                "status": "healthy",
                "configured": True,
                "circuit_state": self._circuit._state,
            }
        except Exception as exc:
            # /v1/health/stores is a PUBLIC endpoint — surface only the safe
            # message + code, never the raw Neo4j exception (may carry the URL).
            logger.warning("Neo4j health check failed: %s", exc, exc_info=True)
            code, message, _ = classify(exc)
            return {
                "status": "unreachable",
                "configured": True,
                "error": message,
                "error_code": code,
            }

    def get_metrics(self) -> dict:
        return {**self._metrics, "circuit_state": self._circuit._state}

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
        if not self._circuit.allow_request():
            logger.debug("neo4j_circuit_open: skipping delete_entity for %s", entity_id)
            return

        try:
            async with driver.session() as session:
                await session.run(
                    "MATCH (e:Entity {entity_id: $entity_id}) DETACH DELETE e",
                    entity_id=entity_id,
                )
            self._circuit.record_success()
        except Exception:
            self._circuit.record_failure()
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
        if not self._circuit.allow_request():
            logger.debug("neo4j_circuit_open: skipping sync_entity for %s", entity_id)
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
            self._circuit.record_success()
            self._metrics["sync_success"] += 1
        except Exception as exc:
            self._circuit.record_failure()
            self._metrics["sync_failure"] += 1
            self._metrics["last_failure_at"] = datetime.now(timezone.utc).isoformat()
            self._metrics["last_failure_error"] = str(exc)[:200]
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

        if relation_type not in RELATION_TYPES:
            raise ValueError(
                f"Invalid relation_type {relation_type!r}; must be one of {sorted(RELATION_TYPES)}"
            )

        if not self._circuit.allow_request():
            logger.debug("neo4j_circuit_open: skipping sync_relationship for %s", relation_id)
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
            self._circuit.record_success()
            self._metrics["sync_success"] += 1
        except Exception as exc:
            self._circuit.record_failure()
            self._metrics["sync_failure"] += 1
            self._metrics["last_failure_at"] = datetime.now(timezone.utc).isoformat()
            self._metrics["last_failure_error"] = str(exc)[:200]
            logger.warning("Neo4j sync_relationship failed for %s", relation_id, exc_info=True)

    async def traverse(
        self, entity_id: str, user_id: str, relation_types: list[str] | None = None, depth: int = 2
    ) -> dict:
        """Traverse the graph from an entity. Returns nodes and edges."""
        driver = await self._get_driver()
        if not driver:
            return {"nodes": [], "edges": []}
        if not self._circuit.allow_request():
            logger.debug("neo4j_circuit_open: skipping traverse for %s", entity_id)
            return {"nodes": [], "edges": []}

        rel_filter = ""
        params: dict = {"entity_id": entity_id, "user_id": user_id}
        if relation_types:
            invalid = set(relation_types) - RELATION_TYPES
            if invalid:
                raise ValueError(f"Invalid relation_types: {invalid}")
            rel_filter = "AND ALL(r IN rels WHERE r.relation_type IN $types)"
            params["types"] = relation_types

        try:
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
                    **params,
                )
                record = await result.single()
                if not record:
                    self._circuit.record_success()
                    return {"nodes": [], "edges": []}

                self._circuit.record_success()
                return {
                    "nodes": record["nodes"],
                    "edges": record["edges"],
                }
        except Exception:
            self._circuit.record_failure()
            logger.warning("Neo4j traverse failed for %s", entity_id, exc_info=True)
            return {"nodes": [], "edges": []}

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
        if not self._circuit.allow_request():
            logger.debug("neo4j_circuit_open: skipping traverse_weighted for %s", entity_id)
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
                data = await result.data()
            self._circuit.record_success()
            return data
        except Exception:
            self._circuit.record_failure()
            logger.warning("Neo4j traverse_weighted failed for %s", entity_id, exc_info=True)
            return []

    async def find_path(
        self, from_entity_id: str, to_entity_id: str, user_id: str, max_depth: int = 4
    ) -> list[dict]:
        """Find shortest path between two entities."""
        driver = await self._get_driver()
        if not driver:
            return []
        if not self._circuit.allow_request():
            logger.debug(
                "neo4j_circuit_open: skipping find_path for %s -> %s",
                from_entity_id,
                to_entity_id,
            )
            return []

        try:
            async with driver.session() as session:
                result = await session.run(
                    f"""
                    MATCH path = shortestPath(
                        (a:Entity {{entity_id: $from_id, user_id: $user_id}})
                        -[*..{max_depth}]-
                        (b:Entity {{entity_id: $to_id, user_id: $user_id}})
                    )
                    RETURN [n IN nodes(path) |
                        {{entity_id: n.entity_id, name: n.name}}
                    ] AS path_nodes
                    """,
                    from_id=from_entity_id,
                    to_id=to_entity_id,
                    user_id=user_id,
                )
                record = await result.single()
                data = record["path_nodes"] if record else []
            self._circuit.record_success()
            return data
        except Exception:
            self._circuit.record_failure()
            logger.warning(
                "Neo4j find_path failed for %s -> %s", from_entity_id, to_entity_id, exc_info=True
            )
            return []

    async def get_related_people(self, entity_id: str, user_id: str) -> list[dict]:
        """Get people related to an entity within 2 hops."""
        driver = await self._get_driver()
        if not driver:
            return []
        if not self._circuit.allow_request():
            logger.debug("neo4j_circuit_open: skipping get_related_people for %s", entity_id)
            return []

        try:
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
            self._circuit.record_success()
            return records
        except Exception:
            self._circuit.record_failure()
            logger.warning("Neo4j get_related_people failed for %s", entity_id, exc_info=True)
            return []

    async def search_entities(
        self, user_id: str, query: str, entity_type: str | None = None, limit: int = 20
    ) -> list[dict]:
        """Search entities by name using Neo4j CONTAINS matching."""
        driver = await self._get_driver()
        if not driver:
            return []
        if not self._circuit.allow_request():
            logger.debug("neo4j_circuit_open: skipping search_entities for user %s", user_id)
            return []

        type_filter = ""
        if entity_type:
            type_filter = "AND e.entity_type = $entity_type"

        try:
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
            self._circuit.record_success()
            return records
        except Exception:
            self._circuit.record_failure()
            logger.warning("Neo4j search_entities failed for user %s", user_id, exc_info=True)
            return []

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
        last_seen_at is old. Filters by the `days` parameter using start_date.
        """
        driver = await self._get_driver()
        if not driver:
            return []
        if not self._circuit.allow_request():
            logger.debug("neo4j_circuit_open: skipping get_stale_relationships for %s", user_id)
            return []

        from datetime import timedelta

        cutoff = (datetime.now(timezone.utc) - timedelta(days=days)).isoformat()

        try:
            async with driver.session() as session:
                result = await session.run(
                    """
                    MATCH (a:Entity {user_id: $user_id})-[r]-(b:Entity)
                    WHERE b.user_id = $user_id
                      AND (r.start_date IS NULL OR r.start_date <= $cutoff_date)
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
                    cutoff_date=cutoff,
                )
                records = await result.data()
            self._circuit.record_success()
            return records
        except Exception:
            self._circuit.record_failure()
            logger.warning("Neo4j get_stale_relationships failed for %s", user_id, exc_info=True)
            return []

    async def detect_communities(self, user_id: str) -> list[dict]:
        """Detect clusters of related entities using connected components."""
        driver = await self._get_driver()
        if not driver:
            return []
        if not self._circuit.allow_request():
            logger.debug("neo4j_circuit_open: skipping detect_communities for %s", user_id)
            return []

        try:
            async with driver.session() as session:
                result = await session.run(
                    """
                    MATCH (e:Entity {user_id: $user_id})
                    OPTIONAL MATCH path = (e)-[*1..3]-(connected:Entity)
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
            self._circuit.record_success()
            return records
        except Exception:
            self._circuit.record_failure()
            logger.warning("Neo4j detect_communities failed for %s", user_id, exc_info=True)
            return []

    async def traverse_temporal(
        self,
        entity_id: str,
        user_id: str,
        after: str | None = None,
        before: str | None = None,
        depth: int = 2,
    ) -> list[dict]:
        """Traverse the graph scoped to a time window.

        Filters relationships by start_date within [after, before].
        Relationships with NULL start_date are included (no temporal info).
        """
        driver = await self._get_driver()
        if not driver:
            return []
        if not self._circuit.allow_request():
            logger.debug("neo4j_circuit_open: skipping traverse_temporal for %s", entity_id)
            return []

        temporal_filter = ""
        if after:
            temporal_filter += (
                " AND ALL(r IN rels WHERE r.start_date IS NULL OR r.start_date >= $after)"
            )
        if before:
            temporal_filter += (
                " AND ALL(r IN rels WHERE r.start_date IS NULL OR r.start_date <= $before)"
            )

        try:
            async with driver.session() as session:
                result = await session.run(
                    f"""
                    MATCH path = (start:Entity {{entity_id: $entity_id,
                                                  user_id: $user_id}})
                          -[rels*1..{depth}]-(connected:Entity {{user_id: $user_id}})
                    WHERE connected.entity_id <> $entity_id
                    {temporal_filter}
                    UNWIND rels AS r
                    RETURN DISTINCT
                        connected.entity_id AS entity_id,
                        connected.name AS name,
                        connected.entity_type AS entity_type,
                        r.relation_type AS relation_type,
                        r.strength AS strength
                    LIMIT 20
                    """,
                    entity_id=entity_id,
                    user_id=user_id,
                    after=after,
                    before=before,
                )
                data = await result.data()
            self._circuit.record_success()
            return data
        except Exception:
            self._circuit.record_failure()
            logger.warning("Neo4j traverse_temporal failed for %s", entity_id, exc_info=True)
            return []
