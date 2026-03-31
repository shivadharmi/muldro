# Knowledge Page — Design Spec

**Date**: 2026-03-31
**Status**: Approved
**Branch**: `improve-the-perception-system-v1`

## Summary

A single `/knowledge` page with 3 tabs (Graph, Memories, Stats) that surfaces the user's full context: entity relationships from Neo4j, memories from Qdrant/Postgres, and growth analytics. Replaces the Preferences tab in Settings. Added as a new sidebar item.

## Goals

1. Let the user visually explore their entity relationship graph with full interactivity (zoom, pan, drag, search, expand neighbors)
2. Provide a searchable, filterable view of all memory types (not just preferences/goals)
3. Show how the user's knowledge base has grown over time (stats/analytics)
4. Enable cross-navigation: click an entity in a memory → jump to it in the graph, and vice versa

## Non-Goals

- Real-time graph updates via WebSocket (polling on tab focus is sufficient for v1)
- 3D graph rendering (2D is simpler and more readable)
- Memory editing (read-only + archive; editing memories is a future feature)
- Graph layout persistence (positions reset on reload; pinned positions are session-only)

---

## Page Structure & Routing

```
/knowledge                     → Knowledge page (Graph tab default)
/knowledge?tab=memories        → Memories tab
/knowledge?tab=stats           → Stats tab
/knowledge?entity={entity_id}  → Graph tab, focused on entity with detail panel open
/knowledge?memory={memory_id}  → Memories tab, memory selected with detail panel open
```

### Sidebar Change

New "Knowledge" item (brain/network icon) at position 4 in the sidebar, between Search and Integrations:

1. Workspace (`/`)
2. Chat (`/chat`)
3. Search (`/search`)
4. **Knowledge (`/knowledge`)** ← new
5. Integrations (`/integrations`)
6. Settings (`/settings`)

### Settings Change

Remove the "Preferences" tab from Settings. Remaining tabs: Account, Policy, Budget. The Knowledge page's Memories tab supersedes it with richer functionality.

**Files to modify:**
- `frontend/src/components/layout/sidebar.tsx` — add Knowledge nav item
- `frontend/src/app/settings/page.tsx` — remove Preferences tab
- `frontend/src/components/settings/preferences-panel.tsx` — delete file

---

## Unified Search Bar

A single search bar in the topbar, consistent across all tabs. Uses the existing `POST /v1/search` TriSearch endpoint (Qdrant + Postgres FTS + Neo4j in parallel).

### Behavior

- **Debounced input** (300ms) triggers search
- **Dropdown** appears with results categorized into 3 groups:
  - **Entities** — shows name, type badge, importance score
  - **Memories** — shows fact text snippet, memory type, confidence
  - **Relationships** — shows `from → relation_type → to`

### Click Actions

| Result Type | Action |
|---|---|
| Entity | Switch to Graph tab, center graph on node, open detail panel |
| Memory | Switch to Memories tab, scroll to and select memory, open detail panel |
| Relationship | Switch to Graph tab, highlight path between the two entities |

### Tab-Aware Enhancement

- **On Graph tab**: matching entities get a glow highlight in the graph (in addition to dropdown)
- **On Memories tab**: memory list live-filters to match search query (in addition to dropdown)

### Keyboard Shortcuts

- `/` focuses the search bar
- `Up/Down` navigates dropdown results
- `Enter` selects highlighted result
- `Esc` closes dropdown

---

## Tab 1: Graph View

### Rendering

**Library**: `react-force-graph-2d` — React wrapper around d3-force. Handles zoom, pan, drag, physics simulation, and click events. ~45KB gzipped.

### Node Design

- **Color by entity type**:
  - Person → Cyan (`--jarvis-primary`)
  - Organization → Violet (`--jarvis-secondary`)
  - Project → Emerald (`--jarvis-accent`)
  - Document → Amber (`--jarvis-warning`)
  - Repository → Error red (`--jarvis-error`)
  - Other → muted gray (`--jarvis-text-muted`)
- **Size**: scales with `importance_score` (min 6px, max 24px radius)
- **Label**: `canonical_name`, truncated to 16 characters
- **Selected state**: glow shadow (`--shadow-glow`), scale 1.2x

### Edge Design

- Thin lines (`1px`) with `--jarvis-border-default` color
- Directional arrow on the target end
- `relation_type` label shown on hover only (tooltip)
- Selected entity's edges highlighted with `--jarvis-primary` color

### Interactions

| Action | Behavior |
|---|---|
| Click node | Open detail panel on right, highlight connected edges |
| Double-click node | Expand neighbors — fetch 1-hop via `/v1/graph/{id}/traverse?depth=1`, merge into graph |
| Right-click node | Context menu: "Focus here", "Expand 2 hops", "Hide node", "View memories" |
| Drag node | Reposition (pin in place), other nodes re-simulate around it |
| Scroll wheel | Zoom in/out |
| Click + drag canvas | Pan |
| Click empty space | Close detail panel, deselect node |
| Hover edge | Tooltip with relation_type |

### Initial Load

1. Call `GET /v1/knowledge/graph` which internally:
   - Calls `GraphEngine.find_central_entities(limit=10)` for seed nodes
   - Calls `GraphEngine.get_subgraph(entity_ids)` to get edges between them
   - Returns `{ nodes, edges, stats: { total_entities, total_relationships } }`
2. Render the initial graph with physics simulation
3. User expands from there via double-click

### Filter Chips

Entity type filter chips below the tabs (All, Person, Organization, Project, Document, Repository). Toggling filters hides/shows nodes of that type **client-side** (no re-fetch). Hidden nodes' edges are also hidden.

### Detail Panel (Right Side, 300px)

Appears when a node is clicked. Sections:

1. **Header**: Avatar circle (initials + type color), name, type badge, importance score
2. **Attributes**: Key-value pairs from `entity.attributes` JSONB (role, company, etc.)
3. **Metadata**: `interaction_count`, `last_seen_at`, `confidence_score`
4. **Connections**: List of connected entities with relationship type label. Each is clickable (centers graph on that entity).
5. **Related Memories**: Top 5 memories linked to this entity (via `entity_refs` on memories). Each shows type icon, text snippet, confidence. Clickable → switches to Memories tab.
6. **Aliases**: List of known aliases (email, handle, name)

**Data sources:**
- Node attributes: from the graph node payload (already loaded)
- Connections: from the graph edges (already loaded)
- Related memories: `GET /v1/knowledge/memories?entity_id={id}&limit=5`
- Aliases: included in entity detail from `GET /v1/knowledge/graph` node payload

---

## Tab 2: Memories View

### Layout

Master-detail list: scrollable memory list on the left, detail panel on the right (appears when a memory is selected).

### Filter Bar

**Type filters** (pill chips): All, Semantic, Episodic, Preference, Goal, Relationship, Procedural
**Sort options**: Recent (default), Confidence, Stability

### Memory Row

Each row shows:
- **Type icon**: Color-coded circle with first letter (S=semantic in violet, E=episodic in cyan, G=goal in emerald, P=preference in amber, R=relationship in gray)
- **Memory text**: The `fact_text` content
- **Inline metadata**: Type label, confidence bar (visual), stability score, relative time
- **Entity chips**: Linked entity names as small badges (clickable → switches to Graph tab centered on entity)

### Memory Detail Panel (Right Side, 320px)

Appears when a memory row is selected. Sections:

1. **Header**: Type icon + type label (e.g., "Semantic Memory")
2. **Full text**: Complete `fact_text`
3. **Properties**: confidence, stability_score, refresh_count, created_at, last_accessed_at, scope, TTL (or "permanent")
4. **Linked Entities**: Clickable list with entity type indicator. Click → switches to Graph tab.
5. **Provenance**: Source text showing where the memory was extracted from (derived from `source_event_ids`)
6. **Actions**: "View in Graph" button (centers graph on first linked entity) and "Archive" button (calls `DELETE /v1/memories/{id}`)

### Pagination

Server-side pagination. 50 items per page. Infinite scroll (fetch next page when scrolled to bottom).

---

## Tab 3: Stats View

### Top Metrics Row (4 cards)

| Metric | Source |
|---|---|
| Total Entities | `COUNT(*)` on entities table |
| Total Relationships | `COUNT(*)` on entity_relationships table |
| Total Memories | `COUNT(*)` on memories table (status=active) |
| Avg Confidence | `AVG(confidence)` on memories table |

Each card shows weekly delta (compare with 7 days ago).

### Entity Type Distribution (Bar Chart)

Horizontal bar chart showing count per entity_type. Top 6 types shown, rest grouped as "Other". CSS-based bars (no charting library).

### Memory Type Breakdown (Donut Chart)

SVG donut chart with conic gradient showing memory_type distribution. Legend on the right with counts. Memory types: Semantic, Episodic, Preference, Relationship, Goal/Other.

### Most Connected Entities (Table)

Top 5 entities by degree centrality. Uses existing `GraphEngine.find_central_entities(limit=5)`. Shows rank, name, type badge, connection count.

### Communities Detected (Card Grid)

2x2 grid of community cards. Uses existing `GraphEngine.detect_communities()`. Each card shows: community seed name, member count, member avatars (small colored circles).

### Knowledge Growth (Bar Chart)

Bar chart showing new entities + memories created per day over the last 7 days. Source: `COUNT(*) GROUP BY DATE(created_at)` on entities and memories tables.

### Stale Relationships (List)

Relationships not updated in 14+ days. Uses existing `GraphEngine.get_stale_relationships()`. Each row shows: from_entity → relation_type → to_entity, with "days ago" label. Warning color for 14-21 days, error color for 21+.

---

## New Backend Endpoints

All endpoints in a new router: `backend/src/api/routes_knowledge.py`

### GET /v1/knowledge/graph

Returns the initial graph payload for rendering.

**Implementation:**
1. Call `GraphEngine.find_central_entities(user_id, limit=10)`
2. Collect entity_ids from result
3. Call `GraphEngine.get_subgraph(entity_ids, user_id)`
4. Enrich nodes with entity attributes from Postgres (batch query)
5. Return `{ nodes: [...], edges: [...], stats: { total_entities, total_relationships } }`

**Node schema:**
```json
{
  "entity_id": "ent_...",
  "canonical_name": "Alice Park",
  "entity_type": "person",
  "importance_score": 0.85,
  "interaction_count": 42,
  "last_seen_at": "2026-03-30T14:00:00Z",
  "attributes": { "role": "Head of Product", "company": "Acme Corp" },
  "aliases": ["alice@acme.com", "alice.park"]
}
```

**Edge schema:**
```json
{
  "from_entity_id": "ent_...",
  "to_entity_id": "ent_...",
  "relation_type": "reports_to",
  "relation_id": "rel_..."
}
```

### GET /v1/knowledge/memories

Paginated, filterable memory list.

**Query params:**
- `type` (optional): memory_type filter (semantic, episodic, preference, goal, relationship, procedural)
- `sort_by` (optional): `recent` (default), `confidence`, `stability`
- `search` (optional): text search filter (uses Postgres FTS)
- `entity_id` (optional): filter memories linked to a specific entity
- `page` (default 1)
- `limit` (default 50, max 100)

**Response:**
```json
{
  "items": [
    {
      "memory_id": "mem_...",
      "memory_type": "semantic",
      "fact_text": "...",
      "confidence": 0.92,
      "stability_score": 0.8,
      "refresh_count": 8,
      "scope": "general",
      "created_at": "2026-03-28T10:00:00Z",
      "last_accessed_at": "2026-03-31T08:00:00Z",
      "expires_at": null,
      "entity_ids": ["ent_...", "ent_..."],
      "entity_names": ["Siva Sankar", "Acme Corp"]
    }
  ],
  "total": 486,
  "page": 1,
  "pages": 10
}
```

### GET /v1/knowledge/memories/{memory_id}

Full memory detail with provenance.

**Response:**
```json
{
  "memory_id": "mem_...",
  "memory_type": "semantic",
  "fact_text": "...",
  "confidence": 0.92,
  "stability_score": 0.8,
  "refresh_count": 8,
  "scope": "general",
  "created_at": "2026-03-28T10:00:00Z",
  "last_accessed_at": "2026-03-31T08:00:00Z",
  "expires_at": null,
  "linked_entities": [
    { "entity_id": "ent_...", "canonical_name": "Siva Sankar", "entity_type": "person" }
  ],
  "provenance": {
    "source_event_ids": ["evt_..."],
    "source_description": "Extracted from email thread 'Re: Weekly sync format'"
  }
}
```

### GET /v1/knowledge/stats

Aggregated dashboard data. Single endpoint to minimize round-trips.

**Response:**
```json
{
  "total_entities": 142,
  "total_relationships": 89,
  "total_memories": 486,
  "avg_confidence": 0.89,
  "weekly_delta": {
    "entities": 12,
    "relationships": 7,
    "memories": 34,
    "confidence": 0.02
  },
  "entity_counts_by_type": [
    { "entity_type": "person", "count": 48 },
    { "entity_type": "organization", "count": 22 }
  ],
  "memory_counts_by_type": [
    { "memory_type": "semantic", "count": 156 },
    { "memory_type": "episodic", "count": 102 }
  ],
  "central_entities": [
    { "entity_id": "ent_...", "name": "Siva Sankar", "entity_type": "person", "degree": 24 }
  ],
  "communities": [
    { "seed_entity_id": "ent_...", "seed_name": "Siva Sankar", "seed_type": "person", "community_size": 8, "community_members": ["ent_..."] }
  ],
  "stale_relationships": [
    { "relation_id": "rel_...", "from_name": "Siva Sankar", "to_name": "Design Review", "relation_type": "scheduled_with", "days_stale": 21 }
  ],
  "growth_by_day": [
    { "date": "2026-03-25", "entities": 8, "memories": 12 }
  ]
}
```

---

## Frontend Components

```
frontend/src/app/knowledge/
  page.tsx                         ← Page shell: topbar, search, tab router

frontend/src/components/knowledge/
  knowledge-search.tsx             ← Search bar with categorized dropdown
  search-dropdown.tsx              ← Dropdown results renderer
  graph-view.tsx                   ← react-force-graph-2d wrapper + canvas
  graph-detail-panel.tsx           ← Right panel for selected entity
  graph-filters.tsx                ← Entity type filter chips
  graph-context-menu.tsx           ← Right-click menu (Focus, Expand, Hide, View memories)
  memories-view.tsx                ← Memory list layout + filters + pagination
  memory-row.tsx                   ← Single memory row component
  memory-detail-panel.tsx          ← Right panel for selected memory
  stats-view.tsx                   ← Stats dashboard layout
  stat-card.tsx                    ← Single top-level metric card
  bar-chart.tsx                    ← CSS-based bar chart
  donut-chart.tsx                  ← SVG donut chart
  community-card.tsx               ← Community cluster card
  stale-relationships.tsx          ← Stale relationships list

frontend/src/stores/
  knowledge-store.ts               ← Zustand store for graph/memory/tab state

frontend/src/lib/
  api.ts                           ← Add knowledge API methods (4 endpoints)
```

### New Dependency

```
react-force-graph-2d    — interactive 2D force-directed graph renderer
```

### Zustand Store

```typescript
interface KnowledgeStore {
  // Tab
  activeTab: 'graph' | 'memories' | 'stats'
  setActiveTab: (tab: string) => void

  // Graph
  graphData: { nodes: GraphNode[]; edges: GraphEdge[] }
  setGraphData: (data: GraphData) => void
  mergeGraphData: (data: GraphData) => void  // for expand
  selectedEntityId: string | null
  selectEntity: (id: string | null) => void
  expandedNodes: Set<string>
  markExpanded: (id: string) => void
  hiddenTypes: Set<string>
  toggleTypeFilter: (type: string) => void

  // Memories
  selectedMemoryId: string | null
  selectMemory: (id: string | null) => void
  memoryTypeFilter: string | null
  setMemoryTypeFilter: (type: string | null) => void
  memorySortBy: 'recent' | 'confidence' | 'stability'
  setMemorySortBy: (sort: string) => void

  // Search
  searchQuery: string
  setSearchQuery: (q: string) => void
}
```

---

## Backend File Structure

```
backend/src/api/
  routes_knowledge.py              ← New router: 4 endpoints

backend/src/services/
  knowledge_service.py             ← Orchestrates GraphEngine + MemoryService + DB queries
```

### knowledge_service.py

A thin orchestration service (no new business logic):

- `get_initial_graph(user_id, workspace_id, db)` — calls GraphEngine + enriches from Postgres
- `get_memories_paginated(user_id, workspace_id, db, filters)` — queries memories table with filters
- `get_memory_detail(memory_id, user_id, workspace_id, db)` — single memory + linked entities + provenance
- `get_stats(user_id, workspace_id, db)` — aggregation queries + GraphEngine calls

---

## Color Mapping Reference

Consistent across all 3 tabs:

| Entity Type | Color Token | Usage |
|---|---|---|
| Person | `--jarvis-primary` (cyan) | Graph nodes, entity chips, badges |
| Organization | `--jarvis-secondary` (violet) | Graph nodes, entity chips, badges |
| Project | `--jarvis-accent` (emerald) | Graph nodes, entity chips, badges |
| Document | `--jarvis-warning` (amber) | Graph nodes, entity chips, badges |
| Repository | `--jarvis-error` (red) | Graph nodes, entity chips, badges |
| Other | `--jarvis-text-muted` (gray) | Graph nodes, entity chips, badges |

| Memory Type | Color Token | Icon |
|---|---|---|
| Semantic | `--jarvis-secondary` (violet) | S |
| Episodic | `--jarvis-primary` (cyan) | E |
| Preference | `--jarvis-warning` (amber) | P |
| Goal | `--jarvis-accent` (emerald) | G |
| Relationship | `--jarvis-text-muted` (gray) | R |
| Procedural | `--jarvis-border-strong` (gray) | X |
| Task Context | `--jarvis-error` (red) | T |
| Briefing Item | `--jarvis-info` (cyan) | B |

---

## Cross-Tab Navigation

| From | Action | To |
|---|---|---|
| Graph → Memory | Click "Related Memories" in detail panel | Memories tab, memory selected |
| Memory → Graph | Click entity chip on memory row | Graph tab, centered on entity |
| Memory → Graph | Click "View in Graph" in detail panel | Graph tab, centered on first linked entity |
| Search → Graph | Click entity result in dropdown | Graph tab, centered on entity |
| Search → Memories | Click memory result in dropdown | Memories tab, memory selected |
| Stats → Graph | Click entity in "Most Connected" table | Graph tab, centered on entity |
| Stats → Graph | Click community card | Graph tab, subgraph of community members |

---

## What Gets Deleted

- `frontend/src/components/settings/preferences-panel.tsx` — entire file
- Preferences tab entry in `frontend/src/app/settings/page.tsx`

---

## Implementation Order

1. **Backend**: `knowledge_service.py` + `routes_knowledge.py` (4 endpoints)
2. **Frontend store**: `knowledge-store.ts`
3. **Graph tab**: `graph-view.tsx` + `graph-detail-panel.tsx` + `graph-filters.tsx` + `graph-context-menu.tsx`
4. **Search**: `knowledge-search.tsx` + `search-dropdown.tsx`
5. **Memories tab**: `memories-view.tsx` + `memory-row.tsx` + `memory-detail-panel.tsx`
6. **Stats tab**: `stats-view.tsx` + charts + community cards
7. **Page shell**: `knowledge/page.tsx` (tab routing, topbar, layout)
8. **Sidebar + Settings cleanup**: Add nav item, remove Preferences tab
9. **Cross-tab navigation wiring**
