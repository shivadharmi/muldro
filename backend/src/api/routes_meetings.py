"""Meeting prep endpoints."""

from fastapi import APIRouter, Depends

from src.api.deps import get_current_user
from src.api.schemas import MeetingPrepRequest, MeetingPrepResponse

router = APIRouter()


@router.post("/v1/meetings/prep", response_model=MeetingPrepResponse)
async def meeting_prep(
    req: MeetingPrepRequest,
    user_id: str = Depends(get_current_user),
):
    """Generate meeting preparation for an upcoming event."""
    # TODO: Wire to meeting prep service
    return MeetingPrepResponse(
        meeting_id=req.meeting_id or "none",
        title="Meeting prep service not yet connected.",
    )
