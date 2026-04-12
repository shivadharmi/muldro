# Fix-9: Data Layer Resilience

Neo4j/Qdrant error handling, dead parameters, and search completeness.

## Phase 1 — Neo4j Exception Handlers (M-26)

**File:** `backend/src/services/graph_engine.py`

### Task 1.1: Wrap `traverse` in try/except (lines 159–187)

The `async with driver.session() as session:` block at line 159 has no exception handler. Neo4j driver exceptions propagate to callers.

```python
# Lines 159-187: wrap in try/except matching traverse_weighted pattern
try:
    async with driver.session() as session:
        result = await session.run(...)
        record = await result.single()
        if not record:
            return {"nodes": [], "edges": []}
        return {"nodes": record["nodes"], "edges": record["edges"]}
except Exception:
    logger.warning("Neo4j traverse failed for %s", entity_id, exc_info=True)
    return {"nodes": [], "edges": []}
```

### Task 1.2: Wrap `find_path` in try/except (lines 248–263)

Same pattern — `session.run()` at line 249 is unguarded.

```python
# Lines 248-263: wrap in try/except
try:
    async with driver.session() as session:
        ...
        return record["path_nodes"] if record else []
except Exception:
    logger.warning("Neo4j find_path failed for %s -> %s", from_entity_id, to_entity_id, exc_info=True)
    return []
```

### Task 1.3: Wrap `get_related_people` in try/except (lines 271–284)

```python
# Lines 271-284: wrap in try/except
try:
    async with driver.session() as session:
        ...
        return records
except Exception:
    logger.warning("Neo4j get_related_people failed for %s", entity_id, exc_info=True)
    return []
```

### Task 1.4: Fix `traverse_weighted` log level (L-16, line 237)

Change `logger.debug` to `logger.warning` at line 237. Also fix `traverse_temporal` at line 568 (same issue).

**Tests:** Unit tests mocking `driver.session()` to raise `neo4j.exceptions.ServiceUnavailable`, asserting empty results returned and no exception propagated.

---

## Phase 2 — Neo4j Dead Parameter and Unbounded Traversal (M-27, M-30)

**File:** `backend/src/services/graph_engine.py`

### Task 2.1: Fix `get_stale_relationships` dead `days` parameter (lines 460–487)

The `days` parameter at line 460 is accepted but never used in the Cypher query (lines 471–486). The query returns all relationships regardless of age.

Add a `$cutoff_date` parameter to the Cypher `WHERE` clause. Compute cutoff from `days`:

```python
from datetime import datetime, timedelta, timezone

cutoff = (datetime.now(timezone.utc) - timedelta(days=days)).isoformat()
```

Update query (line 473) to filter:

```cypher
WHERE b.user_id = $user_id
  AND (r.start_date IS NULL OR r.start_date <= $cutoff_date)
```

Pass `cutoff_date=cutoff` to `session.run()`.

Also wrap in try/except (currently unguarded like Phase 1 methods).

### Task 2.2: Bound `detect_communities` traversal depth (M-30, line 499)

Line 499 uses `(e)-[*]-(connected:Entity)` — unbounded depth, OOM risk on large graphs.

Change to `(e)-[*1..3]-(connected:Entity)` to cap at 3 hops.

Also wrap in try/except (currently unguarded).

**Tests:** Verify `get_stale_relationships(days=7)` passes `cutoff_date` parameter. Verify `detect_communities` Cypher contains `*1..3`.

---

## Phase 3 — Qdrant Exception Specificity (M-28, M-29)

**File:** `backend/src/services/vector_store.py`

### Task 3.1: Fix `ensure_collections` bare `except` (lines 86–93)

Currently line 88 catches all exceptions to check if collection exists. Replace with specific exception:

```python
from qdrant_client.http.exceptions import UnexpectedResponse

for name in collections:
    try:
        await client.get_collection(name)
    except UnexpectedResponse:
        # Collection doesn't exist — create it
        await client.create_collection(...)
        logger.info("Created Qdrant collection: %s", name)
    except Exception:
        logger.warning("Qdrant ensure_collections failed for %s", name, exc_info=True)
```

### Task 3.2: Fix `ensure_indexes` bare `except: pass` (lines 118–125)

Same pattern at line 124. Replace with:

```python
try:
    await client.create_payload_index(...)
except UnexpectedResponse:
    pass  # Index already exists
except Exception:
    logger.warning(
        "Qdrant create_payload_index failed: %s.%s",
        collection, field_name, exc_info=True,
    )
```

**Tests:** Mock `client.create_payload_index` to raise generic `RuntimeError`, assert `logger.warning` called. Mock `UnexpectedResponse`, assert silently handled.

---

## Phase 4 — Conversation Embedding Completeness (H-20, M-33)

**File:** `backend/src/orchestrator/jarvis.py`

### Task 4.1: Add `user_id` parameter to `_summarize_history` (line 2179)

Current signature: `async def _summarize_history(self, lines: list[str], conversation_id: str | None = None)`

The embedding at line 2227 uses `getattr(self, "_current_user_id", None) or ""` — this is an undeclared instance attribute that is unreliable.

Add `user_id: str = ""` parameter. Update callers at line 2155 (and any other call site) to pass `user_id`.

### Task 4.2: Include `summary` in Qdrant payload (M-33, lines 2222–2226)

Currently the payload at lines 2222–2226 contains only `conversation_id`, `message_count`, `created_at`. TriSearch at `tri_search.py:304` reads `payload.get("summary", "")` — always empty.

Add `"summary": summary` to the payload dict:

```python
payload={
    "conversation_id": conversation_id,
    "summary": summary,
    "message_count": len(lines),
    "created_at": datetime.now(timezone.utc).isoformat(),
},
user_id=user_id,  # from parameter, not instance attribute
```

### Task 4.3: Embed conversations proactively, not just on overflow (H-20)

Currently conversation embedding only happens inside `_summarize_history` (line 2206), which is only called when history exceeds 8000 chars (line 2153). Most conversations never reach that threshold.

Add a new method `_embed_conversation_snapshot` that:
1. Takes `conversation_id`, `user_id`, and the conversation lines
2. Generates a summary via Haiku (reuse the summarization prompt)
3. Embeds into Qdrant `conversations` collection with the full payload (including `summary`)

Call it from `_load_conversation_history` when message count exceeds a threshold (e.g., every 5 messages) regardless of char length. Use a simple modulo check on message count.

Guard with the same `getattr(self, "_vector_store", None)` and `getattr(self, "_embedding_service", None)` checks.

### Task 4.4: Upgrade conversation embedding error log level (line 2230)

Change `logger.debug` at line 2230 to `logger.warning` — embedding failures should be visible in prod.

**Tests:** Mock `_embedding_service.embed_text` and `_vector_store.upsert`, verify `summary` key present in payload. Verify `user_id` passed correctly (not empty string). Verify embedding triggered for conversations with 5+ messages.

---

## Phase 5 — TriSearch Collection Mapping (L-17)

**File:** `backend/src/services/tri_search.py`

### Task 5.1: Verify `_collection_to_type` mapping (lines 390–400)

Current mapping at lines 392–399 already includes `"conversations": "conversation"` and `"approvals": "approval"`. This was added in Fix-4.

**Action:** Verify this is complete. If the entries exist (they do per the read at lines 390–400), mark as already fixed. No code change needed.

**Tests:** Add a unit test asserting `_collection_to_type("conversations") == "conversation"` and `_collection_to_type("approvals") == "approval"` to prevent regression.

---

## Verification

After all phases:

1. `ruff check backend/src/services/graph_engine.py backend/src/services/vector_store.py backend/src/services/tri_search.py backend/src/orchestrator/jarvis.py`
2. `pytest tests/ -v -k "graph_engine or vector_store or tri_search or summarize_history or conversation_embed"` — all new + existing tests pass
3. Manual: verify Neo4j/Qdrant failures log at WARNING level (not DEBUG or silent)
