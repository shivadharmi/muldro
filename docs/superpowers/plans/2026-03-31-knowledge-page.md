# Knowledge Page Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Build a `/knowledge` page with 3 tabs (Graph, Memories, Stats) to let the user explore their entity relationships, memories, and knowledge growth. Remove the Preferences tab from Settings.

**Architecture:** Backend adds 4 new API endpoints in `routes_knowledge.py` backed by a thin `KnowledgeService`. Frontend adds a new page at `/knowledge` with a Zustand store, `react-force-graph-2d` for the interactive graph, and component-per-tab architecture. The unified search bar uses the existing `POST /v1/search` TriSearch endpoint.

**Tech Stack:** Python/FastAPI (backend), React/Next.js/TanStack Query/Zustand/Tailwind (frontend), `react-force-graph-2d` (graph rendering), Neo4j (graph data), Qdrant+Postgres (memories)

**Spec:** `docs/superpowers/specs/2026-03-31-knowledge-page-design.md`

---

## File Structure

### Backend (create)
- `backend/src/services/knowledge_service.py` — orchestrates GraphEngine + DB queries for 4 endpoints
- `backend/src/api/routes_knowledge.py` — 4 REST endpoints under `/v1/knowledge/`
- `backend/tests/test_knowledge_service.py` — unit tests for KnowledgeService
- `backend/tests/test_routes_knowledge.py` — API route tests

### Backend (modify)
- `backend/src/api/app.py` — register knowledge router

### Frontend (create)
- `frontend/src/app/knowledge/page.tsx` — page shell with tab routing
- `frontend/src/stores/knowledge-store.ts` — Zustand store
- `frontend/src/components/knowledge/graph-view.tsx` — react-force-graph-2d wrapper
- `frontend/src/components/knowledge/graph-detail-panel.tsx` — entity detail panel
- `frontend/src/components/knowledge/graph-filters.tsx` — entity type filter chips
- `frontend/src/components/knowledge/graph-context-menu.tsx` — right-click menu
- `frontend/src/components/knowledge/knowledge-search.tsx` — unified search with dropdown
- `frontend/src/components/knowledge/memories-view.tsx` — memory list with filters
- `frontend/src/components/knowledge/memory-row.tsx` — single memory row
- `frontend/src/components/knowledge/memory-detail-panel.tsx` — memory detail panel
- `frontend/src/components/knowledge/stats-view.tsx` — stats dashboard
- `frontend/src/components/knowledge/stat-card.tsx` — metric card
- `frontend/src/components/knowledge/bar-chart.tsx` — CSS bar chart
- `frontend/src/components/knowledge/donut-chart.tsx` — SVG donut chart
- `frontend/src/components/knowledge/community-card.tsx` — community cluster card

### Frontend (modify)
- `frontend/src/lib/api.ts` — add 4 knowledge API methods
- `frontend/src/components/layout/sidebar.tsx` — add Knowledge nav item
- `frontend/src/app/settings/page.tsx` — remove Preferences tab

### Frontend (delete)
- `frontend/src/components/settings/preferences-panel.tsx`

---

### Task 1: Backend — KnowledgeService

**Files:**
- Create: `backend/src/services/knowledge_service.py`
- Test: `backend/tests/test_knowledge_service.py`

- [ ] **Step 1: Write the failing test for get_initial_graph**

```python
# backend/tests/test_knowledge_service.py
"""Tests for KnowledgeService."""

import pytest
from unittest.mock import AsyncMock, MagicMock, patch

from src.services.knowledge_service import KnowledgeService


@pytest.fixture
def mock_settings():
    s = MagicMock()
    s.neo4j_url = "bolt://localhost:7687"
    s.neo4j_user = "neo4j"
    s.neo4j_pass = "xxx"  # noqa: S105
    return s


@pytest.fixture
def mock_db():
    db = AsyncMock()
    return db


@pytest.fixture
def service(mock_settings, mock_db):
    return KnowledgeService(settings=mock_settings, db=mock_db)


@pytest.mark.asyncio
async def test_get_initial_graph_returns_nodes_and_edges(service):
    with patch.object(service, "_graph_engine") as mock_graph:
        mock_graph.find_central_entities = AsyncMock(
            return_value=[
                {"entity_id": "ent_1", "name": "Alice", "entity_type": "person", "degree": 5},
                {"entity_id": "ent_2", "name": "Acme", "entity_type": "organization", "degree": 3},
            ]
        )
        mock_graph.get_subgraph = AsyncMock(
            return_value={
                "nodes": [
                    {"entity_id": "ent_1", "name": "Alice", "type": "person"},
                    {"entity_id": "ent_2", "name": "Acme", "type": "organization"},
                ],
                "edges": [
                    {"from": "ent_1", "to": "ent_2", "type": "works_at"},
                ],
            }
        )

        result = await service.get_initial_graph(
            user_id="usr_test", workspace_id="ws_test"
        )

        assert "nodes" in result
        assert "edges" in result
        assert "stats" in result
        assert len(result["nodes"]) == 2
        assert len(result["edges"]) == 1
```

- [ ] **Step 2: Run test to verify it fails**

Run: `cd backend && python -m pytest tests/test_knowledge_service.py::test_get_initial_graph_returns_nodes_and_edges -v`
Expected: FAIL with `ModuleNotFoundError: No module named 'src.services.knowledge_service'`

- [ ] **Step 3: Implement KnowledgeService**

```python
# backend/src/services/knowledge_service.py
"""KnowledgeService — thin orchestration for the Knowledge page endpoints.

Combines GraphEngine (Neo4j), MemoryService (Qdrant/Postgres), and
direct Postgres queries to serve the 4 /v1/knowledge/ endpoints.
"""

import logging
from datetime import datetime, timedelta, timezone

from sqlalchemy import func, select
from sqlalchemy.ext.asyncio import AsyncSession

from src.config.settings import Settings
from src.models.entities import Entity, EntityAlias, EntityRelationship
from src.models.memory import Memory
from src.services.graph_engine import GraphEngine

logger = logging.getLogger(__name__)


class KnowledgeService:
    """Orchestrates graph, memory, and stats queries for the Knowledge page."""

    def __init__(self, settings: Settings, db: AsyncSession) -> None:
        self._settings = settings
        self._db = db
        self._graph_engine = GraphEngine(settings)

    async def close(self) -> None:
        await self._graph_engine.close()

    # ── Graph Tab ─────────────────────────────────────────────────

    async def get_initial_graph(
        self,
        user_id: str,
        workspace_id: str,
    ) -> dict:
        """Return seed graph: top-10 central entities + edges between them."""
        central = await self._graph_engine.find_central_entities(
            user_id, limit=10
        )
        if not central:
            total_e, total_r = await self._count_entities_relationships(
                user_id, workspace_id
            )
            return {
                "nodes": [],
                "edges": [],
                "stats": {
                    "total_entities": total_e,
                    "total_relationships": total_r,
                },
            }

        entity_ids = [e["entity_id"] for e in central]
        subgraph = await self._graph_engine.get_subgraph(entity_ids, user_id)

        # Enrich nodes with Postgres attributes
        enriched_nodes = await self._enrich_nodes(
            subgraph.get("nodes", []), workspace_id
        )

        total_e, total_r = await self._count_entities_relationships(
            user_id, workspace_id
        )

        return {
            "nodes": enriched_nodes,
            "edges": subgraph.get("edges", []),
            "stats": {
                "total_entities": total_e,
                "total_relationships": total_r,
            },
        }

    async def _enrich_nodes(
        self, nodes: list[dict], workspace_id: str
    ) -> list[dict]:
        """Batch-fetch Postgres entity details for graph nodes."""
        if not nodes:
            return []

        ids = [n.get("entity_id") or n.get("id", "") for n in nodes]
        stmt = (
            select(Entity)
            .where(Entity.entity_id.in_(ids), Entity.workspace_id == workspace_id)
        )
        result = await self._db.execute(stmt)
        entities_by_id = {e.entity_id: e for e in result.scalars().all()}

        # Fetch aliases for all entities
        alias_stmt = select(EntityAlias).where(EntityAlias.entity_id.in_(ids))
        alias_result = await self._db.execute(alias_stmt)
        aliases_by_entity: dict[str, list[str]] = {}
        for alias in alias_result.scalars().all():
            aliases_by_entity.setdefault(alias.entity_id, []).append(alias.alias)

        enriched = []
        for node in nodes:
            eid = node.get("entity_id") or node.get("id", "")
            entity = entities_by_id.get(eid)
            enriched.append({
                "entity_id": eid,
                "canonical_name": entity.canonical_name if entity else node.get("name", ""),
                "entity_type": entity.entity_type if entity else node.get("type", "unknown"),
                "importance_score": entity.importance_score if entity else 0.0,
                "interaction_count": entity.interaction_count if entity else 0,
                "last_seen_at": (
                    entity.last_seen_at.isoformat() if entity and entity.last_seen_at else None
                ),
                "attributes": entity.attributes if entity else {},
                "aliases": aliases_by_entity.get(eid, []),
            })
        return enriched

    async def _count_entities_relationships(
        self, user_id: str, workspace_id: str
    ) -> tuple[int, int]:
        e_result = await self._db.execute(
            select(func.count()).select_from(Entity).where(
                Entity.user_id == user_id,
                Entity.workspace_id == workspace_id,
            )
        )
        r_result = await self._db.execute(
            select(func.count()).select_from(EntityRelationship).where(
                EntityRelationship.user_id == user_id,
                EntityRelationship.workspace_id == workspace_id,
            )
        )
        return e_result.scalar() or 0, r_result.scalar() or 0

    # ── Memories Tab ──────────────────────────────────────────────

    async def get_memories_paginated(
        self,
        user_id: str,
        workspace_id: str,
        *,
        memory_type: str | None = None,
        sort_by: str = "recent",
        search: str | None = None,
        entity_id: str | None = None,
        page: int = 1,
        limit: int = 50,
    ) -> dict:
        """Return paginated memory list with filters."""
        base = select(Memory).where(
            Memory.user_id == user_id,
            Memory.workspace_id == workspace_id,
            Memory.status == "active",
        )
        count_base = select(func.count()).select_from(Memory).where(
            Memory.user_id == user_id,
            Memory.workspace_id == workspace_id,
            Memory.status == "active",
        )

        if memory_type:
            base = base.where(Memory.memory_type == memory_type)
            count_base = count_base.where(Memory.memory_type == memory_type)

        if search:
            base = base.where(Memory.fact_text.ilike(f"%{search}%"))
            count_base = count_base.where(Memory.fact_text.ilike(f"%{search}%"))

        if entity_id:
            base = base.where(Memory.entity_ids.any(entity_id))
            count_base = count_base.where(Memory.entity_ids.any(entity_id))

        # Sort
        if sort_by == "confidence":
            base = base.order_by(Memory.confidence.desc())
        elif sort_by == "stability":
            base = base.order_by(Memory.stability_score.desc())
        else:
            base = base.order_by(Memory.created_at.desc())

        # Count total
        total_result = await self._db.execute(count_base)
        total = total_result.scalar() or 0

        # Paginate
        offset = (page - 1) * limit
        base = base.offset(offset).limit(limit)

        result = await self._db.execute(base)
        rows = result.scalars().all()

        # Resolve entity names for each memory
        all_entity_ids: set[str] = set()
        for m in rows:
            if m.entity_ids:
                all_entity_ids.update(m.entity_ids)

        entity_names: dict[str, str] = {}
        if all_entity_ids:
            name_stmt = select(Entity.entity_id, Entity.canonical_name).where(
                Entity.entity_id.in_(list(all_entity_ids))
            )
            name_result = await self._db.execute(name_stmt)
            entity_names = dict(name_result.all())

        items = []
        for m in rows:
            items.append({
                "memory_id": m.memory_id,
                "memory_type": m.memory_type,
                "fact_text": m.fact_text,
                "confidence": m.confidence,
                "stability_score": m.stability_score,
                "refresh_count": m.refresh_count,
                "scope": m.scope,
                "created_at": m.created_at.isoformat() if m.created_at else None,
                "last_accessed_at": (
                    m.last_accessed_at.isoformat() if m.last_accessed_at else None
                ),
                "expires_at": None,  # TTL-based, not stored as a column
                "entity_ids": m.entity_ids or [],
                "entity_names": [
                    entity_names.get(eid, eid) for eid in (m.entity_ids or [])
                ],
            })

        pages = max(1, (total + limit - 1) // limit)
        return {"items": items, "total": total, "page": page, "pages": pages}

    # ── Memory Detail ────────────────────────────────────────────

    async def get_memory_detail(
        self,
        memory_id: str,
        user_id: str,
        workspace_id: str,
    ) -> dict | None:
        """Return full memory detail with linked entities and provenance."""
        stmt = select(Memory).where(
            Memory.memory_id == memory_id,
            Memory.user_id == user_id,
            Memory.workspace_id == workspace_id,
        )
        result = await self._db.execute(stmt)
        m = result.scalar_one_or_none()
        if not m:
            return None

        # Resolve linked entities
        linked_entities = []
        if m.entity_ids:
            ent_stmt = select(
                Entity.entity_id, Entity.canonical_name, Entity.entity_type
            ).where(Entity.entity_id.in_(m.entity_ids))
            ent_result = await self._db.execute(ent_stmt)
            linked_entities = [
                {
                    "entity_id": row.entity_id,
                    "canonical_name": row.canonical_name,
                    "entity_type": row.entity_type,
                }
                for row in ent_result.all()
            ]

        # Build provenance from source_event_ids
        provenance = {"source_event_ids": [], "source_description": None}
        if m.source_event_ids:
            event_ids = (
                m.source_event_ids
                if isinstance(m.source_event_ids, list)
                else list(m.source_event_ids.values())
                if isinstance(m.source_event_ids, dict)
                else []
            )
            provenance["source_event_ids"] = event_ids
            if event_ids:
                from src.models.events import NormalizedEvent

                ev_stmt = select(
                    NormalizedEvent.title, NormalizedEvent.source
                ).where(NormalizedEvent.event_id.in_(event_ids)).limit(3)
                ev_result = await self._db.execute(ev_stmt)
                ev_rows = ev_result.all()
                if ev_rows:
                    parts = [
                        f"{r.source}: {r.title}" for r in ev_rows if r.title
                    ]
                    provenance["source_description"] = (
                        "Extracted from " + "; ".join(parts) if parts else None
                    )

        return {
            "memory_id": m.memory_id,
            "memory_type": m.memory_type,
            "fact_text": m.fact_text,
            "confidence": m.confidence,
            "stability_score": m.stability_score,
            "refresh_count": m.refresh_count,
            "scope": m.scope,
            "created_at": m.created_at.isoformat() if m.created_at else None,
            "last_accessed_at": (
                m.last_accessed_at.isoformat() if m.last_accessed_at else None
            ),
            "expires_at": None,
            "linked_entities": linked_entities,
            "provenance": provenance,
        }

    # ── Stats Tab ─────────────────────────────────────────────────

    async def get_stats(
        self,
        user_id: str,
        workspace_id: str,
    ) -> dict:
        """Return aggregated stats for the Stats dashboard tab."""
        now = datetime.now(timezone.utc)
        week_ago = now - timedelta(days=7)

        # Total counts
        total_entities, total_relationships = await self._count_entities_relationships(
            user_id, workspace_id
        )

        mem_count = await self._db.execute(
            select(func.count()).select_from(Memory).where(
                Memory.user_id == user_id,
                Memory.workspace_id == workspace_id,
                Memory.status == "active",
            )
        )
        total_memories = mem_count.scalar() or 0

        avg_conf = await self._db.execute(
            select(func.avg(Memory.confidence)).where(
                Memory.user_id == user_id,
                Memory.workspace_id == workspace_id,
                Memory.status == "active",
            )
        )
        avg_confidence = round(avg_conf.scalar() or 0.0, 2)

        # Weekly deltas
        new_entities = await self._db.execute(
            select(func.count()).select_from(Entity).where(
                Entity.user_id == user_id,
                Entity.workspace_id == workspace_id,
                Entity.created_at >= week_ago,
            )
        )
        new_rels = await self._db.execute(
            select(func.count()).select_from(EntityRelationship).where(
                EntityRelationship.user_id == user_id,
                EntityRelationship.workspace_id == workspace_id,
                EntityRelationship.created_at >= week_ago,
            )
        )
        new_mems = await self._db.execute(
            select(func.count()).select_from(Memory).where(
                Memory.user_id == user_id,
                Memory.workspace_id == workspace_id,
                Memory.status == "active",
                Memory.created_at >= week_ago,
            )
        )

        # Entity counts by type
        etype_stmt = (
            select(Entity.entity_type, func.count().label("count"))
            .where(
                Entity.user_id == user_id,
                Entity.workspace_id == workspace_id,
            )
            .group_by(Entity.entity_type)
            .order_by(func.count().desc())
        )
        etype_result = await self._db.execute(etype_stmt)
        entity_counts_by_type = [
            {"entity_type": r.entity_type, "count": r.count}
            for r in etype_result.all()
        ]

        # Memory counts by type
        mtype_stmt = (
            select(Memory.memory_type, func.count().label("count"))
            .where(
                Memory.user_id == user_id,
                Memory.workspace_id == workspace_id,
                Memory.status == "active",
            )
            .group_by(Memory.memory_type)
            .order_by(func.count().desc())
        )
        mtype_result = await self._db.execute(mtype_stmt)
        memory_counts_by_type = [
            {"memory_type": r.memory_type, "count": r.count}
            for r in mtype_result.all()
        ]

        # Central entities (from Neo4j)
        central_entities = await self._graph_engine.find_central_entities(
            user_id, limit=5
        )

        # Communities (from Neo4j)
        communities = await self._graph_engine.detect_communities(user_id)

        # Stale relationships (from Neo4j)
        stale_relationships = await self._graph_engine.get_stale_relationships(
            user_id, days=14
        )

        # Growth by day (last 7 days)
        growth_by_day = []
        for i in range(6, -1, -1):
            day_start = (now - timedelta(days=i)).replace(
                hour=0, minute=0, second=0, microsecond=0
            )
            day_end = day_start + timedelta(days=1)

            day_ents = await self._db.execute(
                select(func.count()).select_from(Entity).where(
                    Entity.user_id == user_id,
                    Entity.workspace_id == workspace_id,
                    Entity.created_at >= day_start,
                    Entity.created_at < day_end,
                )
            )
            day_mems = await self._db.execute(
                select(func.count()).select_from(Memory).where(
                    Memory.user_id == user_id,
                    Memory.workspace_id == workspace_id,
                    Memory.status == "active",
                    Memory.created_at >= day_start,
                    Memory.created_at < day_end,
                )
            )
            growth_by_day.append({
                "date": day_start.strftime("%Y-%m-%d"),
                "entities": day_ents.scalar() or 0,
                "memories": day_mems.scalar() or 0,
            })

        return {
            "total_entities": total_entities,
            "total_relationships": total_relationships,
            "total_memories": total_memories,
            "avg_confidence": avg_confidence,
            "weekly_delta": {
                "entities": new_entities.scalar() or 0,
                "relationships": new_rels.scalar() or 0,
                "memories": new_mems.scalar() or 0,
            },
            "entity_counts_by_type": entity_counts_by_type,
            "memory_counts_by_type": memory_counts_by_type,
            "central_entities": central_entities,
            "communities": communities[:4],  # Top 4
            "stale_relationships": stale_relationships,
            "growth_by_day": growth_by_day,
        }
```

- [ ] **Step 4: Run test to verify it passes**

Run: `cd backend && python -m pytest tests/test_knowledge_service.py::test_get_initial_graph_returns_nodes_and_edges -v`
Expected: PASS

- [ ] **Step 5: Write tests for memories and stats**

Append to `backend/tests/test_knowledge_service.py`:

```python
@pytest.mark.asyncio
async def test_get_memories_paginated_returns_items(service, mock_db):
    """Test paginated memory list returns correct structure."""
    from unittest.mock import PropertyMock
    from src.models.memory import Memory as MemoryModel

    mock_memory = MagicMock()
    mock_memory.memory_id = "mem_1"
    mock_memory.memory_type = "semantic"
    mock_memory.fact_text = "Alice is the CEO"
    mock_memory.confidence = 0.9
    mock_memory.stability_score = 0.8
    mock_memory.refresh_count = 3
    mock_memory.scope = "general"
    mock_memory.created_at = datetime(2026, 3, 28, tzinfo=timezone.utc)
    mock_memory.last_accessed_at = None
    mock_memory.entity_ids = ["ent_1"]

    # Mock the execute calls: count then rows then entity names
    mock_count_result = MagicMock()
    mock_count_result.scalar.return_value = 1

    mock_rows_result = MagicMock()
    mock_rows_result.scalars.return_value.all.return_value = [mock_memory]

    mock_names_result = MagicMock()
    mock_names_result.all.return_value = [("ent_1", "Alice")]

    mock_db.execute = AsyncMock(
        side_effect=[mock_count_result, mock_rows_result, mock_names_result]
    )

    result = await service.get_memories_paginated(
        user_id="usr_test", workspace_id="ws_test"
    )

    assert result["total"] == 1
    assert len(result["items"]) == 1
    assert result["items"][0]["memory_id"] == "mem_1"
    assert result["items"][0]["entity_names"] == ["Alice"]


@pytest.mark.asyncio
async def test_get_memory_detail_not_found(service, mock_db):
    """Test memory detail returns None for missing memory."""
    mock_result = MagicMock()
    mock_result.scalar_one_or_none.return_value = None
    mock_db.execute = AsyncMock(return_value=mock_result)

    result = await service.get_memory_detail(
        memory_id="mem_missing",
        user_id="usr_test",
        workspace_id="ws_test",
    )
    assert result is None


@pytest.mark.asyncio
async def test_get_stats_returns_structure(service, mock_db):
    """Test stats returns all required keys."""
    # Mock all the DB count queries to return 0
    mock_scalar = MagicMock()
    mock_scalar.scalar.return_value = 0

    mock_db.execute = AsyncMock(return_value=mock_scalar)

    with patch.object(service, "_graph_engine") as mock_graph:
        mock_graph.find_central_entities = AsyncMock(return_value=[])
        mock_graph.detect_communities = AsyncMock(return_value=[])
        mock_graph.get_stale_relationships = AsyncMock(return_value=[])

        result = await service.get_stats(
            user_id="usr_test", workspace_id="ws_test"
        )

    assert "total_entities" in result
    assert "total_relationships" in result
    assert "total_memories" in result
    assert "avg_confidence" in result
    assert "weekly_delta" in result
    assert "entity_counts_by_type" in result
    assert "memory_counts_by_type" in result
    assert "central_entities" in result
    assert "communities" in result
    assert "stale_relationships" in result
    assert "growth_by_day" in result
    assert len(result["growth_by_day"]) == 7
```

- [ ] **Step 6: Run all service tests**

Run: `cd backend && python -m pytest tests/test_knowledge_service.py -v`
Expected: ALL PASS

- [ ] **Step 7: Commit**

```bash
cd backend && git add src/services/knowledge_service.py tests/test_knowledge_service.py
git commit -m "feat: add KnowledgeService for graph, memories, and stats queries"
```

---

### Task 2: Backend — API Routes

**Files:**
- Create: `backend/src/api/routes_knowledge.py`
- Modify: `backend/src/api/app.py`
- Test: `backend/tests/test_routes_knowledge.py`

- [ ] **Step 1: Write the failing route test**

```python
# backend/tests/test_routes_knowledge.py
"""Tests for Knowledge page API routes."""

import pytest
from unittest.mock import AsyncMock, MagicMock, patch


@pytest.mark.asyncio
async def test_knowledge_graph_endpoint_exists():
    """Verify the knowledge graph route is registered."""
    from src.api.routes_knowledge import router

    routes = [r.path for r in router.routes]
    assert "/v1/knowledge/graph" in routes
    assert "/v1/knowledge/memories" in routes
    assert "/v1/knowledge/memories/{memory_id}" in routes
    assert "/v1/knowledge/stats" in routes
```

- [ ] **Step 2: Run test to verify it fails**

Run: `cd backend && python -m pytest tests/test_routes_knowledge.py::test_knowledge_graph_endpoint_exists -v`
Expected: FAIL with `ModuleNotFoundError`

- [ ] **Step 3: Implement routes_knowledge.py**

```python
# backend/src/api/routes_knowledge.py
"""Knowledge page endpoints — graph, memories, stats."""

import logging

from fastapi import APIRouter, Depends, HTTPException, Query
from pydantic import BaseModel
from sqlalchemy.ext.asyncio import AsyncSession

from src.api.deps import (
    get_current_user_id,
    get_current_workspace_id,
    get_session,
)
from src.config.settings import Settings, get_settings

router = APIRouter()
logger = logging.getLogger(__name__)


# ── Response Models ──────────────────────────────────────────────


class GraphNodeResponse(BaseModel):
    entity_id: str
    canonical_name: str
    entity_type: str
    importance_score: float = 0.0
    interaction_count: int = 0
    last_seen_at: str | None = None
    attributes: dict | None = None
    aliases: list[str] = []


class GraphEdgeResponse(BaseModel):
    from_entity_id: str | None = None
    to_entity_id: str | None = None
    relation_type: str | None = None
    relation_id: str | None = None
    # Also accept the Neo4j shorthand keys
    model_config = {"extra": "allow"}


class GraphStatsResponse(BaseModel):
    total_entities: int = 0
    total_relationships: int = 0


class GraphResponse(BaseModel):
    nodes: list[GraphNodeResponse] = []
    edges: list[dict] = []
    stats: GraphStatsResponse = GraphStatsResponse()


class MemoryItemResponse(BaseModel):
    memory_id: str
    memory_type: str
    fact_text: str
    confidence: float
    stability_score: float
    refresh_count: int = 0
    scope: str | None = None
    created_at: str | None = None
    last_accessed_at: str | None = None
    expires_at: str | None = None
    entity_ids: list[str] = []
    entity_names: list[str] = []


class MemoryListResponse(BaseModel):
    items: list[MemoryItemResponse] = []
    total: int = 0
    page: int = 1
    pages: int = 1


class LinkedEntityResponse(BaseModel):
    entity_id: str
    canonical_name: str
    entity_type: str


class ProvenanceResponse(BaseModel):
    source_event_ids: list[str] = []
    source_description: str | None = None


class MemoryDetailResponse(BaseModel):
    memory_id: str
    memory_type: str
    fact_text: str
    confidence: float
    stability_score: float
    refresh_count: int = 0
    scope: str | None = None
    created_at: str | None = None
    last_accessed_at: str | None = None
    expires_at: str | None = None
    linked_entities: list[LinkedEntityResponse] = []
    provenance: ProvenanceResponse = ProvenanceResponse()


class StatsResponse(BaseModel):
    total_entities: int = 0
    total_relationships: int = 0
    total_memories: int = 0
    avg_confidence: float = 0.0
    weekly_delta: dict = {}
    entity_counts_by_type: list[dict] = []
    memory_counts_by_type: list[dict] = []
    central_entities: list[dict] = []
    communities: list[dict] = []
    stale_relationships: list[dict] = []
    growth_by_day: list[dict] = []


# ── Endpoints ────────────────────────────────────────────────────


@router.get("/v1/knowledge/graph", response_model=GraphResponse)
async def knowledge_graph(
    user_id: str = Depends(get_current_user_id),
    workspace_id: str = Depends(get_current_workspace_id),
    db: AsyncSession = Depends(get_session),
    settings: Settings = Depends(get_settings),
):
    """Initial graph payload: top central entities + edges between them."""
    from src.services.knowledge_service import KnowledgeService

    svc = KnowledgeService(settings=settings, db=db)
    try:
        return await svc.get_initial_graph(user_id, workspace_id)
    finally:
        await svc.close()


@router.get("/v1/knowledge/memories", response_model=MemoryListResponse)
async def knowledge_memories(
    user_id: str = Depends(get_current_user_id),
    workspace_id: str = Depends(get_current_workspace_id),
    db: AsyncSession = Depends(get_session),
    settings: Settings = Depends(get_settings),
    type: str | None = Query(None),
    sort_by: str = Query("recent"),
    search: str | None = Query(None),
    entity_id: str | None = Query(None),
    page: int = Query(1, ge=1),
    limit: int = Query(50, ge=1, le=100),
):
    """Paginated, filterable memory list."""
    from src.services.knowledge_service import KnowledgeService

    svc = KnowledgeService(settings=settings, db=db)
    try:
        return await svc.get_memories_paginated(
            user_id,
            workspace_id,
            memory_type=type,
            sort_by=sort_by,
            search=search,
            entity_id=entity_id,
            page=page,
            limit=limit,
        )
    finally:
        await svc.close()


@router.get("/v1/knowledge/memories/{memory_id}", response_model=MemoryDetailResponse)
async def knowledge_memory_detail(
    memory_id: str,
    user_id: str = Depends(get_current_user_id),
    workspace_id: str = Depends(get_current_workspace_id),
    db: AsyncSession = Depends(get_session),
    settings: Settings = Depends(get_settings),
):
    """Full memory detail with linked entities and provenance."""
    from src.services.knowledge_service import KnowledgeService

    svc = KnowledgeService(settings=settings, db=db)
    try:
        result = await svc.get_memory_detail(memory_id, user_id, workspace_id)
        if not result:
            raise HTTPException(status_code=404, detail=f"Memory {memory_id} not found")
        return result
    finally:
        await svc.close()


@router.get("/v1/knowledge/stats", response_model=StatsResponse)
async def knowledge_stats(
    user_id: str = Depends(get_current_user_id),
    workspace_id: str = Depends(get_current_workspace_id),
    db: AsyncSession = Depends(get_session),
    settings: Settings = Depends(get_settings),
):
    """Aggregated dashboard data for Stats tab."""
    from src.services.knowledge_service import KnowledgeService

    svc = KnowledgeService(settings=settings, db=db)
    try:
        return await svc.get_stats(user_id, workspace_id)
    finally:
        await svc.close()
```

- [ ] **Step 4: Register the router in app.py**

Add to `backend/src/api/app.py`:

At the top imports (line ~36, after other router imports):
```python
from src.api.routes_knowledge import router as knowledge_router
```

In `create_app()`, after the graph_router line (after line 281):
```python
    # Knowledge page (graph + memories + stats)
    app.include_router(knowledge_router, tags=["knowledge"])
```

- [ ] **Step 5: Run route test to verify it passes**

Run: `cd backend && python -m pytest tests/test_routes_knowledge.py -v`
Expected: PASS

- [ ] **Step 6: Run existing tests to verify no regressions**

Run: `cd backend && python -m pytest tests/ -v --timeout=60 -x -q 2>&1 | tail -20`
Expected: All existing tests still pass

- [ ] **Step 7: Commit**

```bash
cd backend && git add src/api/routes_knowledge.py src/api/app.py tests/test_routes_knowledge.py
git commit -m "feat: add /v1/knowledge/ API routes for graph, memories, stats"
```

---

### Task 3: Frontend — Install react-force-graph-2d and add API methods

**Files:**
- Modify: `frontend/package.json` (via npm install)
- Modify: `frontend/src/lib/api.ts`

- [ ] **Step 1: Install react-force-graph-2d**

Run: `cd /Users/sivasankarreddybogala/work/jarvis/frontend && npm install react-force-graph-2d`

- [ ] **Step 2: Add knowledge API methods to api.ts**

Append to `frontend/src/lib/api.ts` (before the last closing comment or at end of file):

```typescript
// ── Knowledge Page ──────────────────────────────────────────────

export interface KnowledgeGraphNode {
  entity_id: string;
  canonical_name: string;
  entity_type: string;
  importance_score: number;
  interaction_count: number;
  last_seen_at: string | null;
  attributes: Record<string, unknown> | null;
  aliases: string[];
}

export interface KnowledgeGraphEdge {
  from_entity_id?: string;
  to_entity_id?: string;
  from?: string;
  to?: string;
  relation_type?: string;
  type?: string;
  relation_id?: string;
}

export interface KnowledgeGraphResponse {
  nodes: KnowledgeGraphNode[];
  edges: KnowledgeGraphEdge[];
  stats: { total_entities: number; total_relationships: number };
}

export interface KnowledgeMemoryItem {
  memory_id: string;
  memory_type: string;
  fact_text: string;
  confidence: number;
  stability_score: number;
  refresh_count: number;
  scope: string | null;
  created_at: string | null;
  last_accessed_at: string | null;
  expires_at: string | null;
  entity_ids: string[];
  entity_names: string[];
}

export interface KnowledgeMemoryListResponse {
  items: KnowledgeMemoryItem[];
  total: number;
  page: number;
  pages: number;
}

export interface KnowledgeMemoryDetail {
  memory_id: string;
  memory_type: string;
  fact_text: string;
  confidence: number;
  stability_score: number;
  refresh_count: number;
  scope: string | null;
  created_at: string | null;
  last_accessed_at: string | null;
  expires_at: string | null;
  linked_entities: { entity_id: string; canonical_name: string; entity_type: string }[];
  provenance: { source_event_ids: string[]; source_description: string | null };
}

export interface KnowledgeStatsResponse {
  total_entities: number;
  total_relationships: number;
  total_memories: number;
  avg_confidence: number;
  weekly_delta: { entities: number; relationships: number; memories: number };
  entity_counts_by_type: { entity_type: string; count: number }[];
  memory_counts_by_type: { memory_type: string; count: number }[];
  central_entities: { entity_id: string; name: string; entity_type: string; degree: number }[];
  communities: { seed_entity_id: string; seed_name: string; seed_type: string; community_size: number; community_members: string[] }[];
  stale_relationships: { relation_id: string; from_name: string; to_name: string; relation_type: string }[];
  growth_by_day: { date: string; entities: number; memories: number }[];
}

export function fetchKnowledgeGraph(): Promise<KnowledgeGraphResponse> {
  return api("/knowledge/graph");
}

export function fetchKnowledgeMemories(params?: {
  type?: string;
  sort_by?: string;
  search?: string;
  entity_id?: string;
  page?: number;
  limit?: number;
}): Promise<KnowledgeMemoryListResponse> {
  const qs = new URLSearchParams();
  if (params?.type) qs.set("type", params.type);
  if (params?.sort_by) qs.set("sort_by", params.sort_by);
  if (params?.search) qs.set("search", params.search);
  if (params?.entity_id) qs.set("entity_id", params.entity_id);
  if (params?.page) qs.set("page", String(params.page));
  if (params?.limit) qs.set("limit", String(params.limit));
  const q = qs.toString();
  return api(`/knowledge/memories${q ? `?${q}` : ""}`);
}

export function fetchKnowledgeMemoryDetail(
  memoryId: string,
): Promise<KnowledgeMemoryDetail> {
  return api(`/knowledge/memories/${memoryId}`);
}

export function fetchKnowledgeStats(): Promise<KnowledgeStatsResponse> {
  return api("/knowledge/stats");
}
```

- [ ] **Step 3: Verify build**

Run: `cd /Users/sivasankarreddybogala/work/jarvis/frontend && npm run build 2>&1 | tail -5`
Expected: Build succeeds

- [ ] **Step 4: Commit**

```bash
cd /Users/sivasankarreddybogala/work/jarvis && git add frontend/package.json frontend/package-lock.json frontend/src/lib/api.ts
git commit -m "feat: add react-force-graph-2d dependency and knowledge API methods"
```

---

### Task 4: Frontend — Zustand Store

**Files:**
- Create: `frontend/src/stores/knowledge-store.ts`

- [ ] **Step 1: Create the knowledge store**

```typescript
// frontend/src/stores/knowledge-store.ts
import { create } from "zustand";

import type {
  KnowledgeGraphNode,
  KnowledgeGraphEdge,
} from "@/lib/api";

interface GraphData {
  nodes: KnowledgeGraphNode[];
  edges: KnowledgeGraphEdge[];
}

type KnowledgeTab = "graph" | "memories" | "stats";
type MemorySort = "recent" | "confidence" | "stability";

interface KnowledgeState {
  // Tab
  activeTab: KnowledgeTab;
  setActiveTab: (tab: KnowledgeTab) => void;

  // Graph
  graphData: GraphData;
  setGraphData: (data: GraphData) => void;
  mergeGraphData: (data: GraphData) => void;
  selectedEntityId: string | null;
  selectEntity: (id: string | null) => void;
  expandedNodes: Set<string>;
  markExpanded: (id: string) => void;
  hiddenTypes: Set<string>;
  toggleTypeFilter: (type: string) => void;

  // Memories
  selectedMemoryId: string | null;
  selectMemory: (id: string | null) => void;
  memoryTypeFilter: string | null;
  setMemoryTypeFilter: (type: string | null) => void;
  memorySortBy: MemorySort;
  setMemorySortBy: (sort: MemorySort) => void;

  // Search
  searchQuery: string;
  setSearchQuery: (q: string) => void;
}

export const useKnowledgeStore = create<KnowledgeState>((set, get) => ({
  // Tab
  activeTab: "graph",
  setActiveTab: (tab) => set({ activeTab: tab }),

  // Graph
  graphData: { nodes: [], edges: [] },
  setGraphData: (data) => set({ graphData: data }),
  mergeGraphData: (data) => {
    const current = get().graphData;
    const existingNodeIds = new Set(current.nodes.map((n) => n.entity_id));
    const newNodes = data.nodes.filter((n) => !existingNodeIds.has(n.entity_id));

    const existingEdgeKeys = new Set(
      current.edges.map(
        (e) => `${e.from_entity_id ?? e.from}-${e.to_entity_id ?? e.to}`
      )
    );
    const newEdges = data.edges.filter(
      (e) =>
        !existingEdgeKeys.has(
          `${e.from_entity_id ?? e.from}-${e.to_entity_id ?? e.to}`
        )
    );

    set({
      graphData: {
        nodes: [...current.nodes, ...newNodes],
        edges: [...current.edges, ...newEdges],
      },
    });
  },
  selectedEntityId: null,
  selectEntity: (id) => set({ selectedEntityId: id }),
  expandedNodes: new Set(),
  markExpanded: (id) => {
    const next = new Set(get().expandedNodes);
    next.add(id);
    set({ expandedNodes: next });
  },
  hiddenTypes: new Set(),
  toggleTypeFilter: (type) => {
    const next = new Set(get().hiddenTypes);
    if (next.has(type)) {
      next.delete(type);
    } else {
      next.add(type);
    }
    set({ hiddenTypes: next });
  },

  // Memories
  selectedMemoryId: null,
  selectMemory: (id) => set({ selectedMemoryId: id }),
  memoryTypeFilter: null,
  setMemoryTypeFilter: (type) => set({ memoryTypeFilter: type }),
  memorySortBy: "recent",
  setMemorySortBy: (sort) => set({ memorySortBy: sort }),

  // Search
  searchQuery: "",
  setSearchQuery: (q) => set({ searchQuery: q }),
}));
```

- [ ] **Step 2: Verify build**

Run: `cd /Users/sivasankarreddybogala/work/jarvis/frontend && npm run build 2>&1 | tail -5`
Expected: Build succeeds

- [ ] **Step 3: Commit**

```bash
cd /Users/sivasankarreddybogala/work/jarvis && git add frontend/src/stores/knowledge-store.ts
git commit -m "feat: add Zustand knowledge store for graph, memories, tab state"
```

---

### Task 5: Frontend — Graph Tab Components

**Files:**
- Create: `frontend/src/components/knowledge/graph-filters.tsx`
- Create: `frontend/src/components/knowledge/graph-context-menu.tsx`
- Create: `frontend/src/components/knowledge/graph-detail-panel.tsx`
- Create: `frontend/src/components/knowledge/graph-view.tsx`

This is a large task. The implementing agent should create all 4 files following these specifications:

- [ ] **Step 1: Create graph-filters.tsx**

Entity type filter chips: All, Person, Organization, Project, Document, Repository. Uses `useKnowledgeStore` for `hiddenTypes` and `toggleTypeFilter`. Each chip is color-coded matching the design spec color mapping (Person=`j-primary`, Organization=`j-secondary`, Project=`j-accent`, Document=`j-warning`, Repository=`j-error`).

- [ ] **Step 2: Create graph-context-menu.tsx**

A positioned div that appears on right-click a node. Options: "Focus here" (centers graph), "Expand 2 hops" (calls traverse with depth=2), "Hide node" (removes from graphData), "View memories" (switches to Memories tab with entity_id filter). Receives `x`, `y`, `entityId`, `onClose`, and callback props.

- [ ] **Step 3: Create graph-detail-panel.tsx**

Right side panel (w-80) that shows when `selectedEntityId` is set. Sections: avatar header (initials + type color + name + type badge), attributes table, metadata (interaction_count, last_seen_at), connections list (clickable — calls `selectEntity`), related memories (fetched via `fetchKnowledgeMemories({ entity_id, limit: 5 })`), aliases list. Uses TanStack `useQuery` for the related memories fetch.

- [ ] **Step 4: Create graph-view.tsx**

The core graph canvas using `react-force-graph-2d`. Import `ForceGraph2D` from `react-force-graph-2d`. Transform `graphData` from the store into the `{ nodes: [{id, ...}], links: [{source, target, ...}] }` format that react-force-graph expects. Map `entity_id` → `id`, `from_entity_id`/`from` → `source`, `to_entity_id`/`to` → `target`. Custom `nodeCanvasObject` callback for colored circles + labels. Handle `onNodeClick` (select entity, open detail panel), `onNodeRightClick` (open context menu), `onBackgroundClick` (deselect), `onNodeDrag` (pin node). Filter out nodes whose `entity_type` is in `hiddenTypes`. Uses `graphRef.current.centerAt()` for search-driven focus.

- [ ] **Step 5: Verify build**

Run: `cd /Users/sivasankarreddybogala/work/jarvis/frontend && npm run build 2>&1 | tail -10`
Expected: Build succeeds (may have unused import warnings which are ok)

- [ ] **Step 6: Commit**

```bash
cd /Users/sivasankarreddybogala/work/jarvis && git add frontend/src/components/knowledge/graph-filters.tsx frontend/src/components/knowledge/graph-context-menu.tsx frontend/src/components/knowledge/graph-detail-panel.tsx frontend/src/components/knowledge/graph-view.tsx
git commit -m "feat: add graph tab components — force graph, detail panel, filters, context menu"
```

---

### Task 6: Frontend — Search Component

**Files:**
- Create: `frontend/src/components/knowledge/knowledge-search.tsx`

- [ ] **Step 1: Create the unified search component**

A search input with debounced dropdown. Uses `searchAll` from api.ts (the existing `POST /v1/search` endpoint). On typing (300ms debounce), shows categorized results in a dropdown positioned below the input. Groups results by `type` field into Entities, Memories, Relationships sections. Click handlers navigate across tabs via `useKnowledgeStore.setActiveTab()` + `selectEntity()`/`selectMemory()`. Keyboard support: `/` focuses input (registered via `useEffect` keydown listener), `Esc` closes dropdown.

- [ ] **Step 2: Verify build**

Run: `cd /Users/sivasankarreddybogala/work/jarvis/frontend && npm run build 2>&1 | tail -5`
Expected: Build succeeds

- [ ] **Step 3: Commit**

```bash
cd /Users/sivasankarreddybogala/work/jarvis && git add frontend/src/components/knowledge/knowledge-search.tsx
git commit -m "feat: add unified knowledge search with categorized dropdown"
```

---

### Task 7: Frontend — Memories Tab Components

**Files:**
- Create: `frontend/src/components/knowledge/memory-row.tsx`
- Create: `frontend/src/components/knowledge/memory-detail-panel.tsx`
- Create: `frontend/src/components/knowledge/memories-view.tsx`

- [ ] **Step 1: Create memory-row.tsx**

A single row: type icon (colored circle with first letter), fact_text, inline metadata (type label, confidence bar as a `div` with percentage width, stability, relative time), entity chips (clickable → sets activeTab to graph + selectEntity). Props: `memory: KnowledgeMemoryItem`, `selected: boolean`, `onSelect: () => void`, `onEntityClick: (id: string) => void`.

- [ ] **Step 2: Create memory-detail-panel.tsx**

Right panel (w-80) shown when a memory is selected. Fetches full detail via `useQuery({ queryKey: ["knowledge-memory", memoryId], queryFn: () => fetchKnowledgeMemoryDetail(memoryId) })`. Sections: header with type icon + label, full fact_text, properties table (confidence, stability_score, refresh_count, created_at, last_accessed_at, scope, TTL), linked entities list (clickable → switches to Graph tab), provenance block (styled quote), action buttons ("View in Graph", "Archive" via `del(`/memories/${id}`)` with useMutation + invalidation).

- [ ] **Step 3: Create memories-view.tsx**

Full memories tab layout. Type filter chips (All + each memory type, color-coded). Sort pills (Recent, Confidence, Stability). Uses `useQuery` with `fetchKnowledgeMemories()` and params from the knowledge store (`memoryTypeFilter`, `memorySortBy`). Renders a scrollable list of `MemoryRow` components. Infinite scroll: when scrolled near bottom, increments page and fetches next batch. Master-detail layout: list on left, `MemoryDetailPanel` on right when a memory is selected.

- [ ] **Step 4: Verify build**

Run: `cd /Users/sivasankarreddybogala/work/jarvis/frontend && npm run build 2>&1 | tail -5`
Expected: Build succeeds

- [ ] **Step 5: Commit**

```bash
cd /Users/sivasankarreddybogala/work/jarvis && git add frontend/src/components/knowledge/memory-row.tsx frontend/src/components/knowledge/memory-detail-panel.tsx frontend/src/components/knowledge/memories-view.tsx
git commit -m "feat: add memories tab — list, detail panel, filters, infinite scroll"
```

---

### Task 8: Frontend — Stats Tab Components

**Files:**
- Create: `frontend/src/components/knowledge/stat-card.tsx`
- Create: `frontend/src/components/knowledge/bar-chart.tsx`
- Create: `frontend/src/components/knowledge/donut-chart.tsx`
- Create: `frontend/src/components/knowledge/community-card.tsx`
- Create: `frontend/src/components/knowledge/stats-view.tsx`

- [ ] **Step 1: Create stat-card.tsx**

A metric card with label, large value, weekly delta (green up arrow or red down arrow). Props: `label: string`, `value: string | number`, `delta?: number`, `color?: string`. Uses Tailwind classes matching the Jarvis design system (bg-surface-1, border-b-secondary, text-t-primary).

- [ ] **Step 2: Create bar-chart.tsx**

CSS-based vertical bar chart. Props: `data: { label: string; value: number; color?: string }[]`. Calculates max value, renders each bar as a percentage-height div inside a flex container. Labels below each bar, value above.

- [ ] **Step 3: Create donut-chart.tsx**

SVG-based donut chart. Props: `data: { label: string; value: number; color: string }[]`, `total: number`. Uses SVG `<circle>` elements with `stroke-dasharray` and `stroke-dashoffset` to draw segments. Center shows total count. Legend rendered below with colored dots + labels + values.

- [ ] **Step 4: Create community-card.tsx**

Community cluster card. Props: `name: string`, `memberCount: number`, `memberNodes: { initials: string; color: string }[]`. Shows name, "N members" subtitle, row of small colored avatar circles (max 4 + "+N" overflow).

- [ ] **Step 5: Create stats-view.tsx**

Full stats tab layout. Uses `useQuery({ queryKey: ["knowledge-stats"], queryFn: fetchKnowledgeStats })`. Renders: 4 StatCards in a grid row, entity type BarChart + memory type DonutChart in a 2-column row, central entities ranked list (clickable → switches to Graph tab), 2x2 CommunityCard grid (clickable → switches to Graph tab), knowledge growth BarChart (7 days), stale relationships list (warning/error colored by staleness).

- [ ] **Step 6: Verify build**

Run: `cd /Users/sivasankarreddybogala/work/jarvis/frontend && npm run build 2>&1 | tail -5`
Expected: Build succeeds

- [ ] **Step 7: Commit**

```bash
cd /Users/sivasankarreddybogala/work/jarvis && git add frontend/src/components/knowledge/stat-card.tsx frontend/src/components/knowledge/bar-chart.tsx frontend/src/components/knowledge/donut-chart.tsx frontend/src/components/knowledge/community-card.tsx frontend/src/components/knowledge/stats-view.tsx
git commit -m "feat: add stats tab — metric cards, charts, communities, growth timeline"
```

---

### Task 9: Frontend — Page Shell and Wiring

**Files:**
- Create: `frontend/src/app/knowledge/page.tsx`

- [ ] **Step 1: Create the knowledge page**

```typescript
// frontend/src/app/knowledge/page.tsx
"use client";

import { useEffect, useCallback } from "react";
import { useSearchParams, useRouter } from "next/navigation";
import { useQuery } from "@tanstack/react-query";
import { PageHeader } from "@/components/layout/page-header";
import { Tabs } from "@/components/ui/tabs";
import { useKnowledgeStore } from "@/stores/knowledge-store";
import { fetchKnowledgeGraph } from "@/lib/api";
import { KnowledgeSearch } from "@/components/knowledge/knowledge-search";
import { GraphView } from "@/components/knowledge/graph-view";
import { GraphFilters } from "@/components/knowledge/graph-filters";
import { GraphDetailPanel } from "@/components/knowledge/graph-detail-panel";
import { MemoriesView } from "@/components/knowledge/memories-view";
import { StatsView } from "@/components/knowledge/stats-view";

type KnowledgeTab = "graph" | "memories" | "stats";

const TABS = [
  { key: "graph", label: "Graph" },
  { key: "memories", label: "Memories" },
  { key: "stats", label: "Stats" },
];

export default function KnowledgePage() {
  const searchParams = useSearchParams();
  const router = useRouter();

  const activeTab = useKnowledgeStore((s) => s.activeTab);
  const setActiveTab = useKnowledgeStore((s) => s.setActiveTab);
  const setGraphData = useKnowledgeStore((s) => s.setGraphData);
  const selectEntity = useKnowledgeStore((s) => s.selectEntity);
  const selectMemory = useKnowledgeStore((s) => s.selectMemory);
  const selectedEntityId = useKnowledgeStore((s) => s.selectedEntityId);

  // Sync tab from URL on mount
  useEffect(() => {
    const tabParam = searchParams.get("tab") as KnowledgeTab | null;
    if (tabParam && ["graph", "memories", "stats"].includes(tabParam)) {
      setActiveTab(tabParam);
    }

    const entityParam = searchParams.get("entity");
    if (entityParam) {
      setActiveTab("graph");
      selectEntity(entityParam);
    }

    const memoryParam = searchParams.get("memory");
    if (memoryParam) {
      setActiveTab("memories");
      selectMemory(memoryParam);
    }
  }, [searchParams, setActiveTab, selectEntity, selectMemory]);

  // Fetch initial graph data
  const { data: graphResponse, isLoading: graphLoading } = useQuery({
    queryKey: ["knowledge-graph"],
    queryFn: fetchKnowledgeGraph,
  });

  // Set graph data when loaded
  useEffect(() => {
    if (graphResponse) {
      setGraphData({
        nodes: graphResponse.nodes,
        edges: graphResponse.edges,
      });
    }
  }, [graphResponse, setGraphData]);

  const handleTabChange = useCallback(
    (key: string) => {
      setActiveTab(key as KnowledgeTab);
      const params = new URLSearchParams(searchParams.toString());
      if (key === "graph") {
        params.delete("tab");
      } else {
        params.set("tab", key);
      }
      params.delete("entity");
      params.delete("memory");
      router.replace(`/knowledge?${params.toString()}`);
    },
    [setActiveTab, searchParams, router],
  );

  const statsLabel = graphResponse
    ? `${graphResponse.stats.total_entities} entities · ${graphResponse.stats.total_relationships} relationships`
    : "";

  return (
    <div className="flex flex-col h-full overflow-hidden">
      {/* Top bar */}
      <div className="flex items-center gap-3 px-4 py-3 border-b border-b-secondary bg-surface-1">
        <PageHeader title="Knowledge" subtitle="" />
        <KnowledgeSearch />
        {statsLabel && (
          <span className="text-xs text-t-muted ml-auto whitespace-nowrap">
            {statsLabel}
          </span>
        )}
      </div>

      {/* Tabs */}
      <div className="px-4 bg-surface-1">
        <Tabs tabs={TABS} active={activeTab} onChange={handleTabChange} />
      </div>

      {/* Tab content */}
      <div className="flex-1 overflow-hidden">
        {activeTab === "graph" && (
          <div className="flex flex-col h-full">
            <GraphFilters />
            <div className="flex flex-1 overflow-hidden">
              <div className="flex-1 relative">
                {graphLoading ? (
                  <div className="flex items-center justify-center h-full">
                    <p className="text-t-tertiary text-sm">Loading graph...</p>
                  </div>
                ) : (
                  <GraphView />
                )}
              </div>
              {selectedEntityId && <GraphDetailPanel />}
            </div>
          </div>
        )}

        {activeTab === "memories" && <MemoriesView />}

        {activeTab === "stats" && <StatsView />}
      </div>
    </div>
  );
}
```

- [ ] **Step 2: Verify build**

Run: `cd /Users/sivasankarreddybogala/work/jarvis/frontend && npm run build 2>&1 | tail -10`
Expected: Build succeeds

- [ ] **Step 3: Commit**

```bash
cd /Users/sivasankarreddybogala/work/jarvis && git add frontend/src/app/knowledge/page.tsx
git commit -m "feat: add Knowledge page shell with tab routing and graph data loading"
```

---

### Task 10: Frontend — Sidebar + Settings Cleanup

**Files:**
- Modify: `frontend/src/components/layout/sidebar.tsx`
- Modify: `frontend/src/app/settings/page.tsx`
- Delete: `frontend/src/components/settings/preferences-panel.tsx`

- [ ] **Step 1: Add Knowledge nav item to sidebar**

In `frontend/src/components/layout/sidebar.tsx`, insert a new `NavItem` between the Search and Integrations items (between line 122 and 123):

```typescript
        <NavItem
          href="/knowledge"
          label="Knowledge"
          active={pathname === "/knowledge"}
          collapsed={collapsed}
          icon={
            <svg width="16" height="16" viewBox="0 0 16 16" fill="none">
              <circle cx="5" cy="5" r="2" stroke="currentColor" strokeWidth="1.3" />
              <circle cx="11" cy="5" r="2" stroke="currentColor" strokeWidth="1.3" />
              <circle cx="8" cy="11" r="2" stroke="currentColor" strokeWidth="1.3" />
              <path d="M6.5 6.5L7.5 9.5M9.5 6.5L8.5 9.5M7 5h2" stroke="currentColor" strokeWidth="1" strokeLinecap="round" />
            </svg>
          }
        />
```

- [ ] **Step 2: Remove Preferences tab from Settings page**

In `frontend/src/app/settings/page.tsx`:
1. Remove the import of `PreferencesPanel`
2. Remove `{ key: "preferences", label: "Preferences" }` from the TABS array
3. Remove the `{activeTab === "preferences" && <PreferencesPanel />}` conditional render block
4. Update the `SettingsTab` type to remove `"preferences"`

- [ ] **Step 3: Delete preferences-panel.tsx**

Run: `rm frontend/src/components/settings/preferences-panel.tsx`

- [ ] **Step 4: Verify build**

Run: `cd /Users/sivasankarreddybogala/work/jarvis/frontend && npm run build 2>&1 | tail -10`
Expected: Build succeeds with no import errors

- [ ] **Step 5: Commit**

```bash
cd /Users/sivasankarreddybogala/work/jarvis && git add frontend/src/components/layout/sidebar.tsx frontend/src/app/settings/page.tsx
git rm frontend/src/components/settings/preferences-panel.tsx
git commit -m "feat: add Knowledge to sidebar, remove Preferences tab from Settings"
```

---

### Task 11: Integration Test — Full Page Smoke Test

- [ ] **Step 1: Run full backend test suite**

Run: `cd /Users/sivasankarreddybogala/work/jarvis/backend && python -m pytest tests/ -v --timeout=60 -x -q 2>&1 | tail -20`
Expected: All tests pass including new knowledge tests

- [ ] **Step 2: Run frontend build**

Run: `cd /Users/sivasankarreddybogala/work/jarvis/frontend && npm run build 2>&1 | tail -10`
Expected: Build succeeds

- [ ] **Step 3: Run frontend lint**

Run: `cd /Users/sivasankarreddybogala/work/jarvis/frontend && npm run lint 2>&1 | tail -10`
Expected: No errors (warnings are acceptable)

- [ ] **Step 4: Run backend lint**

Run: `cd /Users/sivasankarreddybogala/work/jarvis/backend && ruff check src/services/knowledge_service.py src/api/routes_knowledge.py`
Expected: No errors

- [ ] **Step 5: Final commit if any fixes were needed**

```bash
git add -A && git commit -m "fix: resolve lint and build issues for knowledge page"
```
