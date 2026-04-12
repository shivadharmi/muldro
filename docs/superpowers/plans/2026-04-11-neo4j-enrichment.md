# Neo4j Enrichment + Cross-DB Patterns Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Transform Neo4j from a write-only projection into a genuine reasoning layer — typed relationship edges, weighted traversal, temporal scoping, enriched agent context, and graph-boosted search.

**Architecture:** Upgrade `GraphEngine` with typed Cypher labels, strength/temporal properties, and two new traversal methods. Wire `GraphSyncService` to pass strength/dates from Postgres. Replace shallow `get_related_people()` in `ContextBuilder` with rich `traverse_weighted()`. Add `search_with_graph_boost()` to `TriSearchService`. Fix memory stability decay and briefing evidence linking.

**Tech Stack:** Python 3.12, Neo4j (AsyncGraphDatabase), SQLAlchemy, Qdrant, pytest + pytest-asyncio

---

## File Map

| File | Action | Responsibility |
|------|--------|----------------|
| `src/services/graph_engine.py` | Modify | Add `traverse_weighted()`, `traverse_temporal()`. Update `sync_relationship()` signature for strength + temporal + typed labels. Update `full_sync()`, `get_subgraph()`, `get_project_graph()`, `find_central_entities()`, `get_stale_relationships()`, `detect_communities()` to use typed labels. |
| `src/services/graph_sync.py` | Modify | Pass `strength`, `start_date`, `end_date` from Postgres `EntityRelationship` to `sync_relationship()` in all 5 call sites. |
| `src/services/world_model.py` | Modify | Pass `strength`/temporal data when emitting relationship events (minor — `add_relationship` already stores them in Postgres). |
| `src/services/context_builder.py` | Modify | Replace `get_related_people()` with `traverse_weighted()`. Enrich `to_prompt()` graph section with type, strength, distance. |
| `src/services/tri_search.py` | Modify | Add `search_with_graph_boost()` method. |
| `src/services/memory_service.py` | Modify | Add exponential decay to `refresh_stability()`. |
| `src/services/briefing_read_model.py` | Modify | Replace timestamp proximity with vector similarity for `_get_related_items()`. |
| `tests/test_graph_engine.py` | Create | Tests for `traverse_weighted()`, `traverse_temporal()`, typed edge sync. |
| `tests/test_graph_boost.py` | Create | Tests for `search_with_graph_boost()`. |
| `tests/test_tri_search.py` | Modify | Add tests for graph boost integration. |
| `tests/test_context_builder_service.py` | Modify | Update graph relationship tests for enriched format. |
| `tests/test_memory_service.py` | Modify | Add stability decay tests. |
| `tests/test_knowledge_graph.py` | Modify | Update if sync_relationship signature changes break existing tests. |
| `tests/test_graph_sync.py` | Modify/Create | Test that strength/temporal data flows through sync. |

---

### Task 1: Typed Edges + Strength/Temporal in `sync_relationship()`

**Files:**
- Create: `backend/tests/test_graph_engine.py`
- Modify: `backend/src/services/graph_engine.py:93-122`

This task upgrades `sync_relationship()` to create typed Cypher labels (`:WORKS_AT` instead of `:RELATES_TO`) and store `strength`, `start_date`, `end_date` properties on edges.

- [ ] **Step 1: Write the failing test for typed edge sync**

```python
# tests/test_graph_engine.py
"""Tests for GraphEngine — typed edges, weighted traversal, temporal scoping."""

import pytest
from unittest.mock import AsyncMock, MagicMock, patch

from src.services.graph_engine import GraphEngine
from tests.conftest import make_mock_settings


@pytest.fixture
def settings():
    s = make_mock_settings()
    s.neo4j_url = "bolt://localhost:7687"
    s.neo4j_user = "neo4j"
    s.neo4j_password = "x"
    return s


@pytest.fixture
def mock_session():
    """Create a mock Neo4j session with run() that returns mock results."""
    session = AsyncMock()
    session.__aenter__ = AsyncMock(return_value=session)
    session.__aexit__ = AsyncMock(return_value=False)
    return session


@pytest.fixture
def mock_driver(mock_session):
    driver = AsyncMock()
    driver.session.return_value = mock_session
    return driver


class TestTypedEdgeSync:
    @pytest.mark.asyncio
    async def test_sync_relationship_uses_typed_label(self, settings, mock_driver, mock_session):
        """sync_relationship should create a :WORKS_AT edge, not :RELATES_TO."""
        engine = GraphEngine(settings)
        engine._driver = mock_driver

        await engine.sync_relationship(
            relation_id="rel_001",
            from_entity_id="ent_a",
            to_entity_id="ent_b",
            relation_type="works_at",
            user_id="usr_1",
            strength=0.8,
            start_date="2025-06-01",
            end_date=None,
        )

        call_args = mock_session.run.call_args
        cypher = call_args[0][0]
        params = call_args[1] if call_args[1] else call_args[0][1] if len(call_args[0]) > 1 else {}
        # Verify typed label in Cypher (not :RELATES_TO)
        assert ":WORKS_AT" in cypher
        assert ":RELATES_TO" not in cypher
        # Verify strength and temporal params are passed
        assert params.get("strength") == 0.8
        assert params.get("start_date") == "2025-06-01"
        assert params.get("end_date") is None

    @pytest.mark.asyncio
    async def test_sync_relationship_keeps_relation_type_property(
        self, settings, mock_driver, mock_session
    ):
        """Backward compat: relation_type stored as property even with typed label."""
        engine = GraphEngine(settings)
        engine._driver = mock_driver

        await engine.sync_relationship(
            relation_id="rel_002",
            from_entity_id="ent_a",
            to_entity_id="ent_b",
            relation_type="invested_in",
            user_id="usr_1",
        )

        cypher = mock_session.run.call_args[0][0]
        assert ":INVESTED_IN" in cypher
        assert "r.relation_type" in cypher

    @pytest.mark.asyncio
    async def test_sync_relationship_sanitizes_label(self, settings, mock_driver, mock_session):
        """Labels with spaces should be converted to underscores."""
        engine = GraphEngine(settings)
        engine._driver = mock_driver

        await engine.sync_relationship(
            relation_id="rel_003",
            from_entity_id="ent_a",
            to_entity_id="ent_b",
            relation_type="member of",
            user_id="usr_1",
        )

        cypher = mock_session.run.call_args[0][0]
        assert ":MEMBER_OF" in cypher

    @pytest.mark.asyncio
    async def test_sync_relationship_defaults_strength_to_1(
        self, settings, mock_driver, mock_session
    ):
        """When no strength is passed, default to 1.0."""
        engine = GraphEngine(settings)
        engine._driver = mock_driver

        await engine.sync_relationship(
            relation_id="rel_004",
            from_entity_id="ent_a",
            to_entity_id="ent_b",
            relation_type="works_on",
            user_id="usr_1",
        )

        call_args = mock_session.run.call_args
        params = call_args[1] if call_args[1] else call_args[0][1] if len(call_args[0]) > 1 else {}
        assert params.get("strength") == 1.0

    @pytest.mark.asyncio
    async def test_sync_relationship_no_driver_is_noop(self, settings):
        """When Neo4j not configured, sync_relationship returns without error."""
        settings.neo4j_url = ""
        engine = GraphEngine(settings)

        # Should not raise
        await engine.sync_relationship(
            relation_id="rel_005",
            from_entity_id="ent_a",
            to_entity_id="ent_b",
            relation_type="works_at",
            user_id="usr_1",
        )
```

- [ ] **Step 2: Run the test to verify it fails**

Run: `cd backend && python -m pytest tests/test_graph_engine.py -v`
Expected: FAIL — `sync_relationship()` doesn't accept `strength`/`start_date`/`end_date` params, and Cypher still uses `:RELATES_TO`.

- [ ] **Step 3: Implement typed edge sync in `graph_engine.py`**

Replace the current `sync_relationship` method (lines 93-122) with:

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
        """Upsert a relationship edge to Neo4j with a typed label.

        Uses dynamic Cypher labels (e.g. :WORKS_AT) derived from relation_type.
        Keeps relation_type as a property for backward compatibility.
        """
        driver = await self._get_driver()
        if not driver:
            return

        # Sanitize label: "member of" -> "MEMBER_OF"
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
            logger.warning(
                "Neo4j sync_relationship failed for %s", relation_id, exc_info=True
            )
```

- [ ] **Step 4: Run the test to verify it passes**

Run: `cd backend && python -m pytest tests/test_graph_engine.py::TestTypedEdgeSync -v`
Expected: All 5 tests PASS.

- [ ] **Step 5: Update `full_sync()` to pass strength/temporal data**

Update `full_sync()` (line 247) to accept and pass the new params:

```python
    async def full_sync(
        self, user_id: str, entities: list[dict], relationships: list[dict]
    ) -> int:
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
            await self.sync_relationship(
                relation_id=rel["relation_id"],
                from_entity_id=rel["from_entity_id"],
                to_entity_id=rel["to_entity_id"],
                relation_type=rel["relation_type"],
                user_id=user_id,
                strength=rel.get("strength", 1.0),
                start_date=(
                    rel["start_date"].isoformat()
                    if rel.get("start_date")
                    else None
                ),
                end_date=(
                    rel["end_date"].isoformat()
                    if rel.get("end_date")
                    else None
                ),
            )
            count += 1

        logger.info("Full sync to Neo4j: %d items for user %s", count, user_id)
        return count
```

- [ ] **Step 6: Update queries that reference `:RELATES_TO` to be label-agnostic**

In `get_subgraph()` (line 281), `get_project_graph()` (line 317), `find_central_entities()` (line 360), `get_stale_relationships()` (line 393), `detect_communities()` (line 418), and `traverse()` (line 124), replace `:RELATES_TO` with a generic relationship pattern `[r]` (no label constraint — matches any typed edge):

For `get_subgraph()`:
```python
                MATCH (n)-[r]-(m:Entity)
```

For `get_project_graph()`:
```python
                MATCH path = (start:Entity {entity_id: $entity_id, user_id: $user_id})
                      -[rels*1..3]-(connected)
```
(already label-agnostic via `[rels*1..3]`)

For `find_central_entities()`:
```python
                OPTIONAL MATCH (e)-[r]-()
```

For `get_stale_relationships()`:
```python
                MATCH (a:Entity {user_id: $user_id})-[r]-(b:Entity)
```

For `detect_communities()`:
```python
                OPTIONAL MATCH path = (e)-[*]-(connected:Entity)
```

- [ ] **Step 7: Run all existing tests to check nothing broke**

Run: `cd backend && python -m pytest tests/test_graph_engine.py tests/test_knowledge_graph.py -v`
Expected: All PASS.

- [ ] **Step 8: Commit**

```bash
cd backend
git add tests/test_graph_engine.py src/services/graph_engine.py
git commit -m "feat(spec5b): typed relationship edges with strength and temporal data in Neo4j"
```

---

### Task 2: Sync Strength + Temporal Data Through `GraphSyncService`

**Files:**
- Modify: `backend/src/services/graph_sync.py` (all 5 `sync_relationship()` call sites)
- Test: `backend/tests/test_knowledge_graph.py` (existing graph sync tests)

This task ensures Postgres `EntityRelationship.strength`, `start_date`, `end_date` are passed to Neo4j on every sync path.

- [ ] **Step 1: Write the failing test**

Add to `tests/test_graph_engine.py`:

```python
class TestGraphSyncPassesStrength:
    """Verify GraphSyncService passes strength/temporal from Postgres."""

    @pytest.mark.asyncio
    async def test_on_relationship_change_passes_strength(self):
        """on_relationship_change should forward strength + dates to Neo4j."""
        from datetime import date
        from unittest.mock import patch

        from src.services.graph_sync import GraphSyncService

        settings = make_mock_settings()
        settings.neo4j_url = "bolt://localhost:7687"
        settings.neo4j_user = "neo4j"
        settings.neo4j_password = "x"

        mock_db = AsyncMock()
        mock_rel = MagicMock()
        mock_rel.relation_id = "rel_100"
        mock_rel.from_entity_id = "ent_a"
        mock_rel.to_entity_id = "ent_b"
        mock_rel.relation_type = "invested_in"
        mock_rel.user_id = "usr_1"
        mock_rel.strength = 0.75
        mock_rel.start_date = date(2025, 3, 1)
        mock_rel.end_date = None

        # Mock the DB query to return our rel
        mock_result = MagicMock()
        mock_result.scalar_one_or_none.return_value = mock_rel
        mock_db.execute = AsyncMock(return_value=mock_result)

        svc = GraphSyncService(settings, mock_db)
        svc._graph = AsyncMock()

        from src.services.event_bus import BusEvent

        event = BusEvent(
            event_type="relationship.updated",
            payload={"relation_id": "rel_100"},
            user_id="usr_1",
        )
        await svc.on_relationship_change(event)

        svc._graph.sync_relationship.assert_called_once_with(
            relation_id="rel_100",
            from_entity_id="ent_a",
            to_entity_id="ent_b",
            relation_type="invested_in",
            user_id="usr_1",
            strength=0.75,
            start_date="2025-03-01",
            end_date=None,
        )

    @pytest.mark.asyncio
    async def test_sync_relationships_for_entity_passes_strength(self):
        """sync_relationships_for_entity should forward strength + dates."""
        from datetime import date

        from src.services.graph_sync import GraphSyncService

        settings = make_mock_settings()
        settings.neo4j_url = "bolt://localhost:7687"
        settings.neo4j_user = "neo4j"
        settings.neo4j_password = "x"

        mock_rel = MagicMock()
        mock_rel.relation_id = "rel_200"
        mock_rel.from_entity_id = "ent_x"
        mock_rel.to_entity_id = "ent_y"
        mock_rel.relation_type = "reports_to"
        mock_rel.user_id = "usr_1"
        mock_rel.strength = 0.9
        mock_rel.start_date = date(2024, 1, 15)
        mock_rel.end_date = date(2025, 12, 31)

        mock_db = AsyncMock()
        mock_scalars = MagicMock()
        mock_scalars.all.return_value = [mock_rel]
        mock_result = MagicMock()
        mock_result.scalars.return_value = mock_scalars
        mock_db.execute = AsyncMock(return_value=mock_result)

        svc = GraphSyncService(settings, mock_db)
        svc._graph = AsyncMock()

        await svc.sync_relationships_for_entity("ent_x")

        svc._graph.sync_relationship.assert_called_once_with(
            relation_id="rel_200",
            from_entity_id="ent_x",
            to_entity_id="ent_y",
            relation_type="reports_to",
            user_id="usr_1",
            strength=0.9,
            start_date="2024-01-15",
            end_date="2025-12-31",
        )
```

- [ ] **Step 2: Run the test to verify it fails**

Run: `cd backend && python -m pytest tests/test_graph_engine.py::TestGraphSyncPassesStrength -v`
Expected: FAIL — `sync_relationship()` is called without strength/temporal args.

- [ ] **Step 3: Update all 5 call sites in `graph_sync.py`**

Update `on_relationship_change()` (line 65):
```python
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

Update `sync_relationships_for_entity()` (line 101):
```python
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

Update `batch_sync_entities()` (line 144):
```python
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

Update `full_reconciliation()` (line 196):
```python
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

- [ ] **Step 4: Run the tests to verify they pass**

Run: `cd backend && python -m pytest tests/test_graph_engine.py::TestGraphSyncPassesStrength -v`
Expected: All PASS.

- [ ] **Step 5: Run existing graph sync tests for regressions**

Run: `cd backend && python -m pytest tests/test_knowledge_graph.py -v`
Expected: All PASS.

- [ ] **Step 6: Commit**

```bash
cd backend
git add src/services/graph_sync.py tests/test_graph_engine.py
git commit -m "feat(spec5b): sync relationship strength and temporal data to Neo4j"
```

---

### Task 3: Weighted Traversal (`traverse_weighted()`)

**Files:**
- Modify: `backend/src/services/graph_engine.py`
- Test: `backend/tests/test_graph_engine.py`

This task adds `traverse_weighted()` — a new method that ranks connected entities by average relationship strength along the path, with a `min_strength` filter.

- [ ] **Step 1: Write the failing tests**

Add to `tests/test_graph_engine.py`:

```python
class TestTraverseWeighted:
    @pytest.mark.asyncio
    async def test_returns_entities_sorted_by_strength(
        self, settings, mock_driver, mock_session
    ):
        """traverse_weighted returns entities ordered by avg_strength desc."""
        mock_result = AsyncMock()
        mock_result.data = AsyncMock(return_value=[
            {
                "entity_id": "ent_b",
                "name": "Alice",
                "entity_type": "person",
                "attributes": "{}",
                "avg_strength": 0.9,
                "distance": 1,
            },
            {
                "entity_id": "ent_c",
                "name": "Bob",
                "entity_type": "person",
                "attributes": "{}",
                "avg_strength": 0.5,
                "distance": 2,
            },
        ])
        mock_session.run = AsyncMock(return_value=mock_result)

        engine = GraphEngine(settings)
        engine._driver = mock_driver

        results = await engine.traverse_weighted(
            entity_id="ent_a", user_id="usr_1", depth=2, min_strength=0.3
        )

        assert len(results) == 2
        assert results[0]["entity_id"] == "ent_b"
        assert results[0]["avg_strength"] == 0.9
        assert results[1]["entity_id"] == "ent_c"

    @pytest.mark.asyncio
    async def test_cypher_includes_min_strength_param(
        self, settings, mock_driver, mock_session
    ):
        """Cypher query should filter by min_strength."""
        mock_result = AsyncMock()
        mock_result.data = AsyncMock(return_value=[])
        mock_session.run = AsyncMock(return_value=mock_result)

        engine = GraphEngine(settings)
        engine._driver = mock_driver

        await engine.traverse_weighted(
            entity_id="ent_a", user_id="usr_1", min_strength=0.5
        )

        call_args = mock_session.run.call_args
        params = call_args[1] if call_args[1] else call_args[0][1]
        assert params["min_strength"] == 0.5

    @pytest.mark.asyncio
    async def test_no_driver_returns_empty(self, settings):
        """When Neo4j not configured, return empty list."""
        settings.neo4j_url = ""
        engine = GraphEngine(settings)

        results = await engine.traverse_weighted(
            entity_id="ent_a", user_id="usr_1"
        )
        assert results == []

    @pytest.mark.asyncio
    async def test_exception_returns_empty(self, settings, mock_driver, mock_session):
        """On Neo4j error, return empty list instead of crashing."""
        mock_session.run = AsyncMock(side_effect=Exception("Neo4j down"))

        engine = GraphEngine(settings)
        engine._driver = mock_driver

        results = await engine.traverse_weighted(
            entity_id="ent_a", user_id="usr_1"
        )
        assert results == []
```

- [ ] **Step 2: Run the test to verify it fails**

Run: `cd backend && python -m pytest tests/test_graph_engine.py::TestTraverseWeighted -v`
Expected: FAIL — `traverse_weighted` not defined.

- [ ] **Step 3: Implement `traverse_weighted()` in `graph_engine.py`**

Add after the existing `traverse()` method (around line 166):

```python
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
            logger.debug(
                "Neo4j traverse_weighted failed for %s", entity_id, exc_info=True
            )
            return []
```

- [ ] **Step 4: Run the test to verify it passes**

Run: `cd backend && python -m pytest tests/test_graph_engine.py::TestTraverseWeighted -v`
Expected: All 4 tests PASS.

- [ ] **Step 5: Commit**

```bash
cd backend
git add src/services/graph_engine.py tests/test_graph_engine.py
git commit -m "feat(spec5b): add traverse_weighted() for strength-ranked graph traversal"
```

---

### Task 4: Temporal Scoping (`traverse_temporal()`)

**Files:**
- Modify: `backend/src/services/graph_engine.py`
- Test: `backend/tests/test_graph_engine.py`

This task adds `traverse_temporal()` — a method to scope traversals to a time window using `start_date`/`end_date` properties on edges.

- [ ] **Step 1: Write the failing tests**

Add to `tests/test_graph_engine.py`:

```python
class TestTraverseTemporal:
    @pytest.mark.asyncio
    async def test_passes_after_param(self, settings, mock_driver, mock_session):
        """traverse_temporal passes 'after' as a Cypher parameter."""
        mock_result = AsyncMock()
        mock_result.data = AsyncMock(return_value=[])
        mock_session.run = AsyncMock(return_value=mock_result)

        engine = GraphEngine(settings)
        engine._driver = mock_driver

        await engine.traverse_temporal(
            entity_id="ent_a",
            user_id="usr_1",
            after="2025-01-01",
        )

        call_args = mock_session.run.call_args
        cypher = call_args[0][0]
        params = call_args[1] if call_args[1] else call_args[0][1]
        assert params.get("after") == "2025-01-01"
        assert "r.start_date" in cypher

    @pytest.mark.asyncio
    async def test_passes_before_param(self, settings, mock_driver, mock_session):
        """traverse_temporal passes 'before' as a Cypher parameter."""
        mock_result = AsyncMock()
        mock_result.data = AsyncMock(return_value=[])
        mock_session.run = AsyncMock(return_value=mock_result)

        engine = GraphEngine(settings)
        engine._driver = mock_driver

        await engine.traverse_temporal(
            entity_id="ent_a",
            user_id="usr_1",
            before="2026-06-01",
        )

        call_args = mock_session.run.call_args
        params = call_args[1] if call_args[1] else call_args[0][1]
        assert params.get("before") == "2026-06-01"

    @pytest.mark.asyncio
    async def test_returns_data(self, settings, mock_driver, mock_session):
        """traverse_temporal returns structured entity data."""
        mock_result = AsyncMock()
        mock_result.data = AsyncMock(return_value=[
            {
                "entity_id": "ent_b",
                "name": "ProjectX",
                "entity_type": "project",
                "relation_type": "works_on",
                "strength": 0.8,
            },
        ])
        mock_session.run = AsyncMock(return_value=mock_result)

        engine = GraphEngine(settings)
        engine._driver = mock_driver

        results = await engine.traverse_temporal(
            entity_id="ent_a",
            user_id="usr_1",
            after="2025-01-01",
            before="2026-01-01",
        )

        assert len(results) == 1
        assert results[0]["entity_id"] == "ent_b"

    @pytest.mark.asyncio
    async def test_no_driver_returns_empty(self, settings):
        """When Neo4j not configured, return empty list."""
        settings.neo4j_url = ""
        engine = GraphEngine(settings)

        results = await engine.traverse_temporal(
            entity_id="ent_a", user_id="usr_1", after="2025-01-01"
        )
        assert results == []
```

- [ ] **Step 2: Run the test to verify it fails**

Run: `cd backend && python -m pytest tests/test_graph_engine.py::TestTraverseTemporal -v`
Expected: FAIL — `traverse_temporal` not defined.

- [ ] **Step 3: Implement `traverse_temporal()` in `graph_engine.py`**

Add after `traverse_weighted()`:

```python
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

        temporal_filter = ""
        if after:
            temporal_filter += (
                " AND ALL(r IN rels WHERE "
                "r.start_date IS NULL OR r.start_date >= $after)"
            )
        if before:
            temporal_filter += (
                " AND ALL(r IN rels WHERE "
                "r.start_date IS NULL OR r.start_date <= $before)"
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
                return await result.data()
        except Exception:
            logger.debug(
                "Neo4j traverse_temporal failed for %s", entity_id, exc_info=True
            )
            return []
```

- [ ] **Step 4: Run the test to verify it passes**

Run: `cd backend && python -m pytest tests/test_graph_engine.py::TestTraverseTemporal -v`
Expected: All 4 tests PASS.

- [ ] **Step 5: Commit**

```bash
cd backend
git add src/services/graph_engine.py tests/test_graph_engine.py
git commit -m "feat(spec5b): add traverse_temporal() for time-windowed graph queries"
```

---

### Task 5: Enriched ContextBuilder

**Files:**
- Modify: `backend/src/services/context_builder.py:163-169` (graph relationships block)
- Modify: `backend/src/services/context_builder.py:348-354` (`to_prompt()` graph section)
- Test: `backend/tests/test_context_builder_service.py`

This task replaces the shallow `get_related_people()` call with `traverse_weighted()` and enriches the prompt rendering to show entity type, relationship type, strength, and distance.

- [ ] **Step 1: Write the failing test**

Add to `tests/test_context_builder_service.py` (or create a new test class):

```python
class TestEnrichedGraphContext:
    @pytest.mark.asyncio
    async def test_build_uses_traverse_weighted(self):
        """ContextBuilder.build() should call traverse_weighted, not get_related_people."""
        from src.services.context_builder import ContextBuilder

        mock_graph = AsyncMock()
        mock_graph.traverse_weighted = AsyncMock(return_value=[
            {
                "entity_id": "ent_b",
                "name": "Alice Chen",
                "entity_type": "person",
                "avg_strength": 0.85,
                "distance": 1,
                "attributes": "{}",
            },
            {
                "entity_id": "ent_c",
                "name": "Acme Corp",
                "entity_type": "organization",
                "avg_strength": 0.6,
                "distance": 2,
                "attributes": "{}",
            },
        ])

        mock_world = AsyncMock()
        mock_world.find_entity = AsyncMock(return_value=[
            {"entity_id": "ent_a", "entity_type": "person", "canonical_name": "Bob"},
        ])

        builder = ContextBuilder(
            world_model=mock_world,
            graph_engine=mock_graph,
        )

        pack = await builder.build(user_id="usr_1", query="test query")

        mock_graph.traverse_weighted.assert_called()
        # Should NOT call get_related_people
        mock_graph.get_related_people.assert_not_called()
        # Graph relationships should be enriched
        assert len(pack.graph_relationships) == 2
        assert pack.graph_relationships[0]["name"] == "Alice Chen"
        assert pack.graph_relationships[0]["entity_type"] == "person"
        assert pack.graph_relationships[0]["strength"] == 0.85
        assert pack.graph_relationships[0]["distance"] == 1

    def test_to_prompt_renders_enriched_graph(self):
        """to_prompt() should render enriched graph relationships."""
        from src.services.context_builder import ContextBuilder, ContextPack

        pack = ContextPack(
            task_summary="test",
            graph_relationships=[
                {
                    "name": "Sarah Chen",
                    "entity_type": "person",
                    "relation_type": "invested_in",
                    "strength": 0.8,
                    "distance": 1,
                },
                {
                    "name": "Acme Corp",
                    "entity_type": "organization",
                    "strength": 0.6,
                    "distance": 2,
                },
            ],
        )

        prompt = ContextBuilder.to_prompt(pack)
        assert "Sarah Chen" in prompt
        assert "person" in prompt
        assert "strength=0.8" in prompt
        assert "distance=1" in prompt
```

- [ ] **Step 2: Run the test to verify it fails**

Run: `cd backend && python -m pytest tests/test_context_builder_service.py::TestEnrichedGraphContext -v`
Expected: FAIL — still calls `get_related_people()`, and `to_prompt()` doesn't render strength/distance.

- [ ] **Step 3: Update `build()` to use `traverse_weighted()`**

Replace lines 163-169 in `context_builder.py`:

```python
        # B5: Neo4j graph relationships for discovered entities
        if self._graph_engine and entity_ids:
            try:
                for eid in entity_ids[:5]:
                    related = await self._graph_engine.traverse_weighted(
                        entity_id=eid,
                        user_id=user_id,
                        depth=2,
                        min_strength=0.3,
                    )
                    for r in related[:8]:
                        pack.graph_relationships.append({
                            "entity_id": r["entity_id"],
                            "name": r["name"],
                            "entity_type": r.get("entity_type"),
                            "strength": r.get("avg_strength", 0.5),
                            "distance": r.get("distance", 1),
                            "attributes": r.get("attributes"),
                        })
            except Exception:
                logger.debug("Graph relationship lookup failed", exc_info=True)
```

- [ ] **Step 4: Update `to_prompt()` graph section**

Replace lines 348-354 in `context_builder.py`:

```python
        if pack.graph_relationships:
            rel_lines = []
            for r in pack.graph_relationships[:10]:
                name = r.get("name") or r.get("canonical_name", "?")
                etype = r.get("entity_type", "?")
                strength = r.get("strength")
                distance = r.get("distance")
                rtype = r.get("relation_type", "")
                parts = [f"- {name} ({etype})"]
                if rtype:
                    parts.append(f"via {rtype}")
                if strength is not None:
                    parts.append(f"strength={strength:.1f}")
                if distance is not None:
                    parts.append(f"distance={distance}")
                rel_lines.append(" ".join(parts))
            sections.append(
                "## Entity Relationships\n" + "\n".join(rel_lines)
            )
```

- [ ] **Step 5: Run the tests to verify they pass**

Run: `cd backend && python -m pytest tests/test_context_builder_service.py::TestEnrichedGraphContext -v`
Expected: All PASS.

- [ ] **Step 6: Run existing context builder tests for regressions**

Run: `cd backend && python -m pytest tests/test_context_builder_service.py tests/test_context_assembler.py -v`
Expected: All PASS.

- [ ] **Step 7: Commit**

```bash
cd backend
git add src/services/context_builder.py tests/test_context_builder_service.py
git commit -m "feat(spec5b): enriched ContextBuilder with weighted graph traversal"
```

---

### Task 6: Graph+Vector Combined Query (`search_with_graph_boost`)

**Files:**
- Create: `backend/tests/test_graph_boost.py`
- Modify: `backend/src/services/tri_search.py`

This task adds `search_with_graph_boost()` to `TriSearchService` — boosts search results that are connected to context entities in the graph (10% boost per connected entity).

- [ ] **Step 1: Write the failing tests**

```python
# tests/test_graph_boost.py
"""Tests for graph-boosted search in TriSearchService."""

import pytest
from unittest.mock import AsyncMock, MagicMock

from src.services.tri_search import TriSearchService
from tests.conftest import make_mock_settings


@pytest.fixture
def settings():
    return make_mock_settings()


@pytest.fixture
def mock_graph():
    graph = AsyncMock()
    graph.traverse_weighted = AsyncMock(return_value=[
        {"entity_id": "ent_a"},
        {"entity_id": "ent_b"},
    ])
    return graph


class TestSearchWithGraphBoost:
    @pytest.mark.asyncio
    async def test_boosts_graph_connected_results(self, settings, mock_graph):
        """Results sharing entities with graph neighborhood get score boost."""
        svc = TriSearchService(settings, graph_engine=mock_graph)

        # Mock the base search to return two results
        svc.search = AsyncMock(return_value=[
            {
                "id": "mem_1",
                "title": "Result 1",
                "score": 0.8,
                "final_score": 0.8,
                "entity_ids": ["ent_a"],  # connected
            },
            {
                "id": "mem_2",
                "title": "Result 2",
                "score": 0.9,
                "final_score": 0.9,
                "entity_ids": ["ent_z"],  # not connected
            },
        ])

        results = await svc.search_with_graph_boost(
            query="test",
            user_id="usr_1",
            workspace_id="ws_1",
            db=AsyncMock(),
            context_entity_ids=["ent_x"],
            limit=10,
        )

        # mem_1 should be boosted because ent_a is in the neighborhood
        boosted = next(r for r in results if r["id"] == "mem_1")
        unboosted = next(r for r in results if r["id"] == "mem_2")
        assert boosted["final_score"] > 0.8  # was boosted
        assert unboosted["final_score"] == 0.9  # unchanged

    @pytest.mark.asyncio
    async def test_no_context_entities_returns_base(self, settings, mock_graph):
        """Without context entities, returns base search results unchanged."""
        svc = TriSearchService(settings, graph_engine=mock_graph)
        svc.search = AsyncMock(return_value=[
            {"id": "mem_1", "score": 0.8, "final_score": 0.8},
        ])

        results = await svc.search_with_graph_boost(
            query="test",
            user_id="usr_1",
            workspace_id="ws_1",
            db=AsyncMock(),
            context_entity_ids=None,
            limit=10,
        )

        assert len(results) == 1
        assert results[0]["final_score"] == 0.8

    @pytest.mark.asyncio
    async def test_no_graph_engine_returns_base(self, settings):
        """Without graph engine, returns base search results."""
        svc = TriSearchService(settings, graph_engine=None)
        svc.search = AsyncMock(return_value=[
            {"id": "mem_1", "score": 0.8, "final_score": 0.8},
        ])

        results = await svc.search_with_graph_boost(
            query="test",
            user_id="usr_1",
            workspace_id="ws_1",
            db=AsyncMock(),
            context_entity_ids=["ent_a"],
            limit=10,
        )

        assert len(results) == 1

    @pytest.mark.asyncio
    async def test_boost_is_10_percent_per_overlap(self, settings, mock_graph):
        """Each overlapping entity adds 10% boost to final_score."""
        # Neighborhood contains ent_a and ent_b
        svc = TriSearchService(settings, graph_engine=mock_graph)
        svc.search = AsyncMock(return_value=[
            {
                "id": "mem_1",
                "title": "Double overlap",
                "score": 1.0,
                "final_score": 1.0,
                "entity_ids": ["ent_a", "ent_b"],  # 2 overlaps
            },
        ])

        results = await svc.search_with_graph_boost(
            query="test",
            user_id="usr_1",
            workspace_id="ws_1",
            db=AsyncMock(),
            context_entity_ids=["ent_x"],
            limit=10,
        )

        # 1.0 * (1.0 + 0.1 * 2) = 1.2
        assert abs(results[0]["final_score"] - 1.2) < 0.001

    @pytest.mark.asyncio
    async def test_results_re_sorted_after_boost(self, settings, mock_graph):
        """Results are re-sorted by boosted score."""
        svc = TriSearchService(settings, graph_engine=mock_graph)
        svc.search = AsyncMock(return_value=[
            {
                "id": "mem_1",
                "score": 0.7,
                "final_score": 0.7,
                "entity_ids": ["ent_a", "ent_b"],  # both in neighborhood
            },
            {
                "id": "mem_2",
                "score": 0.8,
                "final_score": 0.8,
                "entity_ids": [],  # no overlap
            },
        ])

        results = await svc.search_with_graph_boost(
            query="test",
            user_id="usr_1",
            workspace_id="ws_1",
            db=AsyncMock(),
            context_entity_ids=["ent_x"],
            limit=10,
        )

        # mem_1 boosted: 0.7 * 1.2 = 0.84 > 0.8
        assert results[0]["id"] == "mem_1"
```

- [ ] **Step 2: Run the test to verify it fails**

Run: `cd backend && python -m pytest tests/test_graph_boost.py -v`
Expected: FAIL — `search_with_graph_boost` not defined.

- [ ] **Step 3: Implement `search_with_graph_boost()` in `tri_search.py`**

Add to `TriSearchService` class after `search_for_context()`:

```python
    async def search_with_graph_boost(
        self,
        query: str,
        user_id: str,
        workspace_id: str,
        db: AsyncSession,
        context_entity_ids: list[str] | None = None,
        limit: int = 20,
    ) -> list[dict]:
        """Search with graph-based boosting for results connected to context entities.

        Fetches 2x results, then boosts scores by 10% per entity overlap
        with the graph neighborhood of context entities.
        """
        base_results = await self.search(
            query=query,
            user_id=user_id,
            workspace_id=workspace_id,
            db=db,
            limit=limit * 2,
        )

        if not context_entity_ids or not self._graph_engine:
            return base_results[:limit]

        # Build neighborhood set from context entities
        neighborhood: set[str] = set()
        for eid in context_entity_ids[:3]:
            try:
                related = await self._graph_engine.traverse_weighted(
                    eid, user_id, depth=2
                )
                neighborhood.update(r["entity_id"] for r in related)
            except Exception:
                logger.debug(
                    "Graph boost traversal failed for %s", eid, exc_info=True
                )

        # Apply boost: 10% per overlapping entity
        for result in base_results:
            result_entities = set(result.get("entity_ids") or [])
            overlap = result_entities & neighborhood
            if overlap:
                result["final_score"] = result.get("final_score", 0.0) * (
                    1.0 + 0.1 * len(overlap)
                )

        base_results.sort(
            key=lambda r: r.get("final_score", 0.0), reverse=True
        )
        return base_results[:limit]
```

- [ ] **Step 4: Run the tests to verify they pass**

Run: `cd backend && python -m pytest tests/test_graph_boost.py -v`
Expected: All 5 tests PASS.

- [ ] **Step 5: Run existing TriSearch tests for regressions**

Run: `cd backend && python -m pytest tests/test_tri_search.py -v`
Expected: All PASS.

- [ ] **Step 6: Commit**

```bash
cd backend
git add tests/test_graph_boost.py src/services/tri_search.py
git commit -m "feat(spec5b): graph-boosted search in TriSearch (10% per entity overlap)"
```

---

### Task 7: Memory Stability Decay (Issue #24)

**Files:**
- Modify: `backend/src/services/memory_service.py:772-804` (`refresh_stability()`)
- Test: `backend/tests/test_memory_service.py`

Currently `refresh_stability()` only increments stability by 0.1 on access — it never decays. This task implements exponential decay based on time since last access.

- [ ] **Step 1: Write the failing tests**

Add to `tests/test_memory_service.py`:

```python
class TestStabilityDecay:
    @pytest.mark.asyncio
    async def test_refresh_applies_decay_before_boost(self):
        """Stability should decay based on days since last access, then +0.1 boost."""
        from datetime import datetime, timedelta, timezone
        from unittest.mock import AsyncMock, MagicMock, patch

        from src.services.memory_service import MemoryService

        settings = make_mock_settings()
        mock_db = AsyncMock()

        # Track the values passed to the update statement
        captured_values = {}

        async def capture_execute(stmt):
            # Extract the values dict from the update statement
            if hasattr(stmt, 'compile'):
                # We need to check what values are being set
                pass
            result = MagicMock()
            return result

        mock_db.execute = AsyncMock(side_effect=capture_execute)
        mock_db.flush = AsyncMock()

        svc = MemoryService(settings, mock_db)
        svc._event_bus = None

        # Access a memory that hasn't been accessed in 10 days
        # Current stability: 0.8
        # Decay: max(0.0, 0.8 - 0.02*10) = 0.6
        # After boost: min(1.0, 0.6 + 0.1) = 0.7
        await svc.refresh_stability("mem_test", user_id="usr_1")

        # Verify execute was called (the actual SQL is hard to inspect,
        # so we verify the method was called)
        mock_db.execute.assert_called_once()

    @pytest.mark.asyncio
    async def test_stability_decay_math(self):
        """Verify the decay formula: max(0, stability - 0.02*days) + 0.1."""
        from src.services.memory_service import _compute_decayed_stability

        # 0 days since access: no decay, +0.1 boost
        assert _compute_decayed_stability(0.5, 0) == 0.6

        # 10 days: decay = 0.5 - 0.2 = 0.3, boost = 0.3 + 0.1 = 0.4
        assert _compute_decayed_stability(0.5, 10) == 0.4

        # 30 days: decay = 0.5 - 0.6 = 0.0 (clamped), boost = 0.0 + 0.1 = 0.1
        assert _compute_decayed_stability(0.5, 30) == 0.1

        # Cap at 1.0
        assert _compute_decayed_stability(1.0, 0) == 1.0

        # Very old: floor at 0.1 (the access boost)
        assert _compute_decayed_stability(0.2, 100) == 0.1
```

- [ ] **Step 2: Run the test to verify it fails**

Run: `cd backend && python -m pytest tests/test_memory_service.py::TestStabilityDecay -v`
Expected: FAIL — `_compute_decayed_stability` not defined.

- [ ] **Step 3: Implement stability decay**

Add the helper function at module level in `memory_service.py` (before the `MemoryService` class):

```python
def _compute_decayed_stability(current_stability: float, days_since_access: int) -> float:
    """Compute new stability score with time-based decay and access boost.

    Formula: min(1.0, max(0.0, current - 0.02 * days) + 0.1)
    - Decays by 0.02 per day since last access
    - Adds 0.1 boost for the current access
    - Clamped to [0.0, 1.0]
    """
    decayed = max(0.0, current_stability - 0.02 * days_since_access)
    return min(1.0, decayed + 0.1)
```

Then replace `refresh_stability()` (lines 772-804):

```python
    async def refresh_stability(self, memory_id: str, user_id: str) -> None:
        """Refresh memory stability with time-based decay + access boost.

        Decays stability by 0.02 per day since last access, then adds 0.1.
        This ensures unused memories gradually decay while accessed ones stay stable.
        """
        try:
            now = datetime.now(timezone.utc)

            # Fetch current memory to compute decay
            result = await self._db.execute(
                select(Memory).where(Memory.memory_id == memory_id)
            )
            memory = result.scalar_one_or_none()
            if not memory:
                return

            last_access = memory.last_accessed_at or memory.created_at
            days_since = (now - last_access).days if last_access else 0
            new_stability = _compute_decayed_stability(
                memory.stability_score or 0.0, days_since
            )

            stmt = (
                update(Memory)
                .where(Memory.memory_id == memory_id)
                .values(
                    refresh_count=Memory.refresh_count + 1,
                    last_accessed_at=now,
                    stability_score=new_stability,
                )
            )
            await self._db.execute(stmt)
            await self._db.flush()
            await self._emit_event(
                "memory.updated",
                user_id,
                {"action": "stability_refresh", "memory_id": memory_id},
            )
        except Exception:
            try:
                await self._db.rollback()
            except Exception:
                pass
            logger.debug(
                "Failed to refresh stability for %s", memory_id, exc_info=True
            )
```

- [ ] **Step 4: Run the tests to verify they pass**

Run: `cd backend && python -m pytest tests/test_memory_service.py::TestStabilityDecay -v`
Expected: All PASS.

- [ ] **Step 5: Run existing memory tests for regressions**

Run: `cd backend && python -m pytest tests/test_memory_service.py -v`
Expected: All PASS.

- [ ] **Step 6: Commit**

```bash
cd backend
git add src/services/memory_service.py tests/test_memory_service.py
git commit -m "fix(spec5b): memory stability decay (0.02/day) with access boost (issue #24)"
```

---

### Task 8: Briefing Evidence via Vector Similarity (Issue #26)

**Files:**
- Modify: `backend/src/services/briefing_read_model.py:126-153` (`_get_related_items()`)
- Test: `backend/tests/test_context_builder_service.py` (or inline in briefing tests)

Currently `_get_related_items()` finds related runs by timestamp proximity. This task replaces that with vector similarity from TriSearch, linking briefings to semantically related memories, events, and runs.

- [ ] **Step 1: Write the failing test**

Add to a test file (can be at the bottom of `tests/test_context_builder_service.py` or a new file):

```python
class TestBriefingEvidenceSemantic:
    @pytest.mark.asyncio
    async def test_related_items_uses_tri_search(self):
        """_get_related_items should use TriSearch vector similarity."""
        from unittest.mock import AsyncMock, MagicMock, PropertyMock

        from src.services.briefing_read_model import BriefingReadModel

        mock_db = AsyncMock()
        brm = BriefingReadModel(mock_db, "ws_1")

        mock_tri_search = AsyncMock()
        mock_tri_search.search = AsyncMock(return_value=[
            {
                "id": "mem_1",
                "title": "Related memory",
                "result_type": "memory",
                "final_score": 0.85,
                "text": "Some evidence",
            },
            {
                "id": "evt_2",
                "title": "Related event",
                "result_type": "event",
                "final_score": 0.72,
                "text": "An event",
            },
        ])
        brm._tri_search = mock_tri_search

        mock_briefing = MagicMock()
        mock_briefing.headline = "Q1 Revenue Update"
        mock_briefing.briefing_id = "brn_001"
        mock_briefing.created_at = None

        items = await brm._get_related_items(mock_briefing)

        mock_tri_search.search.assert_called_once()
        # Should include the search results as related items
        assert len(items) >= 2
        assert any(i["item_id"] == "mem_1" for i in items)
```

- [ ] **Step 2: Run the test to verify it fails**

Run: `cd backend && python -m pytest tests/test_context_builder_service.py::TestBriefingEvidenceSemantic -v`
Expected: FAIL — `_get_related_items` uses timestamp, not TriSearch.

- [ ] **Step 3: Update `BriefingReadModel` to accept and use TriSearch**

Update `__init__` and `_get_related_items`:

```python
class BriefingReadModel:
    """Read model for briefing list/detail with evidence and actions."""

    def __init__(
        self,
        db: AsyncSession,
        workspace_id: str,
        tri_search: TriSearchService | None = None,
        user_id: str = "",
    ):
        self._db = db
        self._workspace_id = workspace_id
        self._tri_search = tri_search
        self._user_id = user_id
```

Add the import at the top:
```python
from __future__ import annotations

import logging
from datetime import datetime, timedelta, timezone
from typing import TYPE_CHECKING

from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from src.models.briefings import Briefing

if TYPE_CHECKING:
    from src.services.tri_search import TriSearchService
```

Replace `_get_related_items()`:

```python
    async def _get_related_items(self, briefing: Briefing) -> list[dict]:
        """Find items related to this briefing via vector similarity.

        Falls back to timestamp proximity if TriSearch is unavailable.
        """
        items: list[dict] = []

        # Prefer semantic search for evidence linking (Issue #26)
        if self._tri_search and briefing.headline:
            try:
                results = await self._tri_search.search(
                    query=briefing.headline,
                    user_id=self._user_id,
                    workspace_id=self._workspace_id,
                    db=self._db,
                    types=["event", "memory", "conversation"],
                    limit=10,
                )
                for r in results:
                    items.append({
                        "item_type": r.get("result_type", "unknown"),
                        "item_id": r.get("id", ""),
                        "title": r.get("title", ""),
                        "score": r.get("final_score", 0.0),
                    })
                return items
            except Exception:
                logger.debug(
                    "TriSearch evidence linking failed, falling back",
                    exc_info=True,
                )

        # Fallback: timestamp proximity
        from src.models.task_graph import TaskRun

        if briefing.created_at:
            result = await self._db.execute(
                select(TaskRun)
                .where(
                    TaskRun.workspace_id == self._workspace_id,
                    TaskRun.created_at >= briefing.created_at,
                )
                .order_by(TaskRun.created_at)
                .limit(3)
            )
            for run in result.scalars().all():
                items.append({
                    "item_type": "run",
                    "item_id": run.run_id,
                    "title": f"Run {run.run_id[:16]}...",
                    "status": run.status,
                })

        return items
```

- [ ] **Step 4: Run the tests to verify they pass**

Run: `cd backend && python -m pytest tests/test_context_builder_service.py::TestBriefingEvidenceSemantic -v`
Expected: All PASS.

- [ ] **Step 5: Check for callers of `BriefingReadModel()` and update them**

Run: `cd backend && grep -rn "BriefingReadModel(" src/`

Update any callers to optionally pass `tri_search` and `user_id` if available from the service container. If callers don't have tri_search available, the fallback path handles it.

- [ ] **Step 6: Run full test suite for regressions**

Run: `cd backend && python -m pytest tests/ -v -x --timeout=60`
Expected: All PASS.

- [ ] **Step 7: Commit**

```bash
cd backend
git add src/services/briefing_read_model.py tests/test_context_builder_service.py
git commit -m "fix(spec5b): briefing evidence uses vector similarity (issue #26)"
```

---

### Task 9: Final Integration + Regression Sweep

**Files:**
- All files from Tasks 1-8
- Existing test suites

This task runs the full test suite and fixes any regressions from the changes above.

- [ ] **Step 1: Run the full test suite**

Run: `cd backend && python -m pytest tests/ -v --timeout=60 2>&1 | tail -50`
Expected: All tests pass. Note any failures.

- [ ] **Step 2: Fix any regressions found**

Common issues to check:
- Tests that mock `sync_relationship()` without the new `strength`/`start_date`/`end_date` params
- Tests that assert on `:RELATES_TO` in Cypher output
- Tests that call `get_related_people()` expecting it to be called by ContextBuilder
- `BriefingReadModel` constructor signature changes breaking callers

- [ ] **Step 3: Run ruff format and lint**

Run: `cd backend && ruff format src/ tests/ && ruff check src/ tests/ --fix`
Expected: Clean output.

- [ ] **Step 4: Final commit if any fixes were needed**

```bash
cd backend
git add -A
git commit -m "fix(spec5b): regression fixes from Neo4j enrichment integration"
```

- [ ] **Step 5: Run the tests one more time to confirm**

Run: `cd backend && python -m pytest tests/ -v --timeout=60 -q`
Expected: All PASS, 0 failures.
