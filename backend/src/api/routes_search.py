"""Search endpoints — search memory, entities, and events."""

from fastapi import APIRouter, Depends
from sqlalchemy.ext.asyncio import AsyncSession

from src.api.deps import get_current_user, get_session
from src.api.schemas import SearchRequest, SearchResponse, SearchResult
from src.config.settings import Settings, get_settings
from src.services.memory_service import MemoryService
from src.services.world_model import WorldModel

router = APIRouter()


@router.post("/v1/search", response_model=SearchResponse)
async def search(
    req: SearchRequest,
    user_id: str = Depends(get_current_user),
    db: AsyncSession = Depends(get_session),
    settings: Settings = Depends(get_settings),
):
    """Search Jarvis knowledge base: memory, entities, events."""
    results: list[SearchResult] = []
    scope = req.scope or "all"

    if scope in ("all", "memory"):
        memory_service = MemoryService(settings=settings, db=db)
        memories = await memory_service.retrieve(user_id, req.query, max_results=10)
        for m in memories:
            results.append(
                SearchResult(
                    type="memory",
                    id=m["memory_id"],
                    title=m["fact_text"][:80],
                    summary=m["fact_text"],
                    score=m.get("similarity") or m.get("confidence"),
                )
            )

    if scope in ("all", "entities"):
        world_model = WorldModel(settings=settings, db=db)
        entities = await world_model.find_entity(user_id, req.query)
        for e in entities[:10]:
            results.append(
                SearchResult(
                    type="entity",
                    id=e["entity_id"],
                    title=e["canonical_name"],
                    summary=f"{e['entity_type']}: {e['canonical_name']}",
                )
            )

    return SearchResponse(results=results)
