# Fix-1: Security & Multi-tenant Fixes

**Priority:** P0 — must fix before any testing
**Risk:** Low — surgical fixes, no architectural changes
**Estimated files:** ~4

## Overview

This plan addresses two Cypher injection vulnerabilities in `graph_engine.py` and three multi-tenant workspace isolation leaks where Qdrant searches and a Postgres query omit `workspace_id` filtering, allowing data from one workspace to bleed into another. These are the highest priority fixes because they represent security boundaries that are currently broken.

## Phase 1: Cypher Injection Prevention (graph_engine.py)

### Task 1.1: Add allow-list to `sync_relationship`

- **File:** `backend/src/services/graph_engine.py`, lines 93-134
- **What:** Import `RELATION_TYPES` from `src.services.world_model` at the top of the file. At line 109 (before `label = relation_type.upper()...`), add:
  ```python
  if relation_type not in RELATION_TYPES:
      raise ValueError(
          f"Invalid relation_type {relation_type!r}; "
          f"must be one of {sorted(RELATION_TYPES)}"
      )
  ```
  This prevents arbitrary strings from being interpolated into the Cypher `MERGE (a)-[r:{label}]->(b)` pattern at line 117.
- **Test:** Add `test_sync_relationship_rejects_invalid_type` in `tests/test_graph_engine.py` — call `sync_relationship` with `relation_type="DETACH DELETE n //"` and assert `ValueError` is raised. Add a positive test with a valid type from `RELATION_TYPES` to confirm it still works.

### Task 1.2: Parameterize `traverse()` relation_types

- **File:** `backend/src/services/graph_engine.py`, lines 136-169
- **What:** Replace the f-string interpolation at line 146:
  ```python
  # BEFORE (line 144-146)
  rel_filter = ""
  if relation_types:
      rel_filter = f"AND ALL(r IN rels WHERE r.relation_type IN {relation_types})"
  ```
  With parameterized query + validation:
  ```python
  rel_filter = ""
  params = {"entity_id": entity_id, "user_id": user_id}
  if relation_types:
      invalid = set(relation_types) - RELATION_TYPES
      if invalid:
          raise ValueError(f"Invalid relation_types: {invalid}")
      rel_filter = "AND ALL(r IN rels WHERE r.relation_type IN $types)"
      params["types"] = relation_types
  ```
  Update the `session.run()` call at line 149 to pass `**params` instead of `entity_id=entity_id, user_id=user_id`.
- **Test:** Add `test_traverse_rejects_invalid_relation_types` — pass `relation_types=["works_on", "DROP DATABASE"]` and assert `ValueError`. Add `test_traverse_parameterizes_types` — pass valid types, mock the Neo4j session, and assert that `$types` appears in the query string and the params dict contains `types`.

## Phase 2: Multi-tenant Workspace Scoping

### Task 2.1: Scope `_composite_retrieve` Qdrant search by workspace_id

- **File:** `backend/src/services/memory_service.py`, lines 849-855
- **What:** The `_composite_retrieve` method already receives `workspace_id` as a parameter (line 833). Pass it as a filter to the Qdrant search call at line 850:
  ```python
  # BEFORE
  qdrant_results = await self._vector_store.search(
      "memories",
      query_embedding,
      user_id,
      limit=max_results * 2,
  )

  # AFTER
  qdrant_filters = {}
  if workspace_id:
      qdrant_filters["workspace_id"] = workspace_id
  qdrant_results = await self._vector_store.search(
      "memories",
      query_embedding,
      user_id,
      filters=qdrant_filters,
      limit=max_results * 2,
  )
  ```
  The `VectorStore.search` method (line 184-219) already supports a `filters: dict | None` parameter that appends `FieldCondition` entries to the Qdrant filter. No changes needed in `vector_store.py`.
- **Also:** Add `Memory.workspace_id == workspace_id` condition to the Postgres batch-fetch at lines 861-864 (the `stmt` query) to double-fence:
  ```python
  stmt = select(Memory).where(
      Memory.memory_id.in_(memory_ids),
      Memory.status == "active",
      Memory.workspace_id == workspace_id,
  )
  ```
- **Test:** Add `test_composite_retrieve_scopes_by_workspace` — create memories in two workspaces, call `_composite_retrieve` for workspace A, assert no results from workspace B leak through.

### Task 2.2: Scope fuzzy-match entity dedup by workspace_id

- **File:** `backend/src/services/world_model.py`, lines 406-423
- **What:** The `_find_by_name_or_alias` method already receives `workspace_id` (line 377). Pass it as a filter to the Qdrant `find_similar` call at line 411, and add a `workspace_id` condition to the subsequent DB query at lines 420-423:
  ```python
  # BEFORE (line 411-414)
  similar = await self._vector_store.find_similar(
      "entities",
      embedding,
      user_id,
      threshold=0.92,
      limit=1,
  )

  # AFTER
  similar = await self._vector_store.find_similar(
      "entities",
      embedding,
      user_id,
      threshold=0.92,
      limit=1,
      filters={"workspace_id": workspace_id} if workspace_id else None,
  )
  ```
  However, `find_similar` (line 221-231) delegates to `search` but does not accept a `filters` param. Add `filters: dict | None = None` to `find_similar`'s signature and pass it through:
  ```python
  # vector_store.py line 221-231
  async def find_similar(
      self,
      collection: str,
      query_vector: list[float],
      user_id: str,
      threshold: float = 0.9,
      limit: int = 5,
      filters: dict | None = None,  # ADD THIS
  ) -> list[dict]:
      results = await self.search(collection, query_vector, user_id, filters=filters, limit=limit)
      return [r for r in results if r.get("score", 0) >= threshold]
  ```
  Also scope the Postgres fallback query at line 420-421:
  ```python
  # BEFORE
  result = await self._db.execute(
      select(Entity).where(Entity.entity_id == eid)
  )

  # AFTER
  result = await self._db.execute(
      select(Entity).where(
          Entity.entity_id == eid,
          Entity.workspace_id == workspace_id,
      )
  )
  ```
- **Test:** Add `test_find_by_name_or_alias_qdrant_scoped_by_workspace` — mock `find_similar` and verify the `filters` kwarg contains `workspace_id`. Add a test that the Postgres fallback query includes the workspace_id condition.

### Task 2.3: Scope `_tick_persona_batch` by workspace

- **File:** `backend/src/services/scheduler.py`, lines 578-610
- **What:** The query at line 584 fetches the 20 most recent `InteractionLog` rows without any `workspace_id` filter. If multiple workspaces exist, interactions from different workspaces get mixed into a single persona analysis batch.

  Rewrite to group by `(workspace_id, user_id)` and process each group separately:
  ```python
  query = (
      select(InteractionLog)
      .order_by(InteractionLog.created_at.desc())
      .limit(50)  # fetch more to have enough per group
  )
  if last_batch:
      query = query.where(InteractionLog.created_at > last_batch)

  result = await db.execute(query)
  interactions = result.scalars().all()

  # Group by (workspace_id, user_id)
  from itertools import groupby
  from operator import attrgetter

  grouped: dict[tuple[str, str], list] = {}
  for i in interactions:
      key = (i.workspace_id, i.user_id)
      grouped.setdefault(key, []).append(i)

  for (ws_id, uid), group in grouped.items():
      if len(group) < 5:
          continue
      summary = "\n".join(
          f"- {i.message_preview or '(no preview)'} → {i.intent or 'unknown'}"
          for i in group
      )
      await self._orchestrator._call_agent(
          "persona",
          message=(
              "Analyze these recent user interactions and extract"
              f" preference patterns:\n{summary}"
          ),
          user_id=uid,
          workspace_id=ws_id,
      )
  ```
  Remove the single `user_id = interactions[0].user_id` / `workspace_id = getattr(...)` pattern that assumed all rows belonged to one user.
- **Test:** Add `test_persona_batch_groups_by_workspace` — insert interactions for 2 workspaces, verify `_call_agent` is called once per workspace (not once with mixed data). Verify groups with fewer than 5 interactions are skipped.

## Verification

- [ ] `ruff check src/ tests/` passes with no new violations
- [ ] All existing tests pass: `pytest tests/ -v`
- [ ] New tests for Cypher injection prevention (2 tests per task, 4 total)
- [ ] New tests for workspace scoping (1 per task, 3 total)
- [ ] Manual check: no other `f"` + Cypher patterns in `graph_engine.py`
- [ ] Manual check: no other `vector_store.search` calls missing `workspace_id` filter
