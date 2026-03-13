"""Search endpoints — search memory, entities, and events."""

from fastapi import APIRouter, Depends

from src.api.deps import get_current_user
from src.api.schemas import SearchRequest, SearchResponse

router = APIRouter()


@router.post("/v1/search", response_model=SearchResponse)
async def search(
    req: SearchRequest,
    user_id: str = Depends(get_current_user),
):
    """Search Jarvis knowledge base: memory, entities, events."""
    # TODO: Wire to memory + entity search services
    return SearchResponse(results=[])
