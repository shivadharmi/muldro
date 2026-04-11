# Spec 6A: Fix the Broken Pipes

**Status:** Draft
**Date:** 2026-04-12
**Dependencies:** Specs 5A/5B (Qdrant + Neo4j enrichment) — already implemented; this spec fixes reliability gaps discovered post-implementation
**Blocks:** Spec 6B (Resilience), Spec 6C (Observability)

## Problem Statement

The intelligence pipeline has 6 broken or disconnected links where data is created in Postgres but never reaches Neo4j or Qdrant. These failures are silent — no errors, no logs at INFO level, no health degradation signal. The result: agents see an impoverished world model, graph traversals return empty results, and semantic search misses relevant memories.

Root cause: event handlers exist but are never subscribed, services are instantiated ad-hoc instead of persisted, and several write paths skip downstream sync entirely.

## Soul Alignment

- **"Always preserve clarity"** — silent sync failures violate this directly
- **"Never fake certainty"** — agents reason over incomplete data without knowing it
- **"Real leverage before AI theater"** — the graph and vector stores exist but don't receive data
- **"Trust before autonomy"** — the system can't graduate trust if it can't observe outcomes

## Design

### Component 1: Wire GraphSyncService Event Handlers to Worker

**Problem:** `GraphSyncService.on_entity_change()` and `on_relationship_change()` (`graph_sync.py:30-75`) exist but no consumer group subscribes to entity change events. Entities created by Librarian or any agent via tools never reach Neo4j.

**Fix:** Add a `graph_syncer` consumer group to `StreamConsumerManager.CONSUMER_GROUPS`. The handler listens on the same per-user event stream for `entity.created`, `entity.updated`, `relationship.created`, and `relationship.updated` events, then calls `GraphSyncService.batch_sync_entities()`.

**Key detail:** The worker already processes entity extraction events on the `jarvis:events:{user_id}` stream. Entity change events (`entity.created`, etc.) are published to `jarvis:agent_events:{user_id}` by `WorldModel`. The new consumer must subscribe to the agent events stream, not the main events stream.

```python
# worker.py — split consumer groups by stream
# Main event stream (jarvis:events:{user_id})
MAIN_STREAM_GROUPS = (
    "entity_extractor",
    "memory_extractor",
    "trigger_evaluator",
    "contradiction_checker",  # NEW (Component 4) — contradiction events published to main stream
)
# Agent event stream (jarvis:agent_events:{user_id})
AGENT_STREAM_GROUPS = (
    "graph_syncer",           # NEW (Component 1) — entity.created/updated events on agent stream
)
```

Handler:
```python
async def _handle_graph_sync(self, event) -> None:
    """Sync entity/relationship changes to Neo4j."""
    event_type = event.payload.get("event_type", "")
    if not event_type.startswith(("entity.", "relationship.")):
        return

    entity_id = event.payload.get("entity_id")
    if not entity_id:
        return

    if not self._settings.neo4j_url:
        return

    from src.models.database import get_session_factory
    from src.services.graph_sync import GraphSyncService

    factory = get_session_factory()
    async with factory() as db:
        graph_sync = GraphSyncService(self._settings, db)
        await graph_sync.sync_entity_by_id(entity_id)
        await graph_sync.sync_relationships_for_entity(entity_id)
        await graph_sync.close()
```

**Challenge:** The current `StreamConsumerManager.run()` subscribes to `bus.event_stream(uid)` (main events). For graph sync, we need the agent events stream. The `run()` method needs to also subscribe to agent events for the `graph_syncer` and `contradiction_checker` groups.

**Solution:** Refactor `run()` to subscribe by stream type:
```python
# In run(), main event stream — existing + contradiction_checker
for uid in user_ids:
    stream = bus.event_stream(uid)
    for group in MAIN_STREAM_GROUPS:
        await bus.create_consumer_group(stream, group)
        task = asyncio.create_task(
            self._consumer_loop(bus, stream, group, handler_map[group]),
            name=f"consumer-{uid}-{group}",
        )
        self._tasks.append(task)

# Agent event stream — graph_syncer only
for uid in user_ids:
    agent_stream = f"jarvis:agent_events:{uid}"
    for group in AGENT_STREAM_GROUPS:
        await bus.create_consumer_group(agent_stream, group)
        task = asyncio.create_task(
            self._consumer_loop(bus, agent_stream, group, handler_map[group]),
            name=f"consumer-{uid}-{group}",
        )
        self._tasks.append(task)
```

This replaces the current single `CONSUMER_GROUPS` iteration. The `handler_map` is extended with the new handlers.

**Files:** `worker.py`

### Component 2: Persist GraphSyncService in Runtime

**Problem:** `GraphSyncService` is instantiated ad-hoc in `worker.py:220` and discarded after each entity extraction batch. No persistent health tracking across invocations.

**Fix:** Add `GraphSyncService` as a Tier 3 service in `runtime.py`:

```python
# runtime.py — after GraphEngine initialization
try:
    if svc.graph_engine:
        from src.services.graph_sync import GraphSyncService
        svc.extras["graph_sync"] = GraphSyncService(settings, db)
except Exception:
    logger.debug("Tier 3: GraphSyncService unavailable", exc_info=True)
```

Update `worker.py` `_handle_entity_extraction` to use the persistent instance when available (fall back to ad-hoc for backward compatibility):

```python
# worker.py — in _handle_entity_extraction, replace ad-hoc instantiation
graph_sync = self._graph_sync  # injected from runtime
if not graph_sync:
    graph_sync = GraphSyncService(self._settings, db)
```

Add `graph_sync` parameter to `StreamConsumerManager.__init__`:
```python
def __init__(self, settings: Settings, graph_sync=None):
    self._settings = settings
    self._graph_sync = graph_sync
    ...
```

**Files:** `runtime.py`, `worker.py`

### Component 3: Wire update_entity Tool to Neo4j

**Problem:** `update_entity` in `intelligence_server.py:187-237` modifies Postgres entity attributes and aliases but doesn't trigger Neo4j sync. Agent-driven entity updates create state drift between Postgres and Neo4j.

**Fix:** After the Postgres commit in `update_entity`, emit an `entity.updated` event to the event bus. Component 1's `graph_syncer` consumer will pick it up.

```python
# intelligence_server.py — after db.commit() in update_entity (line 232)
await db.flush()
await db.commit()

# Emit entity.updated for downstream Neo4j sync
try:
    from src.services.event_bus import EventBus
    bus = EventBus(ctx.request_context.lifespan_context.get("redis_url", ""))
    await bus.publish(
        f"jarvis:agent_events:{user_id}",
        "entity.updated",
        {"entity_id": entity_id, "user_id": user_id},
        user_id=user_id,
    )
except Exception:
    logger.debug("entity.updated event emit failed for %s", entity_id, exc_info=True)

return {"status": "updated", "entity_id": entity_id}
```

**Alternative (simpler):** If the event bus is hard to access from the MCP tool context, do an inline sync:
```python
if settings.neo4j_url:
    from src.services.graph_sync import GraphSyncService
    gs = GraphSyncService(settings, db)
    await gs.sync_entity_by_id(entity_id)
    await gs.close()
```

**Recommendation:** Use the inline approach — simpler, synchronous, no dependency on event bus availability from MCP context.

**Files:** `intelligence_server.py`

### Component 4: Wire Contradiction Checker Consumer

**Problem:** `contradiction_check_requested` events are published by `MemoryService.extract_and_store()` (`memory_service.py:204`) to the event bus, but no consumer group subscribes to process them. Contradictions are never detected or resolved.

**Fix:** Add a `contradiction_checker` consumer group to `StreamConsumerManager`. The handler calls `MemoryService.check_contradictions()`.

```python
async def _handle_contradiction_check(self, event) -> None:
    """Check if a newly stored memory contradicts existing ones."""
    payload = event.payload
    memory_id = payload.get("memory_id", "")
    fact_text = payload.get("fact_text", "")
    user_id = payload.get("user_id", event.user_id)
    workspace_id = payload.get("workspace_id", "")

    if not memory_id or not fact_text:
        return

    from src.models.database import get_session_factory
    from src.services.memory_service import MemoryService

    factory = get_session_factory()
    async with factory() as db:
        ms = MemoryService(settings=self._settings, db=db, vector_store=self._vector_store)
        superseded = await ms.check_contradictions(
            user_id=user_id,
            new_fact=fact_text,
            new_memory_id=memory_id,
            workspace_id=workspace_id,
        )
        await db.commit()
        if superseded:
            logger.info(
                "Contradiction check for %s: %d memories superseded",
                memory_id, len(superseded),
            )
```

**Stream:** These events are published to `jarvis:events:{user_id}` (main stream), not agent events. `contradiction_checker` is already placed in `MAIN_STREAM_GROUPS` in Component 1's refactored stream routing.

**Files:** `worker.py`

### Component 5: Qdrant Cascade Delete on Memory Lifecycle

**Problem:** When memories are superseded (contradiction resolution, `memory_service.py:641-651`) or merged (`consolidate_memories`), the old Qdrant vector is NOT deleted. Stale/contradicted vectors remain searchable indefinitely.

**Fix:** Add Qdrant delete calls in two places:

**5a. In `check_contradictions()` — after superseding:**
```python
# memory_service.py — after setting superseded_by (line 651)
superseded.append(cand_id)
# Cascade delete from Qdrant
if self._vector_store:
    try:
        await self._vector_store.delete("memories", cand_id)
    except Exception:
        logger.debug("Qdrant cascade delete failed for %s", cand_id, exc_info=True)
```

**5b. In `consolidate_memories()` — after marking as merged:**
```python
# memory_service.py — after setting status="merged" in consolidation loop
if self._vector_store:
    try:
        await self._vector_store.delete("memories", duplicate_id)
    except Exception:
        logger.debug("Qdrant cascade delete failed for %s", duplicate_id, exc_info=True)
```

**Files:** `memory_service.py`

### Component 6: Enable Memory Consolidation as Scheduler Tick

**Problem:** `consolidate_memories` exists as a schedule action (`schedule_seeder.py:77-86`) but is seeded disabled. It only runs if manually enabled in the database. Since it's a critical maintenance operation (deduplicates memories), it should run reliably.

**Fix:** Add `_tick_consolidation()` as a direct scheduler tick that runs once daily (gated by hour check, not dependent on schedule table):

```python
# scheduler.py — in _tick(), after persona batch
# 4c. Memory consolidation — once daily at 2 AM
current_hour = datetime.now(timezone.utc).hour
if self._tick_count % 120 == 0 and current_hour == 2:
    await self._tick_consolidation(factory)
```

```python
async def _tick_consolidation(self, factory) -> None:
    """Nightly memory consolidation — merge highly similar memories."""
    try:
        async with factory() as db:
            from src.services.memory_service import MemoryService
            # Get all active user IDs
            from sqlalchemy import distinct, select
            from src.models.memory import Memory
            result = await db.execute(
                select(distinct(Memory.user_id)).where(Memory.status == "active")
            )
            user_ids = [r[0] for r in result.all()]

            total_merged = 0
            for uid in user_ids:
                ms = MemoryService(settings=self._settings, db=db, vector_store=self._vector_store)
                merged = await ms.consolidate_memories(uid)
                total_merged += merged
            await db.commit()
            if total_merged:
                logger.info("Nightly consolidation: %d memories merged", total_merged)
    except Exception:
        logger.warning("Memory consolidation tick failed", exc_info=True)
```

**Files:** `scheduler.py`

## Files Changed

| File | Action | Components |
|------|--------|-----------|
| `src/services/worker.py` | Modify | 1, 2, 4 — add consumer groups, handlers, agent events stream |
| `src/runtime.py` | Modify | 2 — persist GraphSyncService |
| `src/tools/intelligence_server.py` | Modify | 3 — inline Neo4j sync after update_entity |
| `src/services/memory_service.py` | Modify | 5 — cascade delete in contradiction + consolidation |
| `src/services/scheduler.py` | Modify | 6 — add consolidation tick |
| `tests/test_worker_graph_sync.py` | Create | 1, 2 — graph sync consumer tests |
| `tests/test_worker_contradiction.py` | Create | 4 — contradiction consumer tests |
| `tests/test_memory_cascade_delete.py` | Create | 5 — Qdrant cascade tests |
| `tests/test_consolidation_tick.py` | Create | 6 — scheduler consolidation tests |

## Testing Strategy

- Unit: graph_syncer handler receives entity.updated → calls sync_entity_by_id
- Unit: graph_syncer handler ignores non-entity events
- Unit: contradiction_checker handler calls check_contradictions with correct params
- Unit: check_contradictions deletes superseded memory from Qdrant
- Unit: consolidate_memories deletes merged memory from Qdrant
- Unit: update_entity tool triggers Neo4j sync
- Unit: _tick_consolidation runs for all active users
- Integration: event published → consumer picks up → Neo4j entity created
- Integration: memory stored → contradiction published → consumer resolves → old vector deleted

## Success Criteria

1. Entity changes from any source (tool, extraction, Librarian) reach Neo4j within 30s
2. Contradiction check events are consumed and resolved (superseded memories removed from Qdrant)
3. update_entity tool changes are reflected in Neo4j
4. Memory consolidation runs nightly without manual schedule enablement
5. No orphaned Qdrant vectors after contradiction resolution or consolidation

## Blast Radius

| File | Change | Risk |
|------|--------|------|
| `worker.py` | Add 2 consumer groups + 2 handlers + agent stream subscriptions | **MEDIUM** — new consumers; existing ones unchanged |
| `runtime.py` | Add GraphSyncService to Tier 3 | **LOW** — additive, optional |
| `intelligence_server.py` | Add inline Neo4j sync after update_entity | **LOW** — after existing commit, guarded |
| `memory_service.py` | Add Qdrant delete in 2 methods | **LOW** — after existing Postgres writes, guarded |
| `scheduler.py` | Add consolidation tick | **LOW** — new tick, doesn't affect existing ticks |

### Total: ~9 files (5 modified, 4 new test files)
