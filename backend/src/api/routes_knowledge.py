"""Knowledge page endpoints — graph, memories, stats."""

from typing import Literal

from fastapi import APIRouter, Depends, HTTPException, Query
from pydantic import BaseModel
from sqlalchemy.ext.asyncio import AsyncSession

from src.api.deps import get_current_user_id, get_current_workspace_id, get_session
from src.config.settings import Settings, get_settings
from src.services.graph_engine import GraphEngine
from src.services.knowledge_service import KnowledgeService

router = APIRouter()


# ── Response models ─────────────────────────────────────────────────────


class GraphNodeResponse(BaseModel):
    entity_id: str
    entity_type: str
    canonical_name: str
    attributes: dict = {}
    importance_score: float | None = None
    confidence_score: float | None = None
    interaction_count: int = 0
    last_seen_at: str | None = None
    aliases: list[str] = []


class GraphStatsResponse(BaseModel):
    total_entities: int
    total_relationships: int


class GraphResponse(BaseModel):
    nodes: list[GraphNodeResponse]
    edges: list[dict]
    stats: GraphStatsResponse


class MemoryListItem(BaseModel):
    memory_id: str
    memory_type: str
    scope: str | None = None
    fact_text: str
    confidence: float
    stability_score: float | None = None
    refresh_count: int | None = None
    last_accessed_at: str | None = None
    created_at: str | None = None
    entity_ids: list[str] = []
    entity_names: list[str] = []


class MemoryListResponse(BaseModel):
    items: list[MemoryListItem]
    total: int
    page: int
    pages: int


class LinkedEntityResponse(BaseModel):
    entity_id: str
    entity_type: str
    canonical_name: str
    importance_score: float | None = None


class ProvenanceResponse(BaseModel):
    source_event_ids: list[str] = []
    source_description: str | None = None


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
    linked_entities: list[LinkedEntityResponse] = []
    provenance: ProvenanceResponse = ProvenanceResponse()


class KnowledgeCardResponse(BaseModel):
    """Design's memory-card shape — unified projection over entities + memories."""

    id: str
    kind: Literal["person", "project", "fact", "preference"]
    label: str
    desc: str | None = None
    sources: list[str] = []


class WeeklyDeltaResponse(BaseModel):
    entities: int
    relationships: int
    memories: int


class EntityCountByType(BaseModel):
    entity_type: str
    count: int


class MemoryCountByType(BaseModel):
    memory_type: str
    count: int


class StatsResponse(BaseModel):
    entity_counts_by_type: list[EntityCountByType]
    memory_counts_by_type: list[MemoryCountByType]
    weekly_delta: WeeklyDeltaResponse
    total_memories: int
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


@router.get("/v1/knowledge/cards", response_model=list[KnowledgeCardResponse])
async def knowledge_cards(
    user_id: str = Depends(get_current_user_id),
    workspace_id: str = Depends(get_current_workspace_id),
    db: AsyncSession = Depends(get_session),
    settings: Settings = Depends(get_settings),
    limit: int = Query(50, ge=1, le=100),
):
    """Unified knowledge-card feed (kind/label/desc/sources) from entities + memories."""
    graph = GraphEngine(settings)
    svc = KnowledgeService(settings=settings, db=db, graph_engine=graph)
    try:
        return await svc.get_knowledge_cards(user_id, workspace_id, limit=limit)
    finally:
        await svc.close()


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
