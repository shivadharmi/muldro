"""Graph query endpoints — Neo4j entity graph traversals."""

from fastapi import APIRouter, Depends, Query
from pydantic import BaseModel

from src.api.deps import get_current_user_id
from src.config.settings import Settings, get_settings
from src.services.graph_engine import GraphEngine

router = APIRouter()


class PathRequest(BaseModel):
    from_entity_id: str
    to_entity_id: str
    max_depth: int = 4


@router.get("/v1/graph/central-entities")
async def central_entities(
    user_id: str = Depends(get_current_user_id),
    settings: Settings = Depends(get_settings),
    entity_type: str | None = Query(None),
    limit: int = Query(10, le=50),
):
    """Find entities with highest degree centrality."""
    graph = GraphEngine(settings)
    try:
        return await graph.find_central_entities(user_id, entity_type=entity_type, limit=limit)
    finally:
        await graph.close()


@router.get("/v1/graph/{entity_id}/traverse")
async def traverse(
    entity_id: str,
    user_id: str = Depends(get_current_user_id),
    settings: Settings = Depends(get_settings),
    depth: int = Query(2, le=5),
    rel_type: str | None = Query(None),
):
    """Multi-hop traversal from an entity."""
    graph = GraphEngine(settings)
    try:
        rel_types = [rel_type] if rel_type else None
        return await graph.traverse(entity_id, user_id, relation_types=rel_types, depth=depth)
    finally:
        await graph.close()


@router.get("/v1/graph/{entity_id}/related-people")
async def related_people(
    entity_id: str,
    user_id: str = Depends(get_current_user_id),
    settings: Settings = Depends(get_settings),
):
    """People connected within 2 hops of an entity."""
    graph = GraphEngine(settings)
    try:
        return await graph.get_related_people(entity_id, user_id)
    finally:
        await graph.close()


@router.post("/v1/graph/path")
async def find_path(
    req: PathRequest,
    user_id: str = Depends(get_current_user_id),
    settings: Settings = Depends(get_settings),
):
    """Shortest path between two entities."""
    graph = GraphEngine(settings)
    try:
        return await graph.find_path(
            req.from_entity_id, req.to_entity_id, user_id, max_depth=req.max_depth
        )
    finally:
        await graph.close()


@router.get("/v1/graph/{entity_id}/subgraph")
async def subgraph(
    entity_id: str,
    user_id: str = Depends(get_current_user_id),
    settings: Settings = Depends(get_settings),
):
    """Full subgraph for an entity (3-hop)."""
    graph = GraphEngine(settings)
    try:
        return await graph.get_project_graph(entity_id, user_id)
    finally:
        await graph.close()
