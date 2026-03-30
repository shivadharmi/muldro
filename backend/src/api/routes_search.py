"""Search endpoint — unified TriSearch across Qdrant, Postgres FTS, and Neo4j."""

from fastapi import APIRouter, Depends
from pydantic import BaseModel
from sqlalchemy.ext.asyncio import AsyncSession

from src.api.deps import get_current_user_id, get_current_workspace_id, get_session
from src.api.schemas import SearchResponse, SearchResult
from src.config.settings import Settings, get_settings

router = APIRouter()


class SearchRequest(BaseModel):
    query: str
    types: list[str] | None = None
    limit: int = 20


@router.post("/v1/search", response_model=SearchResponse)
async def search(
    req: SearchRequest,
    user_id: str = Depends(get_current_user_id),
    workspace_id: str = Depends(get_current_workspace_id),
    db: AsyncSession = Depends(get_session),
    settings: Settings = Depends(get_settings),
):
    """Unified TriSearch: Qdrant (semantic) + Postgres FTS (keyword) + Neo4j (graph)."""
    from src.services.embedding_service import EmbeddingService
    from src.services.graph_engine import GraphEngine
    from src.services.reranker_service import RerankerService
    from src.services.tri_search import TriSearchService
    from src.services.vector_store import VectorStore

    vector_store = VectorStore(settings) if settings.qdrant_url else None
    graph_engine = GraphEngine(settings) if settings.neo4j_url else None
    reranker = RerankerService(settings) if settings.reranker_enabled else None
    embedder = EmbeddingService(settings)

    tri = TriSearchService(
        settings=settings,
        vector_store=vector_store,
        graph_engine=graph_engine,
        reranker=reranker,
        embedder=embedder,
    )

    raw_results = await tri.search(
        query=req.query,
        user_id=user_id,
        workspace_id=workspace_id,
        db=db,
        types=req.types,
        limit=req.limit,
    )

    results = [
        SearchResult(
            type=r.get("result_type", "unknown"),
            id=r.get("id", ""),
            title=r.get("title", ""),
            summary=r.get("text", ""),
            score=r.get("final_score") or r.get("score"),
            source_db=r.get("source_db"),
            why_matched=r.get("why_matched"),
        )
        for r in raw_results
    ]
    return SearchResponse(results=results)
