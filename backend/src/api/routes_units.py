"""The view layer's read surface — one endpoint, one typed object.

`GET /v1/workspace/surfaces` returned `WorkspaceSurfacePush`, whose `preview`
and `detail_config` are annotated `Any`, so nothing crossing the wire had a
shape a client could rely on. A `Unit` is frozen and typed all the way down,
and it is the ONLY object in the view layer (spec §2.2).
"""

import logging
from datetime import datetime, timezone

from fastapi import APIRouter, Depends
from pydantic import BaseModel
from sqlalchemy.ext.asyncio import AsyncSession

from src.api.deps import get_current_user_id, get_current_workspace_id, get_session
from src.view.contracts import Unit
from src.view.feed import assemble_feed

router = APIRouter()
logger = logging.getLogger(__name__)


class UnitFeedResponse(BaseModel):
    units: list[Unit]
    count: int


def parse_frame_key(key: str) -> tuple[str, str, str] | None:
    """Split `source:entity_type:entity_id`, or None when it is not one.

    Splits on the FIRST TWO colons only. A connector's `entity_id` is opaque
    and may contain colons (a Google recurring-instance id does); `source` and
    `entity_type` are code-chosen and never do.
    """
    if not isinstance(key, str):
        return None
    parts = key.split(":", 2)
    if len(parts) != 3 or not all(parts):
        return None
    return parts[0], parts[1], parts[2]


@router.get("/v1/workspace/units", response_model=UnitFeedResponse)
async def get_workspace_units(
    user_id: str = Depends(get_current_user_id),
    workspace_id: str = Depends(get_current_workspace_id),
    db: AsyncSession = Depends(get_session),
) -> UnitFeedResponse:
    """Every Unit the workspace shows, in rank order.

    A pure projection of live domain rows — no cache, no stored feed, and no
    expiry (spec §10 invariants 1 and 9). The Unit exists exactly as long as
    the row it projects.
    """
    units = await assemble_feed(
        db, workspace_id=workspace_id, user_id=user_id, now=datetime.now(timezone.utc)
    )
    return UnitFeedResponse(units=units, count=len(units))
