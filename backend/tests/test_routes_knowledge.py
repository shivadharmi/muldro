"""Tests for /v1/knowledge/ API routes."""

import pytest


@pytest.mark.asyncio
async def test_knowledge_routes_registered():
    """Verify all 4 knowledge endpoints are registered on the router."""
    from src.api.routes_knowledge import router

    routes = [r.path for r in router.routes]
    assert "/v1/knowledge/graph" in routes
    assert "/v1/knowledge/memories" in routes
    assert "/v1/knowledge/memories/{memory_id}" in routes
    assert "/v1/knowledge/stats" in routes


@pytest.mark.asyncio
async def test_knowledge_routes_have_correct_methods():
    """Verify all knowledge routes use GET method."""
    from src.api.routes_knowledge import router

    method_map = {r.path: r.methods for r in router.routes}
    assert method_map["/v1/knowledge/graph"] == {"GET"}
    assert method_map["/v1/knowledge/memories"] == {"GET"}
    assert method_map["/v1/knowledge/memories/{memory_id}"] == {"GET"}
    assert method_map["/v1/knowledge/stats"] == {"GET"}


@pytest.mark.asyncio
async def test_knowledge_response_models_defined():
    """Verify Pydantic response models can be imported and instantiated."""
    from src.api.routes_knowledge import (
        GraphResponse,
        GraphStatsResponse,
        MemoryDetailResponse,
        MemoryListResponse,
        ProvenanceResponse,
        StatsResponse,
        WeeklyDeltaResponse,
    )

    graph = GraphResponse(
        nodes=[],
        edges=[],
        stats=GraphStatsResponse(total_entities=0, total_relationships=0),
    )
    assert graph.nodes == []
    assert graph.edges == []
    assert graph.stats.total_entities == 0

    mem_list = MemoryListResponse(items=[], total=0, page=1, pages=1)
    assert mem_list.total == 0

    detail = MemoryDetailResponse(
        memory_id="mem_test",
        memory_type="fact",
        fact_text="test fact",
        confidence=0.9,
        status="active",
    )
    assert detail.memory_id == "mem_test"
    assert detail.linked_entities == []
    assert detail.provenance == ProvenanceResponse()
    assert detail.provenance.source_event_ids == []
    assert detail.provenance.source_description is None

    stats = StatsResponse(
        entity_counts_by_type=[],
        memory_counts_by_type=[],
        weekly_delta=WeeklyDeltaResponse(entities=0, relationships=0, memories=0),
        total_memories=0,
        avg_confidence=0.0,
        total_entities=0,
        total_relationships=0,
        central_entities=[],
        communities=[],
        stale_relationships=[],
        growth_by_day=[],
    )
    assert stats.total_entities == 0
    assert stats.total_memories == 0
    assert stats.weekly_delta.entities == 0


@pytest.mark.asyncio
async def test_knowledge_router_included_in_app():
    """Verify the knowledge router is registered in the main FastAPI app."""
    from src.api.app import create_app

    app = create_app()
    all_paths = [r.path for r in app.routes]
    assert "/v1/knowledge/graph" in all_paths
    assert "/v1/knowledge/stats" in all_paths
