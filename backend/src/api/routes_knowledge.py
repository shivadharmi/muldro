"""Knowledge page endpoints — graph, memories, stats."""

from fastapi import APIRouter, Depends, HTTPException, Query
from pydantic import BaseModel
from sqlalchemy.ext.asyncio import AsyncSession

from src.api.deps import get_current_user_id, get_current_workspace_id, get_session
from src.config.settings import Settings, get_settings
from src.services.graph_engine import GraphEngine
from src.services.knowledge_service import KnowledgeService

router = APIRouter()


# ── Response models ─────────────────────────────────────────────────────


class GraphResponse(BaseModel):
    nodes: list[dict]
    edges: list[dict]
    stats: dict


class MemoryListResponse(BaseModel):
    items: list[dict]
    total: int
    page: int
    pages: int


class MemoryDetailResponse(BaseModel):
    memory_id: str
    memory_type: str
    scope: str | None = None
    fact_text: str
    confidence: float
    stability_score: float | None = None
    status: str
    refresh_count: int | None = None
    last_accessed_at: str | None = None
    created_at: str | None = None
    updated_at: str | None = None
    entity_ids: list[str] = []
    source_event_ids: list[str] = []
    linked_entities: list[dict] = []
    provenance_events: list[dict] = []


class StatsResponse(BaseModel):
    entity_counts_by_type: dict[str, int]
    memory_counts_by_type: dict[str, int]
    entity_weekly_delta: int
    memory_weekly_delta: int
    avg_confidence: float
    total_entities: int
    total_relationships: int
    central_entities: list[dict]
    communities: list[dict]
    stale_relationships: list[dict]
    growth_by_day: list[dict]


# ── Routes ──────────────────────────────────────────────────────────────


@router.get("/v1/knowledge/graph", response_model=GraphResponse)
async def knowledge_graph(
    user_id: str = Depends(get_current_user_id),
    workspace_id: str = Depends(get_current_workspace_id),
    db: AsyncSession = Depends(get_session),
    settings: Settings = Depends(get_settings),
):
    """Return seed graph nodes, edges, and summary stats for the knowledge page."""
    graph = GraphEngine(settings)
    svc = KnowledgeService(settings=settings, db=db, graph_engine=graph)
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
    """List memories with optional filters and pagination."""
    graph = GraphEngine(settings)
    svc = KnowledgeService(settings=settings, db=db, graph_engine=graph)
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
    """Return full memory detail with linked entities and provenance events."""
    graph = GraphEngine(settings)
    svc = KnowledgeService(settings=settings, db=db, graph_engine=graph)
    try:
        result = await svc.get_memory_detail(memory_id, user_id, workspace_id)
    finally:
        await svc.close()

    if result is None:
        raise HTTPException(status_code=404, detail=f"Memory {memory_id} not found")

    return result


@router.get("/v1/knowledge/stats", response_model=StatsResponse)
async def knowledge_stats(
    user_id: str = Depends(get_current_user_id),
    workspace_id: str = Depends(get_current_workspace_id),
    db: AsyncSession = Depends(get_session),
    settings: Settings = Depends(get_settings),
):
    """Return aggregated knowledge stats — entity/memory counts, deltas, communities."""
    graph = GraphEngine(settings)
    svc = KnowledgeService(settings=settings, db=db, graph_engine=graph)
    try:
        return await svc.get_stats(user_id, workspace_id)
    finally:
        await svc.close()
