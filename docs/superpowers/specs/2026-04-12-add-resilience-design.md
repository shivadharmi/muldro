# Spec 6B: Add Resilience

**Status:** Draft
**Date:** 2026-04-12
**Dependencies:** Spec 6A (Fix Broken Pipes) — pipes must flow before we can make them robust
**Blocks:** Spec 6C (Observability)

## Problem Statement

After Spec 6A, data flows through the pipes — but transient failures (Bedrock outage, Qdrant unreachable, Neo4j timeout) cause permanent data loss. Embeddings that fail are silently skipped, leaving Postgres records invisible to semantic search. Neo4j connection failures cascade into per-write timeouts that slow the entire pipeline. Qdrant payloads become stale as stability scores decay only in Postgres.

## Soul Alignment

- **"Respect reversibility"** — failed operations should be retryable, not permanently lost
- **"Reduce cognitive load"** — operators shouldn't have to manually detect and fix sync gaps
- **"Optimize for long-term trust"** — reliability is the foundation of trust

## Design

### Component 1: DLQ for Failed Embeddings

**Problem:** If `EmbeddingService.embed_text()` returns `None` (Bedrock failure, timeout) or `VectorStore.upsert()` fails (Qdrant unreachable), the memory/entity is stored in Postgres but never indexed in Qdrant. There's no compensation mechanism — the record becomes permanently invisible to semantic search.

This happens in 6 memory creation paths (`extract_and_store`, `extract_preferences`, `store_goal_memory`, `store_instruction_memory`, `store_briefing_memory`, `store_memory`) and 1 entity creation path (`WorldModel.upsert_entity`).

**Fix:** When embedding or Qdrant upsert fails, enqueue a `failed_embedding` entry in the existing `DeadLetterService`. The existing `_tick_dlq_retry` in the scheduler already processes DLQ entries.

**Step 1: Enqueue on failure in MemoryService**

**Prerequisite: Add `dead_letter` parameter to `MemoryService.__init__`:**

```python
# memory_service.py — update __init__ signature
def __init__(
    self,
    settings: Settings,
    db: AsyncSession,
    vector_store=None,
    dead_letter: DeadLetterService | None = None,  # NEW
):
    ...
    self._dead_letter = dead_letter
```

All call sites that instantiate `MemoryService` must be updated to pass `dead_letter` when available:
- `worker.py` `_handle_memory_extraction` — instantiate `DeadLetterService(db)` and pass it
- `scheduler.py` schedule handlers — pass if available
- `runtime.py` Tier 1 init — pass `None` (DLQ not available at Tier 1 build time)

Add a helper method to `MemoryService`:
```python
async def _enqueue_failed_embedding(
    self, memory_id: str, user_id: str, collection: str = "memories"
) -> None:
    """Enqueue a failed embedding for retry via DLQ."""
    if not self._dead_letter:
        return
    try:
        await self._dead_letter.enqueue(
            user_id=user_id,
            operation_type="failed_embedding",
            error_type="EmbeddingFailure",
            error_message=f"Embedding/upsert failed for {collection}:{memory_id}",
            payload={
                "record_id": memory_id,
                "collection": collection,
                "record_type": "memory",
            },
        )
    except Exception:
        logger.warning(
            "Failed to enqueue embedding retry for %s", memory_id, exc_info=True
        )
```

Replace the silent skip pattern in all 6 upsert sites:
```python
# BEFORE (silent skip):
if self._vector_store and embedding:
    await self._vector_store.upsert(...)

# AFTER (DLQ on failure):
if embedding:
    if self._vector_store:
        try:
            await self._vector_store.upsert(...)
        except Exception:
            logger.debug("Qdrant upsert failed for %s", memory_id, exc_info=True)
            await self._enqueue_failed_embedding(memory_id, user_id)
    else:
        await self._enqueue_failed_embedding(memory_id, user_id)
elif self._dead_letter:
    await self._enqueue_failed_embedding(memory_id, user_id)
```

**Step 2: Same pattern for WorldModel entity embedding**

Add the same DLQ enqueue in `WorldModel.upsert_entity()` when entity embedding fails.

**Step 3: Add DLQ retry handler for `failed_embedding` type**

In `scheduler.py`, the existing `_tick_dlq_retry` calls `DeadLetterService.get_pending()` and retries entries. Add handling for `operation_type="failed_embedding"`:

```python
# In the DLQ retry handler (scheduler.py or a new dedicated handler)
async def _retry_failed_embedding(self, entry, factory) -> None:
    """Retry a failed embedding from the DLQ."""
    payload = entry.payload or {}
    record_id = payload.get("record_id")
    collection = payload.get("collection", "memories")
    record_type = payload.get("record_type", "memory")

    async with factory() as db:
        if record_type == "memory":
            from src.models.memory import Memory
            result = await db.execute(
                select(Memory).where(Memory.memory_id == record_id)
            )
            mem = result.scalar_one_or_none()
            if not mem or mem.status != "active":
                return  # record gone or expired, skip

            embedding = await self._embedding_service.embed_text(mem.fact_text)
            if embedding and self._vector_store:
                await self._vector_store.upsert(
                    collection, record_id, embedding,
                    MemoryService._build_memory_payload(
                        memory_type=mem.memory_type,
                        fact_text=mem.fact_text,
                        user_id=mem.user_id,
                        confidence=mem.confidence or 0.5,
                        stability_score=mem.stability_score or 0.0,
                        entity_ids=mem.entity_ids or [],
                        scope=mem.scope,
                    ),
                    mem.user_id,
                )

        elif record_type == "entity":
            from src.models.entities import Entity
            result = await db.execute(
                select(Entity).where(Entity.entity_id == record_id)
            )
            ent = result.scalar_one_or_none()
            if not ent:
                return
            embedding = await self._embedding_service.embed_text(ent.canonical_name)
            if embedding and self._vector_store:
                await self._vector_store.upsert(
                    "entities", record_id, embedding,
                    {"entity_type": ent.entity_type, "canonical_name": ent.canonical_name},
                    ent.user_id,
                )
```

**Files:** `memory_service.py`, `world_model.py`, `scheduler.py`

### Component 2: Circuit Breaker for Neo4j Writes

**Problem:** All `GraphEngine` write methods (`sync_entity`, `sync_relationship`, `delete_entity`) catch exceptions at WARNING level. If Neo4j is down, every write attempt hits a connection timeout (~30s), drastically slowing entity extraction. There's no backoff or fast-fail.

**Fix:** Add a lightweight circuit breaker to `GraphEngine`, modeled after the existing `AnthropicCircuitBreaker` (`api_circuit_breaker.py`).

```python
class _Neo4jCircuit:
    """Simple circuit breaker for Neo4j connections."""

    FAILURE_THRESHOLD = 5
    COOLDOWN_SECONDS = 120

    def __init__(self):
        self._failures = 0
        self._state = "closed"  # closed, open, half_open
        self._opened_at: float = 0

    def record_success(self):
        self._failures = 0
        self._state = "closed"

    def record_failure(self):
        self._failures += 1
        if self._failures >= self.FAILURE_THRESHOLD:
            self._state = "open"
            self._opened_at = time.monotonic()

    def allow_request(self) -> bool:
        if self._state == "closed":
            return True
        if self._state == "open":
            if time.monotonic() - self._opened_at >= self.COOLDOWN_SECONDS:
                self._state = "half_open"
                return True
            return False
        # half_open: allow one probe
        return True
```

Integrate into `GraphEngine`:
```python
class GraphEngine:
    def __init__(self, settings):
        self._settings = settings
        self._driver = None
        self._circuit = _Neo4jCircuit()

    async def sync_entity(self, ...):
        driver = await self._get_driver()
        if not driver or not self._circuit.allow_request():
            return
        try:
            async with driver.session() as session:
                await session.run(...)
            self._circuit.record_success()
        except Exception:
            self._circuit.record_failure()
            logger.warning("Neo4j sync_entity failed for %s", entity_id, exc_info=True)
```

Apply the same pattern to `sync_relationship`, `delete_entity`, `traverse`, `traverse_weighted`, `traverse_temporal`, `find_path`, `get_related_people`, `search_entities`.

**Files:** `graph_engine.py`

### Component 3: Qdrant Payload Refresh for Stability Decay

**Problem:** Memory `stability_score` in Qdrant payloads is written at upsert time and never updated. `_compute_decayed_stability` runs on Postgres access, but Qdrant search uses the stale original score. Over weeks, highly relevant but recently-accessed memories may rank lower than old, never-accessed memories.

**Fix:** Add a `_tick_stability_refresh` to the scheduler that runs daily. It finds memories with significant drift between Postgres stability and Qdrant payload stability, then does a batch `set_payload` update (no re-embedding needed).

```python
async def _tick_stability_refresh(self, factory, vector_store) -> None:
    """Batch-update Qdrant stability_score for memories with significant drift."""
    if not vector_store:
        return
    try:
        from sqlalchemy import select
        from src.models.memory import Memory

        async with factory() as db:
            # Find memories where stability has likely drifted (>7 days since last access)
            from datetime import datetime, timedelta, timezone
            cutoff = datetime.now(timezone.utc) - timedelta(days=7)
            result = await db.execute(
                select(Memory.memory_id, Memory.stability_score)
                .where(
                    Memory.status == "active",
                    Memory.last_accessed_at < cutoff,
                )
                .limit(200)
            )
            updates = result.all()
            if not updates:
                return

            for memory_id, stability in updates:
                try:
                    await vector_store.set_payload(
                        "memories", memory_id,
                        {"stability_score": stability or 0.0},
                    )
                except Exception:
                    pass  # best-effort per record

            logger.info("Stability refresh: %d Qdrant payloads updated", len(updates))
    except Exception:
        logger.warning("Stability refresh tick failed", exc_info=True)
```

Also add `set_payload` method to `VectorStore`:
```python
async def set_payload(
    self, collection: str, point_id: str, payload: dict
) -> None:
    """Update payload fields on an existing point without re-embedding."""
    client = await self._get_client()
    if not client:
        return
    from qdrant_client.models import PointIdsList
    await client.set_payload(
        collection_name=collection,
        payload=payload,
        points=PointIdsList(points=[self._to_uuid(point_id)]),
    )
```

**Scheduler wiring:** Run in the same daily gate as consolidation (Component 6A.6):
```python
if self._tick_count % 120 == 0 and current_hour == 2:
    await self._tick_consolidation(factory)
    await self._tick_stability_refresh(factory, vector_store)
```

**Files:** `scheduler.py`, `vector_store.py`

### Component 4: Separate Extraction from Indexing in Worker

**Problem:** When `_handle_entity_extraction` or `_handle_memory_extraction` fails and retries, it re-runs the full Claude extraction call even if extraction succeeded but embedding failed. This wastes API budget.

**Fix:** Restructure the handlers to separate extraction (Claude call + Postgres write) from indexing (embedding + Qdrant upsert). If extraction succeeds but indexing fails, only the indexing step goes to DLQ (via Component 1).

For `_handle_entity_extraction`:
```python
async def _handle_entity_extraction(self, event) -> None:
    # Step 1: Extract entities (Claude call + Postgres)
    entity_ids = await world_model.extract_from_event(event_id, user_id, ...)
    await db.commit()

    # Step 2: Sync to Neo4j (separate, guarded)
    if entity_ids and self._settings.neo4j_url:
        try:
            graph_sync = self._graph_sync or GraphSyncService(self._settings, db)
            await graph_sync.batch_sync_entities(entity_ids)
        except Exception:
            logger.warning("Neo4j sync failed for event %s", event_id, exc_info=True)
            # Neo4j failures don't block extraction — they'll be retried
            # by the graph_syncer consumer when entity.created events fire
```

The key insight: extraction already emits `entity.created` events, so Component 1's `graph_syncer` consumer provides automatic retry for Neo4j sync failures. The inline sync in the handler is now best-effort with a fallback.

For `_handle_memory_extraction`: The extraction already writes to Postgres and attempts Qdrant upsert inside `MemoryService.extract_and_store()`. With Component 1's DLQ, failed Qdrant upserts are automatically enqueued for retry. No structural change needed — just ensure `dead_letter` is passed to `MemoryService`.

**Files:** `worker.py`

### Component 5: Missing Qdrant Payload Indexes

**Problem:** The `approvals`, `conversations`, and `artifacts` collections have no payload indexes. Any filtered query on these collections scans all points, degrading with scale.

**Fix:** Extend `ensure_indexes()` in `vector_store.py`:

```python
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
    COLLECTION_APPROVALS: [
        ("capability", PayloadSchemaType.KEYWORD),
        ("outcome", PayloadSchemaType.KEYWORD),
    ],
    COLLECTION_CONVERSATIONS: [
        ("conversation_id", PayloadSchemaType.KEYWORD),
    ],
    COLLECTION_ARTIFACTS: [
        ("artifact_type", PayloadSchemaType.KEYWORD),
        ("mime_type", PayloadSchemaType.KEYWORD),
    ],
}
```

**Files:** `vector_store.py`

## Files Changed

| File | Action | Components |
|------|--------|-----------|
| `src/services/memory_service.py` | Modify | 1 — add `dead_letter` param to __init__, DLQ enqueue on embedding failure (6 sites) |
| `src/services/world_model.py` | Modify | 1 — DLQ enqueue on entity embedding failure |
| `src/services/graph_engine.py` | Modify | 2 — circuit breaker for all Neo4j operations |
| `src/services/scheduler.py` | Modify | 1, 3 — DLQ retry handler, stability refresh tick |
| `src/services/vector_store.py` | Modify | 3, 5 — set_payload method, extended indexes |
| `src/services/worker.py` | Modify | 4 — separate extraction from indexing |
| `tests/test_embedding_dlq.py` | Create | 1 — DLQ enqueue + retry tests |
| `tests/test_neo4j_circuit_breaker.py` | Create | 2 — circuit breaker state transition tests |
| `tests/test_stability_refresh.py` | Create | 3 — payload refresh tick tests |
| `tests/test_payload_indexing.py` | Modify | 5 — verify all 6 collections indexed |

## Testing Strategy

- Unit: embedding failure → DLQ entry created with correct payload
- Unit: DLQ retry handler re-embeds and upserts to Qdrant
- Unit: DLQ retry skips expired/merged memories
- Unit: circuit breaker opens after 5 failures, closes after cooldown
- Unit: circuit breaker half_open allows one probe
- Unit: set_payload updates Qdrant without re-embedding
- Unit: stability refresh finds stale memories and updates Qdrant
- Unit: ensure_indexes covers all 6 collections
- Integration: embed failure → DLQ → retry tick → memory appears in Qdrant

## Success Criteria

1. No permanently lost embeddings — every Postgres record eventually reaches Qdrant via DLQ
2. Neo4j outage doesn't slow entity extraction (circuit breaker fast-fails after 5 errors)
3. Qdrant stability scores stay within 0.1 of Postgres values (refreshed daily)
4. All 6 Qdrant collections have payload indexes for filtered queries
5. Claude extraction is never re-called due to embedding-only failures

## Blast Radius

| File | Change | Risk |
|------|--------|------|
| `memory_service.py` | Replace silent skip with DLQ enqueue (6 sites) | **MEDIUM** — touches all memory creation paths |
| `graph_engine.py` | Add circuit breaker wrapper around all methods | **MEDIUM** — changes error handling behavior |
| `vector_store.py` | Add `set_payload` + extend indexes | **LOW** — additive |
| `scheduler.py` | Add DLQ handler + stability refresh | **LOW** — new ticks |
| `worker.py` | Restructure handlers | **LOW** — logic separation, same outcomes |

### Total: ~10 files (6 modified, 3 new test files, 1 modified test)
