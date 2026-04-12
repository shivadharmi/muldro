# Spec 5B: Neo4j Enrichment + Cross-DB Patterns

**Status:** Draft
**Date:** 2026-04-07
**Dependencies:** Spec 5A (Qdrant Enrichment) — vector search used in graph-boosted queries
**Builds toward:** Enhances Specs 1B (Planner context), 2A (trust precedent), 4A (signal similarity)

## Problem Statement

Neo4j has 14 methods but only 3 are used in production (all writes). Agents see a flat name list from `get_related_people()`. Relationship strength is stored but never queried. Temporal data is ignored. All edges are generic `:RELATES_TO`. The graph is a write-only projection that agents never reason over.

## Design

### Component 1: Typed Relationship Edges

Migrate from `:RELATES_TO {relation_type: "works_at"}` to `:WORKS_AT` typed labels:

```python
async def sync_relationship(self, relation_id, from_entity_id, to_entity_id,
                             relation_type, user_id, strength=1.0,
                             start_date=None, end_date=None):
    label = relation_type.upper().replace(" ", "_")
    cypher = f"""
        MATCH (a:Entity {{entity_id: $from_id}})
        MATCH (b:Entity {{entity_id: $to_id}})
        MERGE (a)-[r:{label} {{relation_id: $rel_id}}]->(b)
        SET r.relation_type = $rel_type, r.user_id = $user_id,
            r.strength = $strength, r.start_date = $start_date, r.end_date = $end_date
    """
    ...
```

Keep `relation_type` as property on every edge for backward compatibility during migration.

**Migration:** Run `full_sync()` once to recreate all edges with typed labels.

### Component 2: Sync Relationship Strength + Temporal Data

Update `graph_sync.py` to pass strength, start_date, end_date from Postgres:

```python
async def sync_relationships_for_entity(self, entity_id):
    rels = await db.execute(
        select(EntityRelationship).where(
            (EntityRelationship.from_entity_id == entity_id)
            | (EntityRelationship.to_entity_id == entity_id)
        )
    )
    for rel in rels.scalars():
        await self._graph.sync_relationship(
            relation_id=rel.relation_id,
            from_entity_id=rel.from_entity_id,
            to_entity_id=rel.to_entity_id,
            relation_type=rel.relation_type,
            user_id=rel.user_id,
            strength=rel.strength or 1.0,
            start_date=rel.start_date.isoformat() if rel.start_date else None,
            end_date=rel.end_date.isoformat() if rel.end_date else None,
        )
```

### Component 3: Weighted Traversal

New method that ranks connected entities by relationship strength and recency:

```python
async def traverse_weighted(self, entity_id, user_id, depth=2,
                             relation_types=None, min_strength=0.0):
    cypher = f"""
        MATCH path = (start:Entity {{entity_id: $entity_id, user_id: $user_id}})
              -[rels*1..{depth}]-(connected:Entity {{user_id: $user_id}})
        WITH connected, relationships(path) AS path_rels
        WITH connected,
             reduce(s = 0.0, r IN path_rels | s + coalesce(r.strength, 0.5)) / size(path_rels) AS avg_strength,
             size(path_rels) AS distance
        WHERE avg_strength >= $min_strength
        RETURN DISTINCT
            connected.entity_id AS entity_id,
            connected.name AS name,
            connected.entity_type AS entity_type,
            connected.attributes AS attributes,
            avg_strength, distance
        ORDER BY avg_strength DESC, distance ASC
        LIMIT 20
    """
    ...
```

### Component 4: Temporal Scoping

New method to scope traversals to a time window:

```python
async def traverse_temporal(self, entity_id, user_id, after=None, before=None, depth=2):
    temporal_filter = ""
    if after:
        temporal_filter += " AND (r.start_date IS NULL OR r.start_date >= $after)"
    if before:
        temporal_filter += " AND (r.start_date IS NULL OR r.start_date <= $before)"
    # Cypher query with temporal_filter in WHERE clause
    ...
```

### Component 5: Enriched ContextBuilder

Replace shallow `get_related_people()` with rich `traverse_weighted()`:

```python
# In context_builder.py
if self._graph_engine and entity_ids:
    for eid in entity_ids[:5]:
        related = await self._graph_engine.traverse_weighted(
            entity_id=eid, user_id=user_id, depth=2, min_strength=0.3,
        )
        for r in related[:8]:
            pack.graph_relationships.append({
                "entity_id": r["entity_id"],
                "name": r["name"],
                "entity_type": r["entity_type"],
                "strength": r["avg_strength"],
                "distance": r["distance"],
                "attributes": r.get("attributes"),
            })
```

**Agent prompt rendering changes from:**
```
## Entity Relationships
- Alice (works_for)
```

**To:**
```
## Entity Relationships
Sarah Chen (person, investor)
  ├─ INVESTED_IN → YourCompany (strength: 0.8, since 2025-11)
  ├─ WORKS_AT → Acme Ventures (organization)
  └─ 3 interactions in last 30 days
```

### Component 6: Graph+Vector Combined Query

New method in TriSearch that boosts results connected via the graph:

```python
async def search_with_graph_boost(self, query, user_id, context_entity_ids=None, limit=20):
    base_results = await self.search(query, user_id, limit=limit * 2)
    if not context_entity_ids or not self._graph_engine:
        return base_results[:limit]

    neighborhood = set()
    for eid in context_entity_ids[:3]:
        related = await self._graph_engine.traverse_weighted(eid, user_id, depth=2)
        neighborhood.update(r["entity_id"] for r in related)

    for result in base_results:
        result_entities = set(result.get("entity_ids", []))
        overlap = result_entities & neighborhood
        if overlap:
            result["score"] *= 1.0 + (0.1 * len(overlap))

    base_results.sort(key=lambda r: r.get("score", 0), reverse=True)
    return base_results[:limit]
```

## Absorbed Issues from Audit

**Issue #24 — Memory stability no decay:** Implement exponential decay in `refresh_stability()`:
```python
def refresh_stability(memory):
    days_since = (now - (memory.last_accessed_at or memory.created_at)).days
    decay = max(0.0, memory.stability_score - (0.02 * days_since))
    memory.stability_score = min(1.0, decay + 0.1)
    memory.last_accessed_at = now
    memory.access_count += 1
```

**Issue #26 — Briefing evidence by timestamp:** With events now in Qdrant (Spec 5A), briefing generation uses vector similarity:
```python
related = await tri_search.search(query=briefing.headline, user_id=user_id,
                                   types=["event", "memory", "conversation"], limit=10)
```

## Files Changed

### Modified Files
- `src/services/graph_engine.py` — Add `traverse_weighted()`, `traverse_temporal()`. Update `sync_relationship()` to accept strength + temporal params. Typed edge labels.
- `src/services/graph_sync.py` — Pass strength, start_date, end_date from Postgres
- `src/services/context_builder.py` — Use `traverse_weighted()`, enrich `to_prompt()` graph section
- `src/services/world_model.py` — Pass strength/temporal data on relationship upsert
- `src/services/tri_search.py` — Add `search_with_graph_boost()`
- `src/services/memory_service.py` — Stability decay in `refresh_stability()`
- `src/services/briefing_read_model.py` — Use vector similarity for related items (replaces timestamp proximity)

### New Files
- `tests/test_traverse_weighted.py`
- `tests/test_graph_boost.py`

## Testing Strategy

- Unit tests: `traverse_weighted()` — strength ordering, min_strength filter, depth limit
- Unit tests: `traverse_temporal()` — time window scoping
- Unit tests: typed edge sync — correct Neo4j label created
- Unit tests: `search_with_graph_boost()` — entity overlap boosts scores
- Unit tests: stability decay math
- Integration: sync relationship with strength → traverse_weighted → sorted by strength
- Integration: ContextBuilder produces enriched graph context
- Integration: briefing evidence uses vector similarity

## Success Criteria

1. Neo4j relationships have typed labels, strength, and temporal data
2. `traverse_weighted()` ranks by relationship strength
3. Agent prompts show rich relationship context (not flat names)
4. Graph+vector combined search boosts graph-connected results
5. Memory stability decays over time
6. Briefing evidence uses semantic similarity

## Blast Radius

**Moderate — graph engine changes + ContextBuilder enrichment.**

| File | Change | Risk |
|------|--------|------|
| `src/services/graph_engine.py` | 2 new methods + modify sync_relationship | **MEDIUM** — adds to graph API |
| `src/services/context_builder.py` | Replace get_related_people with traverse_weighted | **MEDIUM** — changes agent context format |
| `src/services/tri_search.py` | Add graph boost method | **LOW** — new method |
| `src/services/graph_sync.py` | Pass extra params | **LOW** — additive |

### Total: ~15 files (7 modified, 2 new tests, 6 existing tests updated)
