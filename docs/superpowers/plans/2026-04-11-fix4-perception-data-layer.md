# Fix-4: Perception & Data Layer Fixes

**Priority:** P1 — data integrity and feature correctness
**Risk:** Low-medium — mostly additive fixes, no architectural changes
**Estimated files:** ~8-10
**Dependencies:** None (independent of Fix-1, Fix-2, Fix-3)

## Overview

Two themes converge in this fix batch:

1. **Perception pipeline silent failures** — The relevance assessor and engagement service have bugs that silently suppress push-tier notifications. Haiku's code-fenced JSON responses cause `json.loads()` to fail (H-15), and engagement suppression has no recovery path once triggered (H-16/H-17).

2. **Data layer eviction/search gaps** — Qdrant vectors are orphaned when records are deleted or expired through multiple code paths (H-21, H-22, H-23). TriSearch excludes two Qdrant collections (H-25), ContextBuilder overwrites TriSearch results (H-24), and conversation embeddings are rarely written (H-20).

## Phase 1: Perception Pipeline Fixes

### Task 1.1: Strip code fences in relevance assessor (H-15)

**File:** `backend/src/services/relevance_assessor.py`
**Lines:** 124-125

**Problem:** `json.loads(text)` at line 125 fails when Haiku wraps its JSON response in markdown code fences (`` ```json\n{...}\n``` ``). The broad `except Exception` at line 132 catches this and returns a silent default assessment, meaning push-tier notifications are never delivered for affected signals.

**Fix:**
- Add `import re` at top of file.
- Insert a code-fence stripping helper before `json.loads()`:
  ```python
  text = re.sub(r"^```\w*\n?", "", text.strip()).rstrip("`").strip()
  ```
- Apply between line 124 (`text = response.content[0].text`) and line 125 (`data = json.loads(text)`).

**Tests:**
- Unit test `assess_relevance` with mocked client returning code-fenced JSON.
- Unit test the strip helper with plain JSON, `` ```json `` fences, and `` ``` `` fences.

### Task 1.2: Add engagement suppression TTL (H-16, H-17)

**File:** `backend/src/services/engagement_service.py`
**Lines:** 97-101 (`get_relevance_penalty`) and 103-113 (`is_suppressed`)

**Problem:** After 5 consecutive dismissals, `suppressed=True` is set (line 78). Only an `"engaged"` action clears it (line 72), but suppressed signals are never shown to the user, creating a permanent deadlock.

**Fix:**
- Add `_SUPPRESSION_TTL_DAYS = 7` constant near line 23.
- In `get_relevance_penalty()` (line 97), before returning `1.0` for suppressed rows, check `row.updated_at`:
  ```python
  if row.suppressed:
      ttl_cutoff = datetime.now(timezone.utc) - timedelta(days=_SUPPRESSION_TTL_DAYS)
      if row.updated_at and row.updated_at < ttl_cutoff:
          row.suppressed = False
          row.consecutive_dismissals = 0
          return 0.0
      return 1.0
  ```
- Apply the same TTL check in `is_suppressed()` (line 107) before returning `True`.
- Import `timedelta` at top (already imported: `datetime, timezone` from line 3).

**Tests:**
- Unit test: suppression clears after 7 days of inactivity.
- Unit test: suppression persists within 7 days.

## Phase 2: Qdrant Eviction Cascades

### Task 2.1: Add Qdrant cascade to approval eviction (H-21)

**File:** `backend/src/services/eviction_service.py`
**Lines:** 202-216 (`_evict_approvals`)

**Problem:** `_evict_approvals()` uses a bulk `DELETE` statement (line 207-211) which returns only `rowcount`, not the IDs needed for Qdrant cascade. The Qdrant `approvals` collection retains orphaned vectors.

**Fix:**
- Refactor to first SELECT approval IDs (like `_evict_memories` does), then cascade to Qdrant, then DELETE from Postgres:
  ```python
  async def _evict_approvals(self) -> int:
      from src.models.approvals import Approval

      cutoff = datetime.now(timezone.utc) - timedelta(days=APPROVAL_RETENTION_DAYS)
      stmt = (
          select(Approval.approval_id)
          .where(
              Approval.status.in_(["expired", "approved", "rejected"]),
              Approval.created_at < cutoff,
          )
      )
      result = await self._db.execute(stmt)
      approval_ids = [row[0] for row in result.all()]

      if not approval_ids:
          return 0

      await self._cascade_qdrant_delete("approvals", approval_ids)

      await self._db.execute(
          delete(Approval).where(Approval.approval_id.in_(approval_ids))
      )
      await self._db.flush()
      logger.info("Evicted %d approvals", len(approval_ids))
      return len(approval_ids)
  ```

**Tests:**
- Unit test: verify `_cascade_qdrant_delete("approvals", ...)` is called with correct IDs.

### Task 2.2: Add immediate Qdrant delete on memory deletion API (H-22)

**File:** `backend/src/api/routes_memories.py`
**Lines:** 230-253 (`delete_memory`)

**Problem:** `DELETE /v1/memories/{memory_id}` sets `memory.status = "expired"` (line 251) but does not delete the vector from Qdrant. The memory remains searchable for up to 7 days until `EvictionService` runs.

**Fix:**
- Add `vector_store` dependency injection to the route (from `ServiceContainer` or a new dep).
- After `memory.status = "expired"`, call:
  ```python
  if vector_store:
      try:
          await vector_store.delete("memories", memory_id)
      except Exception:
          logger.warning("Qdrant delete failed for memory %s", memory_id, exc_info=True)
  ```
- The `VectorStore.delete(collection, id)` signature takes `(str, str)` per `vector_store.py:233`.

**Tests:**
- Unit test: verify `vector_store.delete("memories", memory_id)` is called on DELETE.
- Unit test: route still succeeds if Qdrant delete fails.

### Task 2.3: Add Qdrant delete to heartbeat TTL expiry (H-23)

**File:** `backend/src/services/heartbeat.py`
**Lines:** 79-103 (`_expire_stale_memories`)

**Problem:** `_expire_stale_memories` sets `mem.status = "expired"` (line 97) but does not delete from Qdrant. Same pattern as H-22.

**Fix:**
- Accept optional `vector_store: VectorStore | None = None` in `HeartbeatService.__init__()` (line 31).
- After the expiry loop (after line 101 flush), delete expired memory vectors:
  ```python
  if expired_ids and self._vector_store:
      for mid in expired_ids:
          try:
              await self._vector_store.delete("memories", mid)
          except Exception:
              logger.debug("Qdrant delete failed for memory %s", mid, exc_info=True)
  ```
- Collect `expired_ids` during the loop (append `mem.memory_id` when marking expired).

**Tests:**
- Unit test: verify `vector_store.delete` called for each expired memory.

## Phase 3: Search & Context Completeness

### Task 3.1: Add conversations/approvals to TriSearch (H-25)

**File:** `backend/src/services/tri_search.py`
**Lines:** 282-284 (`_search_qdrant` method, line 284: `collections=["memories", "events", "artifacts"]`)

**Problem:** The `hybrid_search` call hardcodes 3 collections, excluding `conversations` and `approvals`.

**Fix:**
- Change the collections list at line 284 to:
  ```python
  collections=["memories", "events", "artifacts", "conversations", "approvals"],
  ```
- Update `_collection_to_type()` mapping at line 392-398 to include:
  ```python
  "conversations": "conversation",
  "approvals": "approval",
  ```

**Tests:**
- Unit test: verify `hybrid_search` is called with all 5 collections.
- Unit test: `_collection_to_type` returns correct types for new collections.

### Task 3.2: Fix ContextBuilder entity double-write (H-24)

**File:** `backend/src/services/context_builder.py`
**Lines:** 147-158

**Problem:** The world-model entity lookup at lines 148-158 unconditionally overwrites `pack.entities` (line 157), discarding any entities populated by TriSearch at lines 123-131.

**Fix:**
- Guard the world-model fallback with a check:
  ```python
  if not pack.entities and self._world_model and query:
  ```
  Change line 148 from `if self._world_model and query:` to `if not pack.entities and self._world_model and query:`.

**Tests:**
- Unit test: when TriSearch returns entities, world-model fallback is skipped.
- Unit test: when TriSearch returns no entities, world-model fallback runs.

## Phase 4: Conversation Embedding Coverage

### Task 4.1: Improve conversation embedding frequency (H-20)

**File:** `backend/src/orchestrator/jarvis.py`
**Lines:** 2208-2236

**Problem:** Conversation summaries are only embedded into Qdrant during history summarization (when total chars exceed 8000). Most conversations never reach this threshold, so the `conversations` Qdrant collection stays empty. Additionally, `_current_user_id` (line 2229) is an undeclared instance attribute that may be empty.

**Fix:**
- Add periodic embedding at a lower threshold: after every N messages (e.g., 10), or when a conversation ends, embed a summary into Qdrant regardless of char count.
- In `_load_conversation_history()`, after building the lines list, if `len(lines) >= 10` and no summarization occurred, trigger a lightweight embedding of the most recent messages.
- Pass `user_id` explicitly through the call chain instead of relying on `getattr(self, "_current_user_id", None)`. The `_load_conversation_history` method already receives `user_id` — thread it through to `_summarize_history`.
- Update `_summarize_history` signature to accept `user_id: str` parameter.

**Tests:**
- Unit test: conversations with 10+ messages get embedded even under 8000 chars.
- Unit test: `user_id` is passed explicitly to the Qdrant upsert call.

## Verification

- [ ] `ruff check src/ tests/` passes with no errors
- [ ] `ruff format --check src/ tests/` passes
- [ ] `pytest tests/ -v -k "relevance"` — code-fence stripping tests pass
- [ ] `pytest tests/ -v -k "engagement"` — suppression TTL tests pass
- [ ] `pytest tests/ -v -k "eviction"` — Qdrant cascade tests pass (approvals, memories)
- [ ] `pytest tests/ -v -k "heartbeat"` — Qdrant delete on expiry tests pass
- [ ] `pytest tests/ -v -k "tri_search"` — 5-collection search tests pass
- [ ] `pytest tests/ -v -k "context_builder"` — entity double-write fix tests pass
- [ ] `pytest tests/ -v -k "conversation"` — embedding frequency tests pass
- [ ] Full suite: `pytest tests/ -v` — no regressions
- [ ] Manual: trigger a perception signal with Haiku returning code-fenced JSON — verify push notification is delivered
- [ ] Manual: verify suppressed engagement auto-clears after 7 days (use DB time manipulation)
