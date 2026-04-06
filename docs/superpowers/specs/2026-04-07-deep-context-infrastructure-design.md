# Spec 5: Deep Context Infrastructure (Qdrant + Neo4j)

**Status:** Draft
**Date:** 2026-04-07
**Dependencies:** None (data layer — can be built in parallel with Specs 2-3)
**Enhances:** Spec 1 (Planner context), Spec 2 (Trust precedent), Spec 4 (Signal similarity)

## Problem Statement

Jarvis has a vector database (Qdrant) and a graph database (Neo4j) but both are significantly underutilized:

### Qdrant: 50% collection utilization, narrow embedding scope

- **4 collections defined, 2 populated.** The `events` and `artifacts` collections are created on startup but never receive data. Zero vectors stored.
- **Only 2 data types embedded.** Memory `fact_text` and entity `canonical_name`. Rich text content in conversations (messages), plans (goals, reasoning), approvals (summaries), and events (titles, summaries) is searched only via Postgres FTS keyword matching — no semantic similarity.
- **Minimal payloads.** Qdrant stores only type + text + user_id. Confidence, stability, entity_type, timestamps — all metadata useful for filtered search — stays in Postgres, requiring a second round-trip after vector search.
- **No payload indexing.** Every query filters only on `user_id`. Can't efficiently filter by `memory_type`, `entity_type`, or `confidence` within Qdrant.

### Neo4j: 80% of methods unused, agents never reason over the graph

- **14 methods implemented, 3 used in production** (all writes: `sync_entity`, `sync_relationship`, `delete_entity`). Read methods like `traverse()`, `find_path()`, `get_project_graph()` are never called by agents.
- **Agent context is paper-thin.** ContextBuilder calls `get_related_people()` for top 3 entities, max 5 people each. Agents see a flat list: `"Alice (works_for)"`. No relationship strength, no temporal context, no multi-hop reasoning.
- **Single edge type.** All relationships use `:RELATES_TO` with `relation_type` as a property. Neo4j is optimized for typed edge labels — this defeats the graph's query advantages.
- **Relationship strength is stored but never queried.** The `strength` field exists in Postgres (`EntityRelationship`) but isn't synced to Neo4j or used in any traversal.
- **Temporal data ignored.** `start_date` and `end_date` on relationships exist but no query uses them.

### Soul/Vision Alignment Issues

- **Vision Pillar #1:** "Continuous Context — maintain continuity across conversations, tools, projects, tasks, and time" — the context infrastructure doesn't leverage the data it already stores
- **Vision Pillar #3:** "Durable Memory — retain useful context in a way that improves usefulness" — memory retrieval is semantically narrow
- **Soul:** "Jarvis should feel like a system that understands what matters" — without graph reasoning, Jarvis doesn't understand relationships between entities, goals, and actions

## Design

### Core Principle

The vector DB answers "what is semantically similar?" The graph DB answers "what is connected to what?" Together, they should give agents rich, multi-dimensional context about the user's world — not just retrieved facts, but understood relationships and historical patterns.

### Part 1: Qdrant Improvements

#### Component 1: Populate Events Collection

When `EventProcessor` ingests normalized events, embed the event summary alongside Postgres storage.

**What to embed:** `f"{event.title}: {event.summary}"` — combines heading with detail for richer semantic representation.

**Payload:**
```python
{
    "event_type": event.event_type,        # "email_received", "pr_opened", etc.
    "source": event.source,                 # "gmail", "github", "slack"
    "importance_score": event.importance_score,
    "occurred_at": event.occurred_at.isoformat(),
    "actor": event.actor_name,              # who triggered the event
}
```

**Implementation:** In `EventProcessor.process_event()`, after Postgres insert, embed and upsert to Qdrant `events` collection. Conditional: skip if importance_score < 0.3 (filter noise — not every system event needs embedding).

**Use case:** "Find events similar to this situation" — when the Planner is reasoning about a new signal, it can search for semantically similar past events to understand precedent.

#### Component 2: Add Conversations Collection

New Qdrant collection for conversation message summaries.

**What to embed:** Summarized conversation turns — not raw messages (too noisy), but conversation-level summaries.

**When:** After conversation history summarization runs (existing `_summarize_history` in jarvis.py produces a summary when history > 8000 chars). Store that summary as a vector.

**Payload:**
```python
{
    "conversation_id": conversation.conversation_id,
    "message_count": len(messages),
    "topics": extracted_topics,             # LLM-extracted topic list
    "created_at": conversation.created_at.isoformat(),
}
```

**Collection name:** `conversations`

**Use case:** "When did we discuss something like this?" — when the user asks about a topic, TriSearch can find semantically similar past conversations, not just keyword matches.

#### Component 3: Add Approvals Collection (for Spec 2 Trust)

New Qdrant collection for approval decision history.

**What to embed:** `f"{capability}: {action_summary} → {outcome}"` — captures the action context and what the user decided.

**Payload:**
```python
{
    "capability": approval.capability,       # "email.send"
    "risk_level": risk_assessment.risk_level, # from LLM assessor
    "outcome": "approved" | "rejected" | "modified",
    "decided_at": approval.decided_at.isoformat(),
}
```

**When:** After each approval decision in `routes_approvals.py` (already planned in Spec 2 trust feedback loop).

**Use case:** When the LLM risk assessor (Spec 2) evaluates a new action, search for semantically similar past approvals. "3 similar actions approved, 0 rejected" gives the assessor concrete precedent beyond abstract trust scores.

#### Component 4: Populate Artifacts Collection

When artifacts are created via `ArtifactStorage`, embed the title and description.

**What to embed:** `f"{artifact.title}: {artifact.description or ''}"`.

**Payload:**
```python
{
    "mime_type": artifact.mime_type,
    "artifact_type": artifact.artifact_type, # "email_draft", "report", "document"
    "created_at": artifact.created_at.isoformat(),
}
```

**Use case:** "Find documents related to this topic" — artifact search that works semantically, not just by title keyword.

#### Component 5: Payload Indexing

Add Qdrant payload indexes for fields that enable filtered search:

```python
async def ensure_indexes(self) -> None:
    """Create payload indexes for filtered search."""
    client = await self._get_client()
    if not client:
        return

    from qdrant_client.models import PayloadSchemaType

    indexes = {
        COLLECTION_MEMORIES: [
            ("memory_type", PayloadSchemaType.KEYWORD),
            ("confidence", PayloadSchemaType.FLOAT),
        ],
        COLLECTION_ENTITIES: [
            ("entity_type", PayloadSchemaType.KEYWORD),
        ],
        COLLECTION_EVENTS: [
            ("source", PayloadSchemaType.KEYWORD),
            ("event_type", PayloadSchemaType.KEYWORD),
            ("importance_score", PayloadSchemaType.FLOAT),
        ],
    }

    for collection, fields in indexes.items():
        for field_name, schema_type in fields:
            try:
                await client.create_payload_index(
                    collection_name=collection,
                    field_name=field_name,
                    field_schema=schema_type,
                )
            except Exception:
                pass  # Index may already exist
```

**Use case:** Search memories of type "preference" only, or events from "gmail" only, or entities of type "person" only — without scanning all vectors.

#### Component 6: Richer Memory Payloads

Extend memory payloads to include scoring metadata, eliminating the Postgres round-trip for TriSearch composite scoring:

```python
# Current payload:
{"memory_type": str, "fact_text": str}

# Proposed payload:
{
    "memory_type": str,
    "fact_text": str,
    "confidence": float,           # for composite scoring
    "stability_score": float,      # for composite scoring
    "entity_ids": list[str],       # for entity overlap scoring
    "created_at": str,             # for recency scoring
    "scope": str,                  # "planning", "general", etc.
}
```

**Benefit:** TriSearch currently fetches Qdrant results, extracts memory_ids, then batch-queries Postgres for confidence/stability/timestamps. With richer payloads, the composite score can be computed directly from Qdrant results — one round-trip instead of two.

### Part 2: Neo4j Improvements

#### Component 7: Typed Relationship Edges

Migrate from single `:RELATES_TO` to typed Neo4j relationship labels.

**Current:** `(a)-[:RELATES_TO {relation_type: "works_at"}]->(b)`
**Proposed:** `(a)-[:WORKS_AT]->(b)` with `relation_type` kept as fallback property

Neo4j's query planner is optimized for typed edges. `MATCH (a)-[:WORKS_AT]->(b)` is significantly faster than `MATCH (a)-[r:RELATES_TO]->(b) WHERE r.relation_type = 'works_at'` because typed edges use a dedicated index.

**Migration approach:** `sync_relationship()` dynamically creates the relationship with the correct label:

```python
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
    """Upsert a typed relationship edge to Neo4j."""
    driver = await self._get_driver()
    if not driver:
        return

    # Sanitize relation_type to valid Neo4j label
    label = relation_type.upper().replace(" ", "_")

    cypher = f"""
        MATCH (a:Entity {{entity_id: $from_id}})
        MATCH (b:Entity {{entity_id: $to_id}})
        MERGE (a)-[r:{label} {{relation_id: $rel_id}}]->(b)
        SET r.relation_type = $rel_type,
            r.user_id = $user_id,
            r.strength = $strength,
            r.start_date = $start_date,
            r.end_date = $end_date
    """

    async with driver.session() as session:
        await session.run(
            cypher,
            from_id=from_entity_id,
            to_id=to_entity_id,
            rel_id=relation_id,
            rel_type=relation_type,
            user_id=user_id,
            strength=strength,
            start_date=start_date,
            end_date=end_date,
        )
```

**Compatibility:** Keep `relation_type` as a property on every edge so generic `:RELATES_TO` queries still work during transition. The `traverse()` method's `rel_filter` switches to label-based matching.

#### Component 8: Sync Relationship Strength & Temporal Data

Currently `sync_relationship()` only syncs `relation_id`, `relation_type`, and `user_id`. Extend to include:

- `strength` (Float) — from Postgres `EntityRelationship.strength`
- `start_date` (String) — temporal start
- `end_date` (String) — temporal end (null = ongoing)
- `last_interaction_at` (String) — most recent interaction timestamp

**Update `graph_sync.py`:** When syncing relationships, read `strength`, `start_date`, `end_date` from Postgres and pass to `sync_relationship()`.

**Use case:** Weighted traversals now prefer strong, recent relationships. "Who is the user closest to?" returns people with high strength and recent interactions, not just anyone within 2 hops.

#### Component 9: Weighted Traversal

New `traverse_weighted()` method that ranks connected entities by relationship strength and recency:

```python
async def traverse_weighted(
    self,
    entity_id: str,
    user_id: str,
    depth: int = 2,
    relation_types: list[str] | None = None,
    min_strength: float = 0.0,
    active_only: bool = True,
) -> list[dict]:
    """Traverse graph with strength-weighted ranking."""

    type_filter = ""
    if relation_types:
        labels = "|".join(rt.upper().replace(" ", "_") for rt in relation_types)
        type_filter = f"[:{labels}]"
    else:
        type_filter = ""

    active_filter = ""
    if active_only:
        active_filter = "AND (r.end_date IS NULL OR r.end_date > datetime())"

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
            avg_strength,
            distance
        ORDER BY avg_strength DESC, distance ASC
        LIMIT 20
    """

    driver = await self._get_driver()
    if not driver:
        return []

    async with driver.session() as session:
        result = await session.run(
            cypher,
            entity_id=entity_id,
            user_id=user_id,
            min_strength=min_strength,
        )
        return await result.data()
```

#### Component 10: Temporal Scoping

New parameter on traversal methods to scope relationships to a time window:

```python
async def traverse_temporal(
    self,
    entity_id: str,
    user_id: str,
    after: str | None = None,    # ISO date — only relationships started after this
    before: str | None = None,   # ISO date — only relationships started before this
    depth: int = 2,
) -> dict:
    """Traverse graph scoped to a time window."""

    temporal_filter = ""
    if after:
        temporal_filter += " AND (r.start_date IS NULL OR r.start_date >= $after)"
    if before:
        temporal_filter += " AND (r.start_date IS NULL OR r.start_date <= $before)"

    # ... Cypher query with temporal_filter in WHERE clause
```

**Use case:** "Who was involved during the Series A process?" scopes to relationships active during that time period, filtering out irrelevant connections.

#### Component 11: Enriched ContextBuilder

Upgrade ContextBuilder to produce rich graph context, not just flat names.

**Current (context_builder.py line 163-168):**
```python
if self._graph_engine and entity_ids:
    for eid in entity_ids[:3]:
        related = await self._graph_engine.get_related_people(user_id, eid)
        for r in related[:5]:
            pack.graph_relationships.append(r)
```

**Proposed:**
```python
if self._graph_engine and entity_ids:
    for eid in entity_ids[:5]:  # Top 5, not 3
        # Use weighted traversal instead of basic get_related_people
        related = await self._graph_engine.traverse_weighted(
            entity_id=eid,
            user_id=user_id,
            depth=2,
            min_strength=0.3,  # Skip weak connections
        )
        for r in related[:8]:  # Up to 8 per entity
            pack.graph_relationships.append({
                "entity_id": r["entity_id"],
                "name": r["name"],
                "entity_type": r["entity_type"],
                "strength": r["avg_strength"],
                "distance": r["distance"],
                "attributes": r.get("attributes"),
            })
```

**Agent prompt rendering (current):**
```
## Entity Relationships
- Alice (works_for)
- Acme Corp (related_to)
```

**Agent prompt rendering (proposed):**
```
## Entity Relationships
Sarah Chen (person, investor)
  ├─ INVESTED_IN → YourCompany (strength: 0.8, since 2025-11)
  ├─ WORKS_AT → Acme Ventures (organization)
  └─ 3 interactions in last 30 days, last: 2 days ago

John (person, team member)
  ├─ WORKS_ON → Series A goal (strength: 0.9)
  ├─ REPORTS_TO → You (strength: 0.7)
  └─ 12 interactions in last 30 days
```

This gives agents genuine relationship understanding, not just name lists.

### Part 3: Cross-Database Patterns

#### Component 12: Vector + Graph Combined Queries

New method in TriSearch that combines vector similarity with graph distance for richer results:

```python
async def search_with_graph_boost(
    self,
    query: str,
    user_id: str,
    context_entity_ids: list[str] | None = None,
    limit: int = 20,
) -> list[SearchResult]:
    """Search with graph proximity boost.

    Results connected to context entities via the graph get a score boost.
    """
    base_results = await self.search(query, user_id, limit=limit * 2)

    if not context_entity_ids or not self._graph_engine:
        return base_results[:limit]

    # Get 2-hop neighborhood of context entities
    neighborhood = set()
    for eid in context_entity_ids[:3]:
        related = await self._graph_engine.traverse_weighted(
            eid, user_id, depth=2
        )
        neighborhood.update(r["entity_id"] for r in related)

    # Boost results that mention entities in the neighborhood
    for result in base_results:
        result_entities = set(result.get("entity_ids", []))
        overlap = result_entities & neighborhood
        if overlap:
            result["score"] *= 1.0 + (0.1 * len(overlap))  # 10% boost per connected entity

    base_results.sort(key=lambda r: r.get("score", 0), reverse=True)
    return base_results[:limit]
```

**Use case:** When searching for context about "Series A," results mentioning Sarah Chen (who is graph-connected to the Series A goal) get boosted over results about unrelated Series A topics.

## Files Changed

### New Files
- `src/models/qdrant_collections.py` — Collection names and payload schemas as constants (replaces scattered string literals)
- Alembic migration for new Qdrant payload indexes (runs `ensure_indexes()` on startup)

### Modified Files — Qdrant
- `src/services/vector_store.py` — Add `ensure_indexes()`, add `conversations` collection constant, enrich `upsert()` to accept richer payloads
- `src/services/embedding_service.py` — Add batch embedding optimization (parallel calls with semaphore)
- `src/services/memory_service.py` — Enrich memory payloads with confidence, stability, entity_ids, created_at
- `src/services/event_processor.py` — Add event embedding after Postgres insert (conditional on importance_score)
- `src/services/artifact_storage.py` — Add artifact title/metadata embedding on create
- `src/services/tri_search.py` — Use richer payloads for composite scoring (skip Postgres round-trip), add `search_with_graph_boost()`
- `src/api/routes_approvals.py` — Embed approval decisions to `approvals` collection (alongside Spec 2 trust feedback)
- `src/orchestrator/jarvis.py` — Embed conversation summaries to `conversations` collection after `_summarize_history`

### Modified Files — Neo4j
- `src/services/graph_engine.py` — Add `traverse_weighted()`, `traverse_temporal()`. Update `sync_relationship()` to accept strength, start_date, end_date. Migrate to typed edge labels.
- `src/services/graph_sync.py` — Pass strength, start_date, end_date when syncing relationships
- `src/services/context_builder.py` — Use `traverse_weighted()` instead of `get_related_people()`. Enrich graph context rendering in `to_prompt()`.
- `src/services/world_model.py` — Pass strength and temporal data to `graph_sync` when upserting relationships

### Modified Files — Integration
- `src/services/context_builder.py` — Enriched graph context section, richer entity rendering
- `src/services/tri_search.py` — `search_with_graph_boost()` combining vector + graph signals

## Testing Strategy

- Unit tests for `ensure_indexes()` — verify index creation for each collection
- Unit tests for event embedding — conditional on importance_score threshold
- Unit tests for conversation summary embedding — verify payload structure
- Unit tests for approval embedding — verify capability + outcome text
- Unit tests for richer memory payloads — verify all fields included
- Unit tests for `traverse_weighted()` — strength ordering, min_strength filter
- Unit tests for `traverse_temporal()` — time window scoping
- Unit tests for typed edge sync — verify correct Neo4j label created
- Unit tests for `search_with_graph_boost()` — verify entity overlap boosts scores
- Integration test: store event → search by semantic similarity → event found
- Integration test: store conversation summary → search "when did we discuss X" → conversation found
- Integration test: approve action → search similar approvals → found with correct outcome
- Integration test: sync relationship with strength → traverse_weighted → sorted by strength
- Integration test: ContextBuilder produces enriched graph context with strength and types

## Success Criteria

1. All 4 Qdrant collections actively populated (events, artifacts, conversations, approvals)
2. TriSearch uses richer payloads to skip Postgres round-trip for scoring
3. Payload indexing enables filtered search by memory_type, entity_type, source
4. Neo4j relationships have typed labels, strength, and temporal data
5. Agent prompts show relationship strength, type, and recency — not just flat names
6. `traverse_weighted()` ranks entities by relationship strength
7. Planner and Perceiver agents receive genuinely useful graph context that improves plan quality

## Blast Radius

This spec primarily extends existing services (additive changes) rather than replacing contracts. The blast radius is moderate and concentrated in the data layer.

### Tier 1: CRITICAL — Core data services

| File | What changes | Why |
|------|-------------|-----|
| `src/services/graph_engine.py` | Add `traverse_weighted()`, `traverse_temporal()`. Modify `sync_relationship()` to accept strength + temporal params. Migrate `:RELATES_TO` to typed labels | Core graph service — all graph queries originate here |
| `src/services/vector_store.py` | Add `ensure_indexes()`, add `COLLECTION_CONVERSATIONS` and `COLLECTION_APPROVALS` constants | Core vector service — all Qdrant operations go through here |
| `src/services/context_builder.py` | Replace `get_related_people()` with `traverse_weighted()`, enrich `to_prompt()` graph section from flat list to structured relationships | Every agent prompt flows through ContextBuilder |

### Tier 2: HIGH — Data ingestion points

| File | What changes | Why |
|------|-------------|-----|
| `src/services/memory_service.py` | Enrich Qdrant payloads with confidence, stability, entity_ids, created_at | Memory storage — most frequent Qdrant writes |
| `src/services/event_processor.py` | Add event embedding after Postgres insert (conditional on importance threshold) | Event ingestion — populates empty events collection |
| `src/services/graph_sync.py` | Pass strength, start_date, end_date from Postgres to `sync_relationship()` | Graph sync pipeline — relationship data flow |
| `src/services/world_model.py` | Extract and pass strength/temporal data when upserting relationships | Entity relationship management |
| `src/services/artifact_storage.py` | Add artifact title embedding on create | Artifact storage — populates empty artifacts collection |
| `src/orchestrator/jarvis.py` | Embed conversation summaries after `_summarize_history` | Conversation summary storage |
| `src/api/routes_approvals.py` | Embed approval decisions (alongside Spec 2 trust feedback) | Approval history storage |

### Tier 3: MEDIUM — Search and retrieval

| File | What changes | Why |
|------|-------------|-----|
| `src/services/tri_search.py` | Use richer payloads for composite scoring (skip Postgres round-trip). Add `search_with_graph_boost()` | Search service — performance + quality improvement |
| `src/services/embedding_service.py` | Add parallel batch embedding with semaphore | Embedding generation — performance |
| `src/api/routes_search.py` | May expose new collection types in search results | Search API |
| `src/api/routes_graph.py` | Update `traverse` endpoint to support weighted/temporal params | Graph API |

### Tier 4: Tests

| File | What changes | Why |
|------|-------------|-----|
| `tests/test_vector_store.py` | Add tests for ensure_indexes, new collections | Vector store tests |
| `tests/test_graph_engine.py` | Add tests for traverse_weighted, traverse_temporal, typed edges | Graph engine tests |
| `tests/test_tri_search.py` | Update for richer payloads, add graph boost tests | Search tests |
| `tests/test_context_builder.py` | Update for enriched graph context format | Context builder tests |
| `tests/test_memory_service.py` | Update for richer Qdrant payloads | Memory service tests |
| `tests/test_event_processor.py` | Add event embedding tests | Event processing tests |
| `tests/test_graph_sync.py` | Update for strength/temporal sync | Graph sync tests |
| `tests/test_world_model.py` | Update for relationship data passing | World model tests |
| `tests/test_entity_dedup.py` | Verify entity dedup still works with richer payloads | Entity dedup regression |

### Tier 5: Safe — Services that consume but don't change

| File | Status | Why safe |
|------|--------|----------|
| `src/services/eviction_service.py` | Safe | Deletion cascade unchanged — deletes by ID |
| `src/services/knowledge_service.py` | Safe | Knowledge page consumes graph data — benefits from richer data automatically |
| `src/services/reranker_service.py` | Safe | Reranker operates on text, not payloads |
| `src/services/fts_service.py` | Safe | FTS is independent of Qdrant |
| Frontend components | Safe | Frontend consumes search results — benefits from richer results automatically |

### Key Risk: Neo4j Typed Edge Migration

Migrating from `:RELATES_TO` to typed labels (`WORKS_AT`, `INVESTED_IN`, etc.) requires:

1. All existing queries that use `-[:RELATES_TO]-` must be updated to handle typed labels
2. The `traverse()` method's `rel_filter` changes from property filtering to label matching
3. Existing data in Neo4j needs a one-time migration: read all `:RELATES_TO` edges, recreate with typed labels
4. During migration, both old and new edges may coexist — queries must handle both

**Mitigation:** Phase the migration:
- Phase A: `sync_relationship()` starts writing typed edges (new data)
- Phase B: `full_sync()` runs once to migrate existing edges
- Phase C: Generic queries updated to handle typed labels
- Phase D: Delete old `:RELATES_TO` edges

### Interdependency with Other Specs

| Spec | What this spec provides | When needed |
|------|------------------------|-------------|
| Spec 1 (Planner) | Richer graph context in agent prompts — Planner understands entity relationships | From Phase 1 of Spec 1 |
| Spec 2 (Trust) | Approval similarity search — risk assessor finds precedent from past approvals | From Phase 2 of Spec 2 |
| Spec 4 (Perception) | Event similarity search — relevance assessor compares signals to past events | From Phase 1 of Spec 4 |

### Total: ~25 files affected (12 source, 9 tests, 2 API routes, 2 new files)