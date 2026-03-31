"""Tests for KnowledgeService — graph, memories, and stats queries.

Covers all 4 public methods:
- get_initial_graph
- get_memories_paginated
- get_memory_detail
- get_stats
"""

from datetime import datetime, timezone
from unittest.mock import AsyncMock, MagicMock

from tests.conftest import TEST_USER_ID, TEST_WORKSPACE_ID, make_mock_settings

# ── Helpers ─────────────────────────────────────────────────────────────


def _make_memory(
    memory_id: str = "mem_001",
    memory_type: str = "semantic",
    scope: str = "general",
    fact_text: str = "Alice is CFO at Acme Corp",
    confidence: float = 0.9,
    stability_score: float = 0.5,
    status: str = "active",
    refresh_count: int = 0,
    entity_ids: list[str] | None = None,
    source_event_ids: list[str] | None = None,
    last_accessed_at: datetime | None = None,
    created_at: datetime | None = None,
    updated_at: datetime | None = None,
) -> MagicMock:
    """Create a mock Memory object."""
    mem = MagicMock()
    mem.memory_id = memory_id
    mem.user_id = TEST_USER_ID
    mem.workspace_id = TEST_WORKSPACE_ID
    mem.memory_type = memory_type
    mem.scope = scope
    mem.fact_text = fact_text
    mem.confidence = confidence
    mem.stability_score = stability_score
    mem.status = status
    mem.refresh_count = refresh_count
    mem.entity_ids = entity_ids
    mem.source_event_ids = source_event_ids
    mem.last_accessed_at = last_accessed_at
    mem.created_at = created_at or datetime(2026, 3, 28, 10, 0, tzinfo=timezone.utc)
    mem.updated_at = updated_at or datetime(2026, 3, 28, 10, 0, tzinfo=timezone.utc)
    return mem


def _make_entity(
    entity_id: str = "ent_001",
    entity_type: str = "person",
    canonical_name: str = "Alice",
    importance_score: float = 0.8,
    confidence_score: float = 0.9,
    interaction_count: int = 5,
    last_seen_at: datetime | None = None,
    attributes: dict | None = None,
) -> MagicMock:
    """Create a mock Entity object."""
    ent = MagicMock()
    ent.entity_id = entity_id
    ent.entity_type = entity_type
    ent.canonical_name = canonical_name
    ent.importance_score = importance_score
    ent.confidence_score = confidence_score
    ent.interaction_count = interaction_count
    ent.last_seen_at = last_seen_at
    ent.attributes = attributes or {}
    ent.workspace_id = TEST_WORKSPACE_ID
    return ent


def _make_alias(entity_id: str, alias: str, alias_type: str = "name") -> MagicMock:
    """Create a mock EntityAlias object."""
    a = MagicMock()
    a.entity_id = entity_id
    a.alias = alias
    a.alias_type = alias_type
    a.workspace_id = TEST_WORKSPACE_ID
    return a


def _make_event(
    event_id: str = "evt_001",
    title: str = "Investor follow-up",
    source: str = "gmail",
    summary: str = "Investor requested latest deck",
) -> MagicMock:
    """Create a mock NormalizedEvent object."""
    ev = MagicMock()
    ev.event_id = event_id
    ev.title = title
    ev.source = source
    ev.summary = summary
    ev.workspace_id = TEST_WORKSPACE_ID
    return ev


def _make_graph_engine(
    central_entities: list[dict] | None = None,
    subgraph: dict | None = None,
    communities: list[dict] | None = None,
    stale_relationships: list[dict] | None = None,
) -> MagicMock:
    """Create a mock GraphEngine."""
    ge = MagicMock()
    ge.find_central_entities = AsyncMock(return_value=central_entities or [])
    ge.get_subgraph = AsyncMock(return_value=subgraph or {"nodes": [], "edges": []})
    ge.detect_communities = AsyncMock(return_value=communities or [])
    ge.get_stale_relationships = AsyncMock(return_value=stale_relationships or [])
    ge.close = AsyncMock()
    return ge


def _mock_db_execute(return_values: list):
    """Create a mock db with chained execute calls returning different results.

    Each item in return_values is used for successive db.execute() calls.
    """
    db = AsyncMock()
    side_effects = []
    for rv in return_values:
        mock_result = MagicMock()
        if isinstance(rv, int):
            # Scalar result (count)
            mock_result.scalar.return_value = rv
            mock_result.scalar_one_or_none.return_value = rv
        elif isinstance(rv, list):
            # List result (scalars().all() or .all())
            mock_result.scalars.return_value.all.return_value = rv
            mock_result.all.return_value = rv
        elif rv is None:
            mock_result.scalar.return_value = None
            mock_result.scalar_one_or_none.return_value = None
            mock_result.scalars.return_value.all.return_value = []
            mock_result.all.return_value = []
        else:
            # Single object (scalar_one_or_none)
            mock_result.scalar_one_or_none.return_value = rv
            mock_result.scalar.return_value = rv
        side_effects.append(mock_result)
    db.execute = AsyncMock(side_effect=side_effects)
    return db


# ── Tests: get_initial_graph ────────────────────────────────────────────


async def test_get_initial_graph_with_central_entities():
    """Returns enriched nodes, edges, and stats when central entities exist."""
    from src.services.knowledge_service import KnowledgeService

    central = [
        {"entity_id": "ent_001", "name": "Alice", "entity_type": "person", "degree": 5},
        {"entity_id": "ent_002", "name": "Acme", "entity_type": "organization", "degree": 3},
    ]
    subgraph = {
        "nodes": [
            {"entity_id": "ent_001", "name": "Alice", "type": "person"},
            {"entity_id": "ent_002", "name": "Acme", "type": "organization"},
        ],
        "edges": [
            {"from": "ent_001", "to": "ent_002", "type": "works_at"},
        ],
    }
    graph_engine = _make_graph_engine(central_entities=central, subgraph=subgraph)

    ent1 = _make_entity(entity_id="ent_001", canonical_name="Alice", entity_type="person")
    ent2 = _make_entity(entity_id="ent_002", canonical_name="Acme Corp", entity_type="organization")
    alias1 = _make_alias("ent_001", "alice@acme.com", "email")

    # DB calls: entities query, aliases query, count entities, count relationships
    db = _mock_db_execute(
        [
            [ent1, ent2],  # entities
            [alias1],  # aliases
            42,  # count entities
            15,  # count relationships
        ]
    )

    svc = KnowledgeService(make_mock_settings(), db, graph_engine)
    result = await svc.get_initial_graph(TEST_USER_ID, TEST_WORKSPACE_ID)

    assert len(result["nodes"]) == 2
    assert result["edges"] == [{"from": "ent_001", "to": "ent_002", "type": "works_at"}]
    assert result["stats"]["total_entities"] == 42
    assert result["stats"]["total_relationships"] == 15

    # Verify enrichment
    alice_node = next(n for n in result["nodes"] if n["entity_id"] == "ent_001")
    assert alice_node["canonical_name"] == "Alice"
    assert alice_node["importance_score"] == 0.8
    assert len(alice_node["aliases"]) == 1
    assert alice_node["aliases"][0]["alias"] == "alice@acme.com"

    graph_engine.find_central_entities.assert_awaited_once_with(user_id=TEST_USER_ID, limit=10)
    graph_engine.get_subgraph.assert_awaited_once_with(
        entity_ids=["ent_001", "ent_002"], user_id=TEST_USER_ID
    )


async def test_get_initial_graph_empty():
    """Returns empty nodes/edges when no central entities exist."""
    from src.services.knowledge_service import KnowledgeService

    graph_engine = _make_graph_engine(central_entities=[])

    # DB calls: count entities, count relationships
    db = _mock_db_execute([10, 3])

    svc = KnowledgeService(make_mock_settings(), db, graph_engine)
    result = await svc.get_initial_graph(TEST_USER_ID, TEST_WORKSPACE_ID)

    assert result["nodes"] == []
    assert result["edges"] == []
    assert result["stats"]["total_entities"] == 10
    assert result["stats"]["total_relationships"] == 3
    graph_engine.get_subgraph.assert_not_awaited()


# ── Tests: get_memories_paginated ───────────────────────────────────────


async def test_get_memories_paginated_basic():
    """Returns paginated memories with entity name resolution."""
    from src.services.knowledge_service import KnowledgeService

    mem1 = _make_memory(
        memory_id="mem_001",
        entity_ids=["ent_001"],
        fact_text="Alice is CFO",
    )
    mem2 = _make_memory(
        memory_id="mem_002",
        entity_ids=None,
        fact_text="Budget is $5M",
    )

    # Mocking entity name resolution
    name_row = MagicMock()
    name_row.entity_id = "ent_001"
    name_row.canonical_name = "Alice"

    # DB calls: count, memories list, entity names
    db = _mock_db_execute(
        [
            2,  # total count
            [mem1, mem2],  # memories
            [name_row],  # entity names
        ]
    )

    graph_engine = _make_graph_engine()
    svc = KnowledgeService(make_mock_settings(), db, graph_engine)
    result = await svc.get_memories_paginated(TEST_USER_ID, TEST_WORKSPACE_ID)

    assert result["total"] == 2
    assert result["page"] == 1
    assert result["pages"] == 1
    assert len(result["items"]) == 2
    assert result["items"][0]["memory_id"] == "mem_001"
    assert result["items"][0]["entities"][0]["name"] == "Alice"
    assert result["items"][1]["entities"] == []


async def test_get_memories_paginated_with_filters():
    """Pagination and memory_type filter are passed correctly."""
    from src.services.knowledge_service import KnowledgeService

    mem1 = _make_memory(memory_id="mem_003", memory_type="preference")

    db = _mock_db_execute(
        [
            1,  # total count
            [mem1],  # memories
            [],  # entity names (none needed)
        ]
    )

    graph_engine = _make_graph_engine()
    svc = KnowledgeService(make_mock_settings(), db, graph_engine)
    result = await svc.get_memories_paginated(
        TEST_USER_ID,
        TEST_WORKSPACE_ID,
        memory_type="preference",
        page=1,
        limit=10,
    )

    assert result["total"] == 1
    assert result["page"] == 1
    assert result["pages"] == 1
    assert len(result["items"]) == 1
    assert result["items"][0]["memory_type"] == "preference"


async def test_get_memories_paginated_multi_page():
    """Correctly computes pages for multi-page results."""
    from src.services.knowledge_service import KnowledgeService

    mem = _make_memory(memory_id="mem_010")

    db = _mock_db_execute(
        [
            55,  # total count
            [mem],  # single item on page 3
            [],  # entity names
        ]
    )

    graph_engine = _make_graph_engine()
    svc = KnowledgeService(make_mock_settings(), db, graph_engine)
    result = await svc.get_memories_paginated(
        TEST_USER_ID,
        TEST_WORKSPACE_ID,
        page=3,
        limit=20,
    )

    assert result["total"] == 55
    assert result["page"] == 3
    assert result["pages"] == 3  # ceil(55/20) = 3


async def test_get_memories_paginated_clamps_params():
    """Page and limit are clamped to valid ranges."""
    from src.services.knowledge_service import KnowledgeService

    db = _mock_db_execute([0, [], []])

    graph_engine = _make_graph_engine()
    svc = KnowledgeService(make_mock_settings(), db, graph_engine)
    result = await svc.get_memories_paginated(
        TEST_USER_ID,
        TEST_WORKSPACE_ID,
        page=-1,
        limit=500,
    )

    assert result["page"] == 1  # clamped from -1
    assert result["pages"] == 1  # min 1


# ── Tests: get_memory_detail ────────────────────────────────────────────


async def test_get_memory_detail_found():
    """Returns full detail with linked entities and provenance events."""
    from src.services.knowledge_service import KnowledgeService

    mem = _make_memory(
        memory_id="mem_detail_01",
        entity_ids=["ent_001", "ent_002"],
        source_event_ids=["evt_001", "evt_002"],
    )

    ent1 = _make_entity(entity_id="ent_001", canonical_name="Alice")
    ent2 = _make_entity(entity_id="ent_002", canonical_name="Acme")
    evt1 = _make_event(event_id="evt_001", title="Email from Alice")
    evt2 = _make_event(event_id="evt_002", title="Calendar invite")

    # DB calls: memory, linked entities, provenance events
    db = _mock_db_execute(
        [
            mem,  # memory
            [ent1, ent2],  # linked entities
            [evt1, evt2],  # provenance events
        ]
    )

    graph_engine = _make_graph_engine()
    svc = KnowledgeService(make_mock_settings(), db, graph_engine)
    result = await svc.get_memory_detail("mem_detail_01", TEST_USER_ID, TEST_WORKSPACE_ID)

    assert result is not None
    assert result["memory_id"] == "mem_detail_01"
    assert result["fact_text"] == "Alice is CFO at Acme Corp"
    assert len(result["linked_entities"]) == 2
    assert result["linked_entities"][0]["canonical_name"] == "Alice"
    assert len(result["provenance_events"]) == 2
    assert result["provenance_events"][0]["title"] == "Email from Alice"
    assert result["entity_ids"] == ["ent_001", "ent_002"]
    assert result["source_event_ids"] == ["evt_001", "evt_002"]


async def test_get_memory_detail_not_found():
    """Returns None when memory not found."""
    from src.services.knowledge_service import KnowledgeService

    db = _mock_db_execute([None])

    graph_engine = _make_graph_engine()
    svc = KnowledgeService(make_mock_settings(), db, graph_engine)
    result = await svc.get_memory_detail("mem_nonexistent", TEST_USER_ID, TEST_WORKSPACE_ID)

    assert result is None


async def test_get_memory_detail_no_entities_no_events():
    """Returns detail with empty linked_entities and provenance_events."""
    from src.services.knowledge_service import KnowledgeService

    mem = _make_memory(
        memory_id="mem_bare",
        entity_ids=None,
        source_event_ids=None,
    )

    # DB calls: memory only (no entity or event queries needed)
    db = _mock_db_execute([mem])

    graph_engine = _make_graph_engine()
    svc = KnowledgeService(make_mock_settings(), db, graph_engine)
    result = await svc.get_memory_detail("mem_bare", TEST_USER_ID, TEST_WORKSPACE_ID)

    assert result is not None
    assert result["linked_entities"] == []
    assert result["provenance_events"] == []
    assert result["entity_ids"] == []
    assert result["source_event_ids"] == []


# ── Tests: get_stats ────────────────────────────────────────────────────


async def test_get_stats_returns_all_sections():
    """Returns all expected stat sections with correct structure."""
    from src.services.knowledge_service import KnowledgeService

    central = [
        {"entity_id": "ent_001", "name": "Alice", "entity_type": "person", "degree": 10},
    ]
    communities = [
        {
            "seed_entity_id": "ent_001",
            "seed_name": "Alice",
            "seed_type": "person",
            "community_members": ["ent_002"],
            "community_size": 2,
        }
    ]
    stale = [
        {
            "relation_id": "rel_001",
            "from_entity_id": "ent_001",
            "from_name": "Alice",
            "to_entity_id": "ent_002",
            "to_name": "Bob",
            "relation_type": "works_with",
        }
    ]
    graph_engine = _make_graph_engine(
        central_entities=central,
        communities=communities,
        stale_relationships=stale,
    )

    # Mock rows for entity/memory count group-by queries
    entity_type_row = MagicMock()
    entity_type_row.__getitem__ = lambda s, i: ("person", 5)[i]
    memory_type_row = MagicMock()
    memory_type_row.__getitem__ = lambda s, i: ("semantic", 12)[i]
    growth_entity_row = MagicMock()
    growth_entity_row.__getitem__ = lambda s, i: ("2026-03-28", 3)[i]
    growth_memory_row = MagicMock()
    growth_memory_row.__getitem__ = lambda s, i: ("2026-03-28", 7)[i]

    # DB calls: entity_counts_by_type, memory_counts_by_type,
    #           entity_weekly_delta, memory_weekly_delta, avg_confidence,
    #           total_entities, total_relationships,
    #           growth_entity, growth_memory
    db = _mock_db_execute(
        [
            [entity_type_row],  # entity counts by type
            [memory_type_row],  # memory counts by type
            8,  # entity weekly delta
            20,  # memory weekly delta
            0.85,  # avg confidence
            42,  # total entities
            15,  # total relationships
            [growth_entity_row],  # growth entities
            [growth_memory_row],  # growth memories
        ]
    )

    svc = KnowledgeService(make_mock_settings(), db, graph_engine)
    result = await svc.get_stats(TEST_USER_ID, TEST_WORKSPACE_ID)

    assert "entity_counts_by_type" in result
    assert "memory_counts_by_type" in result
    assert result["entity_weekly_delta"] == 8
    assert result["memory_weekly_delta"] == 20
    assert result["avg_confidence"] == 0.85
    assert result["total_entities"] == 42
    assert result["total_relationships"] == 15
    assert result["central_entities"] == central
    assert result["communities"] == communities
    assert result["stale_relationships"] == stale
    assert "growth_by_day" in result

    graph_engine.find_central_entities.assert_awaited_once_with(user_id=TEST_USER_ID, limit=5)
    graph_engine.detect_communities.assert_awaited_once_with(user_id=TEST_USER_ID)
    graph_engine.get_stale_relationships.assert_awaited_once_with(user_id=TEST_USER_ID, days=14)


async def test_get_stats_empty():
    """Returns zeroed stats when no data exists."""
    from src.services.knowledge_service import KnowledgeService

    graph_engine = _make_graph_engine()

    # All counts return 0 or empty lists
    db = _mock_db_execute(
        [
            [],  # entity counts by type (empty)
            [],  # memory counts by type (empty)
            0,  # entity weekly delta
            0,  # memory weekly delta
            None,  # avg confidence (no active memories)
            0,  # total entities
            0,  # total relationships
            [],  # growth entities
            [],  # growth memories
        ]
    )

    svc = KnowledgeService(make_mock_settings(), db, graph_engine)
    result = await svc.get_stats(TEST_USER_ID, TEST_WORKSPACE_ID)

    assert result["entity_counts_by_type"] == {}
    assert result["memory_counts_by_type"] == {}
    assert result["entity_weekly_delta"] == 0
    assert result["memory_weekly_delta"] == 0
    assert result["avg_confidence"] == 0.0
    assert result["total_entities"] == 0
    assert result["total_relationships"] == 0
    assert result["central_entities"] == []
    assert result["communities"] == []
    assert result["stale_relationships"] == []
    assert result["growth_by_day"] == []


# ── Tests: edge cases ───────────────────────────────────────────────────


async def test_memory_to_dict_structure():
    """Verify _memory_to_dict produces correct structure."""
    from src.services.knowledge_service import KnowledgeService

    graph_engine = _make_graph_engine()
    db = AsyncMock()
    svc = KnowledgeService(make_mock_settings(), db, graph_engine)

    mem = _make_memory(
        memory_id="mem_struct",
        memory_type="episodic",
        scope="planning",
        fact_text="Meeting at 3pm",
        confidence=0.95,
        stability_score=0.7,
        entity_ids=["ent_001"],
    )
    name_map = {"ent_001": "Alice"}

    result = svc._memory_to_dict(mem, name_map)

    assert result["memory_id"] == "mem_struct"
    assert result["memory_type"] == "episodic"
    assert result["scope"] == "planning"
    assert result["fact_text"] == "Meeting at 3pm"
    assert result["confidence"] == 0.95
    assert result["stability_score"] == 0.7
    assert result["entities"] == [{"entity_id": "ent_001", "name": "Alice"}]
    assert "created_at" in result


async def test_resolve_memory_sort_defaults():
    """Sort falls back to created_at for unknown sort_by."""
    from src.models.memory import Memory
    from src.services.knowledge_service import KnowledgeService

    graph_engine = _make_graph_engine()
    db = AsyncMock()
    svc = KnowledgeService(make_mock_settings(), db, graph_engine)

    assert svc._resolve_memory_sort("created_at") is Memory.created_at
    assert svc._resolve_memory_sort("confidence") is Memory.confidence
    assert svc._resolve_memory_sort("unknown_field") is Memory.created_at


async def test_get_stats_communities_limited_to_4():
    """Communities result is capped at 4 even when GraphEngine returns more."""
    from src.services.knowledge_service import KnowledgeService

    communities_5 = [
        {"seed_entity_id": f"ent_{i}", "seed_name": f"Node{i}", "community_size": i}
        for i in range(5)
    ]
    graph_engine = _make_graph_engine(communities=communities_5)

    entity_type_row = MagicMock()
    entity_type_row.__getitem__ = lambda s, i: ("person", 1)[i]

    db = _mock_db_execute(
        [
            [entity_type_row],  # entity counts by type
            [],  # memory counts by type
            0,  # entity weekly delta
            0,  # memory weekly delta
            0.5,  # avg confidence
            10,  # total entities
            5,  # total relationships
            [],  # growth entities
            [],  # growth memories
        ]
    )

    svc = KnowledgeService(make_mock_settings(), db, graph_engine)
    result = await svc.get_stats(TEST_USER_ID, TEST_WORKSPACE_ID)

    assert len(result["communities"]) == 4
    assert result["communities"] == communities_5[:4]


async def test_close_delegates_to_graph_engine():
    """close() calls graph_engine.close()."""
    from src.services.knowledge_service import KnowledgeService

    graph_engine = _make_graph_engine()
    db = AsyncMock()
    svc = KnowledgeService(make_mock_settings(), db, graph_engine)

    await svc.close()

    graph_engine.close.assert_awaited_once()


async def test_async_context_manager():
    """KnowledgeService can be used as an async context manager."""
    from src.services.knowledge_service import KnowledgeService

    graph_engine = _make_graph_engine()
    db = AsyncMock()

    async with KnowledgeService(make_mock_settings(), db, graph_engine) as svc:
        assert svc is not None
        assert isinstance(svc, KnowledgeService)

    graph_engine.close.assert_awaited_once()
