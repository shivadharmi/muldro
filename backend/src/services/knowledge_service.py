"""KnowledgeService — orchestrates GraphEngine + Postgres for knowledge page endpoints.

Thin orchestration layer that serves 4 knowledge page endpoints:
- Initial graph (seed nodes + edges + stats)
- Paginated memories with filters
- Memory detail with linked entities and provenance
- Aggregated stats (counts, deltas, communities, stale relationships)
"""

import logging
import math
from datetime import datetime, timedelta, timezone

from sqlalchemy import func, select
from sqlalchemy.ext.asyncio import AsyncSession

from src.config.settings import Settings
from src.models.entities import Entity, EntityAlias, EntityRelationship
from src.models.events import NormalizedEvent
from src.models.memory import Memory
from src.services.graph_engine import GraphEngine

logger = logging.getLogger(__name__)

# Memory types that surface as descriptive "fact" cards (preference handled separately).
_FACT_MEMORY_TYPES = ("semantic", "relationship")
_LABEL_MAX_LEN = 48


def _derive_label(fact_text: str | None) -> str:
    """Derive a short card label from a memory's fact_text (pure helper).

    Takes the first sentence/clause and truncates to ~48 chars. Falls back to
    a generic label when fact_text is empty.
    """
    text = (fact_text or "").strip()
    if not text:
        return "Untitled"
    # First sentence — split on terminal punctuation.
    for sep in (". ", "? ", "! ", "\n"):
        idx = text.find(sep)
        if idx != -1:
            text = text[:idx]
            break
    text = text.strip().rstrip(".?!")
    if len(text) <= _LABEL_MAX_LEN:
        return text
    return text[: _LABEL_MAX_LEN - 1].rstrip() + "…"


def _normalize_source_event_ids(raw: object) -> list[str]:
    """Coerce a memory's source_event_ids (dict or list) into a flat list of IDs."""
    if isinstance(raw, dict):
        return [str(v) for v in raw.values() if v]
    if isinstance(raw, list):
        return [str(v) for v in raw if v]
    return []


def _entity_card_kind(entity_type: str) -> str | None:
    """Map an entity_type to a knowledge-card kind, or None if it has no card."""
    if entity_type == "person":
        return "person"
    if entity_type in ("project", "initiative"):
        return "project"
    return None


def _entity_sources(entity: "Entity") -> list[str]:
    """Best-effort source-system slugs for an entity from its source_refs.

    source_refs is a list of dicts that may carry a ``source`` key. Returns a
    de-duplicated, order-preserving list; empty when attribution is unknown.
    """
    refs = entity.source_refs
    if not isinstance(refs, list):
        return []
    sources: list[str] = []
    for ref in refs:
        if isinstance(ref, dict):
            src = ref.get("source")
            if src and src not in sources:
                sources.append(str(src))
    return sources


class KnowledgeService:
    """Orchestrates GraphEngine (Neo4j) + Postgres for knowledge page queries."""

    def __init__(
        self, settings: Settings, db: AsyncSession, graph_engine: GraphEngine | None = None
    ):
        self._settings = settings
        self._db = db
        self._graph = graph_engine

    async def close(self) -> None:
        """Release GraphEngine resources (no-op when no graph engine was attached)."""
        if self._graph is not None:
            await self._graph.close()

    async def __aenter__(self) -> "KnowledgeService":
        return self

    async def __aexit__(self, exc_type, exc_val, exc_tb) -> None:
        await self.close()

    async def get_initial_graph(
        self,
        user_id: str,
        workspace_id: str,
    ) -> dict:
        """Return seed nodes with enriched Postgres data, edges, and stats.

        Calls GraphEngine.find_central_entities for seed nodes, then
        GraphEngine.get_subgraph for edges. Enriches nodes with full
        Postgres entity data (attributes, aliases, importance_score, etc.).
        """
        central = await self._graph.find_central_entities(user_id=user_id, limit=10)
        entity_ids = [n["entity_id"] for n in central]

        if not entity_ids:
            total_entities = await self._count_entities(user_id, workspace_id)
            total_relationships = await self._count_relationships(user_id, workspace_id)
            return {
                "nodes": [],
                "edges": [],
                "stats": {
                    "total_entities": total_entities,
                    "total_relationships": total_relationships,
                },
            }

        subgraph = await self._graph.get_subgraph(entity_ids=entity_ids, user_id=user_id)

        enriched_nodes = await self._enrich_nodes(entity_ids, workspace_id, central)

        total_entities = await self._count_entities(user_id, workspace_id)
        total_relationships = await self._count_relationships(user_id, workspace_id)

        return {
            "nodes": enriched_nodes,
            "edges": subgraph.get("edges", []),
            "stats": {
                "total_entities": total_entities,
                "total_relationships": total_relationships,
            },
        }

    async def get_memories_paginated(
        self,
        user_id: str,
        workspace_id: str,
        *,
        memory_type: str | None = None,
        sort_by: str = "created_at",
        search: str | None = None,
        entity_id: str | None = None,
        page: int = 1,
        limit: int = 20,
    ) -> dict:
        """Query memories with filters, pagination, and sort.

        Returns { items, total, page, pages }.
        """
        page = max(1, page)
        limit = max(1, min(limit, 100))

        base_conditions = [
            Memory.user_id == user_id,
            Memory.workspace_id == workspace_id,
            Memory.status == "active",
        ]

        if memory_type:
            base_conditions.append(Memory.memory_type == memory_type)

        if search:
            base_conditions.append(Memory.fact_text.ilike(f"%{search}%"))

        if entity_id:
            base_conditions.append(Memory.entity_ids.any(entity_id))

        # Count query
        count_stmt = select(func.count(Memory.memory_id)).where(*base_conditions)
        total = (await self._db.execute(count_stmt)).scalar() or 0

        # Sort
        sort_column = self._resolve_memory_sort(sort_by)

        # Data query
        offset = (page - 1) * limit
        data_stmt = (
            select(Memory)
            .where(*base_conditions)
            .order_by(sort_column.desc())
            .offset(offset)
            .limit(limit)
        )
        result = await self._db.execute(data_stmt)
        memories = result.scalars().all()

        # Collect all entity_ids across memories for name resolution
        all_entity_ids: set[str] = set()
        for mem in memories:
            if mem.entity_ids:
                all_entity_ids.update(mem.entity_ids)

        entity_name_map = await self._resolve_entity_names(all_entity_ids, workspace_id)

        # Batch-resolve provenance source slugs for the whole page in ONE query
        # (no N+1), reusing the same approach as the knowledge-cards path.
        per_memory_event_ids = {
            mem.memory_id: _normalize_source_event_ids(mem.source_event_ids) for mem in memories
        }
        all_event_ids: set[str] = set()
        for ids in per_memory_event_ids.values():
            all_event_ids.update(ids)
        event_source_map = await self._resolve_event_sources(all_event_ids, workspace_id)

        items = [
            self._memory_to_dict(
                mem,
                entity_name_map,
                self._memory_sources(per_memory_event_ids.get(mem.memory_id, []), event_source_map),
            )
            for mem in memories
        ]

        pages = max(1, math.ceil(total / limit))

        return {
            "items": items,
            "total": total,
            "page": page,
            "pages": pages,
        }

    async def get_memory_detail(
        self,
        memory_id: str,
        user_id: str,
        workspace_id: str,
    ) -> dict | None:
        """Return full memory with linked entities and provenance events.

        Returns detail dict or None if not found.
        """
        stmt = select(Memory).where(
            Memory.memory_id == memory_id,
            Memory.user_id == user_id,
            Memory.workspace_id == workspace_id,
        )
        result = await self._db.execute(stmt)
        memory = result.scalar_one_or_none()
        if not memory:
            return None

        # Resolve linked entities
        linked_entities: list[dict] = []
        if memory.entity_ids:
            entity_stmt = select(Entity).where(
                Entity.entity_id.in_(memory.entity_ids),
                Entity.workspace_id == workspace_id,
            )
            entity_result = await self._db.execute(entity_stmt)
            entities = entity_result.scalars().all()
            linked_entities = [
                {
                    "entity_id": e.entity_id,
                    "entity_type": e.entity_type,
                    "canonical_name": e.canonical_name,
                    "importance_score": e.importance_score,
                }
                for e in entities
            ]

        # Resolve provenance
        raw_source = memory.source_event_ids
        if isinstance(raw_source, dict):
            source_event_ids = list(raw_source.values())
        elif isinstance(raw_source, list):
            source_event_ids = raw_source
        else:
            source_event_ids = []

        source_description: str | None = None
        if source_event_ids:
            event_stmt = select(NormalizedEvent).where(
                NormalizedEvent.event_id.in_(source_event_ids),
                NormalizedEvent.workspace_id == workspace_id,
            )
            event_result = await self._db.execute(event_stmt)
            events = event_result.scalars().all()
            if events:
                parts = [f"{ev.source}: {ev.title}" for ev in events]
                source_description = "Extracted from " + "; ".join(parts)

        provenance = {
            "source_event_ids": source_event_ids or [],
            "source_description": source_description,
        }

        return {
            "memory_id": memory.memory_id,
            "memory_type": memory.memory_type,
            "scope": memory.scope,
            "fact_text": memory.fact_text,
            "confidence": memory.confidence,
            "stability_score": memory.stability_score,
            "status": memory.status,
            "refresh_count": memory.refresh_count,
            "last_accessed_at": (
                memory.last_accessed_at.isoformat() if memory.last_accessed_at else None
            ),
            "created_at": memory.created_at.isoformat() if memory.created_at else None,
            "updated_at": memory.updated_at.isoformat() if memory.updated_at else None,
            "entity_ids": memory.entity_ids or [],
            "linked_entities": linked_entities,
            "provenance": provenance,
        }

    async def get_stats(
        self,
        user_id: str,
        workspace_id: str,
    ) -> dict:
        """Aggregate stats for the knowledge page.

        Returns entity/memory counts by type, weekly deltas, central entities,
        communities, stale relationships, and growth by day (last 7 days).
        """
        entity_counts_raw = await self._entity_counts_by_type(user_id, workspace_id)
        memory_counts_raw = await self._memory_counts_by_type(user_id, workspace_id)

        now = datetime.now(timezone.utc)
        week_ago = now - timedelta(days=7)

        entity_weekly_delta = await self._count_entities_since(user_id, workspace_id, week_ago)
        memory_weekly_delta = await self._count_memories_since(user_id, workspace_id, week_ago)
        relationship_weekly_delta = await self._count_relationships_since(
            user_id, workspace_id, week_ago
        )
        avg_confidence = await self._avg_confidence(user_id, workspace_id)
        total_entities = await self._count_entities(user_id, workspace_id)
        total_relationships = await self._count_relationships(user_id, workspace_id)
        total_memories = await self._count_total_memories(user_id, workspace_id)

        central_entities = await self._graph.find_central_entities(user_id=user_id, limit=5)
        all_communities = await self._graph.detect_communities(user_id=user_id)
        communities = all_communities[:4]
        stale_relationships = await self._graph.get_stale_relationships(user_id=user_id, days=14)

        growth_by_day = await self._growth_by_day(user_id, workspace_id, days=7)

        # Convert count dicts to frontend-expected array-of-objects format
        entity_counts_by_type = [
            {"entity_type": t, "count": c} for t, c in entity_counts_raw.items()
        ]
        memory_counts_by_type = [
            {"memory_type": t, "count": c} for t, c in memory_counts_raw.items()
        ]

        return {
            "entity_counts_by_type": entity_counts_by_type,
            "memory_counts_by_type": memory_counts_by_type,
            "weekly_delta": {
                "entities": entity_weekly_delta,
                "relationships": relationship_weekly_delta,
                "memories": memory_weekly_delta,
            },
            "total_memories": total_memories,
            "avg_confidence": avg_confidence,
            "total_entities": total_entities,
            "total_relationships": total_relationships,
            "central_entities": central_entities,
            "communities": communities,
            "stale_relationships": stale_relationships,
            "growth_by_day": growth_by_day,
        }

    async def get_knowledge_cards(
        self,
        user_id: str,
        workspace_id: str,
        *,
        limit: int = 50,
    ) -> list[dict]:
        """Unified knowledge-card feed drawn from BOTH entities and memories.

        Card shape: {id, kind, label, desc, sources}.

        Kind mapping:
        - entity person -> person; entity project/initiative -> project
        - memory preference -> preference; memory semantic/relationship -> fact

        Sources:
        - entities: best-effort from Entity.source_refs (empty when unknown)
        - memories: source_event_ids resolved (batched) to originating event
          source systems (gmail/slack/notion/...). Empty when unresolvable.
        """
        limit = max(1, min(limit, 100))

        entity_cards = await self._entity_cards(user_id, workspace_id, limit)
        memory_cards = await self._memory_cards(user_id, workspace_id, limit)

        # Interleave by simple recency-agnostic merge then cap at limit. Entities
        # first (curated graph), then memories — both already individually limited.
        return (entity_cards + memory_cards)[:limit]

    async def _entity_cards(self, user_id: str, workspace_id: str, limit: int) -> list[dict]:
        """Build person/project cards from entities."""
        stmt = (
            select(Entity)
            .where(
                Entity.user_id == user_id,
                Entity.workspace_id == workspace_id,
                Entity.entity_type.in_(("person", "project", "initiative")),
            )
            .order_by(Entity.importance_score.desc(), Entity.last_seen_at.desc())
            .limit(limit)
        )
        result = await self._db.execute(stmt)
        entities = result.scalars().all()

        cards: list[dict] = []
        for e in entities:
            kind = _entity_card_kind(e.entity_type)
            if kind is None:
                continue
            attrs = e.attributes or {}
            desc = attrs.get("role") or attrs.get("summary") or e.entity_type
            cards.append(
                {
                    "id": e.entity_id,
                    "kind": kind,
                    "label": e.canonical_name,
                    "desc": desc,
                    "sources": _entity_sources(e),
                }
            )
        return cards

    async def _memory_cards(self, user_id: str, workspace_id: str, limit: int) -> list[dict]:
        """Build preference/fact cards from descriptive memories."""
        card_types = ("preference", *_FACT_MEMORY_TYPES)
        stmt = (
            select(Memory)
            .where(
                Memory.user_id == user_id,
                Memory.workspace_id == workspace_id,
                Memory.status == "active",
                Memory.memory_type.in_(card_types),
            )
            .order_by(Memory.stability_score.desc(), Memory.created_at.desc())
            .limit(limit)
        )
        result = await self._db.execute(stmt)
        memories = result.scalars().all()

        # Batch-resolve all referenced event IDs -> source slug in ONE query (no N+1).
        per_memory_event_ids = {
            m.memory_id: _normalize_source_event_ids(m.source_event_ids) for m in memories
        }
        all_event_ids: set[str] = set()
        for ids in per_memory_event_ids.values():
            all_event_ids.update(ids)

        event_source_map = await self._resolve_event_sources(all_event_ids, workspace_id)

        cards: list[dict] = []
        for m in memories:
            kind = "preference" if m.memory_type == "preference" else "fact"
            cards.append(
                {
                    "id": m.memory_id,
                    "kind": kind,
                    "label": _derive_label(m.fact_text),
                    "desc": m.fact_text,
                    "sources": self._memory_sources(
                        per_memory_event_ids.get(m.memory_id, []), event_source_map
                    ),
                }
            )
        return cards

    @staticmethod
    def _memory_sources(event_ids: list[str], event_source_map: dict[str, str]) -> list[str]:
        """De-duplicated, order-preserving source slugs for one memory.

        Maps a memory's normalized event IDs through the batched event→source
        map. Empty when none resolve. Shared by the memories-list and cards
        paths to keep provenance resolution DRY.
        """
        sources: list[str] = []
        for eid in event_ids:
            src = event_source_map.get(eid)
            if src and src not in sources:
                sources.append(src)
        return sources

    async def _resolve_event_sources(
        self, event_ids: set[str], workspace_id: str
    ) -> dict[str, str]:
        """Map event_id -> source slug for a set of event IDs (single query)."""
        if not event_ids:
            return {}
        stmt = select(NormalizedEvent.event_id, NormalizedEvent.source).where(
            NormalizedEvent.event_id.in_(list(event_ids)),
            NormalizedEvent.workspace_id == workspace_id,
        )
        result = await self._db.execute(stmt)
        return {row.event_id: row.source for row in result.all()}

    # ── Private helpers ──────────────────────────────────────────────────

    async def _enrich_nodes(
        self,
        entity_ids: list[str],
        workspace_id: str,
        neo4j_nodes: list[dict] | None = None,
    ) -> list[dict]:
        """Fetch full Postgres entity data + aliases for the given IDs.

        Iterates over the input entity_ids list so that nodes present in Neo4j
        but missing from Postgres still appear with fallback data (name, type).
        """
        if not entity_ids:
            return []

        entity_stmt = select(Entity).where(
            Entity.entity_id.in_(entity_ids),
            Entity.workspace_id == workspace_id,
        )
        result = await self._db.execute(entity_stmt)
        entities = result.scalars().all()
        entity_map = {e.entity_id: e for e in entities}

        # Fetch aliases for all entities in one query
        alias_stmt = select(EntityAlias).where(
            EntityAlias.entity_id.in_(entity_ids),
            EntityAlias.workspace_id == workspace_id,
        )
        alias_result = await self._db.execute(alias_stmt)
        aliases = alias_result.scalars().all()

        alias_map: dict[str, list[str]] = {}
        for a in aliases:
            alias_map.setdefault(a.entity_id, []).append(a.alias)

        # Build a lookup for Neo4j fallback data
        neo4j_map: dict[str, dict] = {}
        for node in neo4j_nodes or []:
            nid = node.get("entity_id")
            if nid:
                neo4j_map[nid] = node

        enriched = []
        for eid in entity_ids:
            e = entity_map.get(eid)
            if e:
                enriched.append(
                    {
                        "entity_id": e.entity_id,
                        "entity_type": e.entity_type,
                        "canonical_name": e.canonical_name,
                        "attributes": e.attributes,
                        "importance_score": e.importance_score,
                        "confidence_score": e.confidence_score,
                        "interaction_count": e.interaction_count,
                        "last_seen_at": e.last_seen_at.isoformat() if e.last_seen_at else None,
                        "aliases": alias_map.get(e.entity_id, []),
                    }
                )
            else:
                # Fallback: use Neo4j node data so the node is not silently dropped
                fallback = neo4j_map.get(eid, {})
                enriched.append(
                    {
                        "entity_id": eid,
                        "entity_type": fallback.get("entity_type", "unknown"),
                        "canonical_name": fallback.get("name", eid),
                        "attributes": {},
                        "importance_score": None,
                        "confidence_score": None,
                        "interaction_count": 0,
                        "last_seen_at": None,
                        "aliases": alias_map.get(eid, []),
                    }
                )

        return enriched

    async def _count_entities(self, user_id: str, workspace_id: str) -> int:
        stmt = select(func.count(Entity.entity_id)).where(
            Entity.user_id == user_id,
            Entity.workspace_id == workspace_id,
        )
        return (await self._db.execute(stmt)).scalar() or 0

    async def _count_relationships(self, user_id: str, workspace_id: str) -> int:
        stmt = select(func.count(EntityRelationship.relation_id)).where(
            EntityRelationship.user_id == user_id,
            EntityRelationship.workspace_id == workspace_id,
        )
        return (await self._db.execute(stmt)).scalar() or 0

    async def _avg_confidence(self, user_id: str, workspace_id: str) -> float:
        """Compute average confidence for active memories, rounded to 2 decimals."""
        stmt = select(func.avg(Memory.confidence)).where(
            Memory.user_id == user_id,
            Memory.workspace_id == workspace_id,
            Memory.status == "active",
        )
        result = (await self._db.execute(stmt)).scalar()
        if result is None:
            return 0.0
        return round(float(result), 2)

    async def _resolve_entity_names(
        self, entity_ids: set[str], workspace_id: str
    ) -> dict[str, str]:
        """Map entity_id -> canonical_name for a set of IDs."""
        if not entity_ids:
            return {}
        stmt = select(Entity.entity_id, Entity.canonical_name).where(
            Entity.entity_id.in_(list(entity_ids)),
            Entity.workspace_id == workspace_id,
        )
        result = await self._db.execute(stmt)
        return {row.entity_id: row.canonical_name for row in result.all()}

    def _memory_to_dict(
        self,
        mem: Memory,
        entity_name_map: dict[str, str],
        sources: list[str] | None = None,
    ) -> dict:
        """Convert a Memory row to a dict with resolved entity names + sources."""
        eids = mem.entity_ids or []
        return {
            "memory_id": mem.memory_id,
            "memory_type": mem.memory_type,
            "scope": mem.scope,
            "fact_text": mem.fact_text,
            "confidence": mem.confidence,
            "stability_score": mem.stability_score,
            "refresh_count": mem.refresh_count,
            "last_accessed_at": (
                mem.last_accessed_at.isoformat() if mem.last_accessed_at else None
            ),
            "created_at": mem.created_at.isoformat() if mem.created_at else None,
            "entity_ids": list(eids),
            "entity_names": [entity_name_map.get(eid, eid) for eid in eids],
            "sources": sources or [],
        }

    def _resolve_memory_sort(self, sort_by: str):
        """Map sort_by string to a SQLAlchemy column."""
        sort_map = {
            "created_at": Memory.created_at,
            "confidence": Memory.confidence,
            "stability_score": Memory.stability_score,
            "last_accessed_at": Memory.last_accessed_at,
        }
        return sort_map.get(sort_by, Memory.created_at)

    async def _entity_counts_by_type(self, user_id: str, workspace_id: str) -> dict[str, int]:
        stmt = (
            select(Entity.entity_type, func.count(Entity.entity_id))
            .where(
                Entity.user_id == user_id,
                Entity.workspace_id == workspace_id,
            )
            .group_by(Entity.entity_type)
        )
        result = await self._db.execute(stmt)
        return {row[0]: row[1] for row in result.all()}

    async def _memory_counts_by_type(self, user_id: str, workspace_id: str) -> dict[str, int]:
        stmt = (
            select(Memory.memory_type, func.count(Memory.memory_id))
            .where(
                Memory.user_id == user_id,
                Memory.workspace_id == workspace_id,
                Memory.status == "active",
            )
            .group_by(Memory.memory_type)
        )
        result = await self._db.execute(stmt)
        return {row[0]: row[1] for row in result.all()}

    async def _count_entities_since(self, user_id: str, workspace_id: str, since: datetime) -> int:
        stmt = select(func.count(Entity.entity_id)).where(
            Entity.user_id == user_id,
            Entity.workspace_id == workspace_id,
            Entity.created_at >= since,
        )
        return (await self._db.execute(stmt)).scalar() or 0

    async def _count_relationships_since(
        self, user_id: str, workspace_id: str, since: datetime
    ) -> int:
        stmt = select(func.count(EntityRelationship.relation_id)).where(
            EntityRelationship.user_id == user_id,
            EntityRelationship.workspace_id == workspace_id,
            EntityRelationship.created_at >= since,
        )
        return (await self._db.execute(stmt)).scalar() or 0

    async def _count_total_memories(self, user_id: str, workspace_id: str) -> int:
        stmt = select(func.count(Memory.memory_id)).where(
            Memory.user_id == user_id,
            Memory.workspace_id == workspace_id,
            Memory.status == "active",
        )
        return (await self._db.execute(stmt)).scalar() or 0

    async def _count_memories_since(self, user_id: str, workspace_id: str, since: datetime) -> int:
        stmt = select(func.count(Memory.memory_id)).where(
            Memory.user_id == user_id,
            Memory.workspace_id == workspace_id,
            Memory.status == "active",
            Memory.created_at >= since,
        )
        return (await self._db.execute(stmt)).scalar() or 0

    async def _growth_by_day(self, user_id: str, workspace_id: str, days: int = 7) -> list[dict]:
        """Return entity + memory creation counts per day for the last N days."""
        now = datetime.now(timezone.utc)
        start = now - timedelta(days=days)

        entity_day = func.date(Entity.created_at).label("day")
        entity_stmt = (
            select(
                entity_day,
                func.count(Entity.entity_id).label("count"),
            )
            .where(
                Entity.user_id == user_id,
                Entity.workspace_id == workspace_id,
                Entity.created_at >= start,
            )
            .group_by(entity_day)
            .order_by(entity_day)
        )
        entity_result = await self._db.execute(entity_stmt)
        entity_rows = {str(row[0]): row[1] for row in entity_result.all()}

        memory_day = func.date(Memory.created_at).label("day")
        memory_stmt = (
            select(
                memory_day,
                func.count(Memory.memory_id).label("count"),
            )
            .where(
                Memory.user_id == user_id,
                Memory.workspace_id == workspace_id,
                Memory.status == "active",
                Memory.created_at >= start,
            )
            .group_by(memory_day)
            .order_by(memory_day)
        )
        memory_result = await self._db.execute(memory_stmt)
        memory_rows = {str(row[0]): row[1] for row in memory_result.all()}

        all_days = set(entity_rows.keys()) | set(memory_rows.keys())
        growth = []
        for day in sorted(all_days):
            growth.append(
                {
                    "date": day,
                    "entities": entity_rows.get(day, 0),
                    "memories": memory_rows.get(day, 0),
                }
            )

        return growth
