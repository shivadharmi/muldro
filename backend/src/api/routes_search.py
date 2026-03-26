"""Search endpoints — hybrid ES (BM25) + Qdrant (semantic) + unified search."""

from fastapi import APIRouter, Depends
from pydantic import BaseModel
from sqlalchemy.ext.asyncio import AsyncSession

from src.api.deps import get_current_user_id, get_current_workspace_id, get_session
from src.api.schemas import SearchRequest, SearchResponse, SearchResult
from src.config.settings import Settings, get_settings
from src.services.search_service import SearchService
from src.services.vector_store import VectorStore

router = APIRouter()

SCOPE_MAP = {
    "all": ["events", "entities", "memories"],
    "memory": ["memories"],
    "entities": ["entities"],
    "events": ["events"],
}


@router.post("/v1/search", response_model=SearchResponse)
async def search(
    req: SearchRequest,
    user_id: str = Depends(get_current_user_id),
    settings: Settings = Depends(get_settings),
):
    """Hybrid search across events, entities, and memories via ES + Qdrant + RRF."""
    vector_store = VectorStore(settings) if settings.qdrant_url else None
    search_svc = SearchService(settings, vector_store=vector_store)

    scopes = SCOPE_MAP.get(req.scope or "all", ["events", "entities", "memories"])
    hybrid_results = await search_svc.search(user_id, req.query, scopes=scopes, limit=20)

    # Map index/collection names to result type
    index_type_map = {
        "jarvis-events": "event",
        "jarvis-entities": "entity",
        "jarvis-memories": "memory",
        "events": "event",
        "entities": "entity",
        "memories": "memory",
    }

    results: list[SearchResult] = []
    for item in hybrid_results:
        source = item.get("source") or item.get("payload") or {}
        index = item.get("index", item.get("collection", ""))
        result_type = index_type_map.get(index, "event")
        title = (
            source.get("title") or source.get("canonical_name") or source.get("fact_text", "")[:80]
        )
        summary = source.get("summary") or source.get("fact_text") or source.get("canonical_name")
        results.append(
            SearchResult(
                type=result_type,
                id=item.get("id", ""),
                title=title,
                summary=summary,
                score=item.get("rrf_score") or item.get("score"),
            )
        )

    return SearchResponse(results=results)


class UnifiedSearchRequest(BaseModel):
    query: str
    types: list[str] | None = None
    limit: int = 20


@router.post("/v1/search/unified")
async def unified_search(
    req: UnifiedSearchRequest,
    workspace_id: str = Depends(get_current_workspace_id),
    db: AsyncSession = Depends(get_session),
):
    """Unified search across conversations, briefings, approvals, entities, memories, goals."""
    from src.services.unified_search import UnifiedSearchService

    svc = UnifiedSearchService(db, workspace_id)
    return await svc.search(req.query, types=req.types, limit=req.limit)
