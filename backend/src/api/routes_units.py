"""The view layer's read surface — one endpoint, one typed object.

`GET /v1/workspace/surfaces` returned a push model whose `preview` and
`detail_config` were annotated `Any`, so nothing crossing the wire had a shape
a client could rely on. A `Unit` is frozen and typed all the way down, and it
is the ONLY object the view layer puts on the wire.
"""

import logging
from datetime import datetime, timezone

from fastapi import APIRouter, Depends, HTTPException
from pydantic import BaseModel
from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from src.api.deps import get_current_user_id, get_current_workspace_id, get_session
from src.models.events import NormalizedEvent
from src.services.engagement_service import EngagementService
from src.services.unit_dismissals import OWN_SOURCE, dismiss
from src.view.contracts import Unit
from src.view.feed import assemble_feed

router = APIRouter()
logger = logging.getLogger(__name__)


class UnitFeedResponse(BaseModel):
    units: list[Unit]
    count: int
    # Index into `units` where attention stops: everything from here on is
    # collapsed behind one row. NOT a filter — the tail is still on the wire,
    # still ranked, still reachable. `count == fold_after` means nothing folds.
    fold_after: int


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
    expiry. The Unit exists exactly as long as the row it projects.
    """
    feed = await assemble_feed(
        db, workspace_id=workspace_id, user_id=user_id, now=datetime.now(timezone.utc)
    )
    return UnitFeedResponse(units=feed.units, count=len(feed.units), fold_after=feed.fold_after)


class DismissRequest(BaseModel):
    frame_key: str


class DismissResponse(BaseModel):
    status: str


@router.post("/v1/workspace/units/dismiss", response_model=DismissResponse)
async def dismiss_unit(
    body: DismissRequest,
    user_id: str = Depends(get_current_user_id),
    workspace_id: str = Depends(get_current_workspace_id),
    db: AsyncSession = Depends(get_session),
) -> DismissResponse:
    """Hide this card, and teach the ranker less of its kind.

    TWO writes, because a dismissal is two facts and neither implies the other.
    "Not this one" is `unit_dismissals`, read back by the feed — without it the
    card returned on the next poll, since the feed is a pure projection of live
    rows and nothing recorded that the founder had cleared one. "Less of this
    kind" is `engagement_history`, read by the ranker. Dropping either would
    leave a write with no reader or a card that will not go away.

    `engagement_history` is keyed on `(signal_source, signal_category)`, and
    `ranking/build.py` reads it as `(frame.source, event.event_type)` — e.g.
    `("gmail", "email_received")`. The insight route this replaces recorded
    `("gmail", "perception_gmail")`, because `perception_runner` synthesised
    that event type for its per-poll PerceptionSignal. The two never met, so
    no dismissal the founder has ever made reached the ranker. This writes the
    key the ranker reads.

    Demotion only; nothing here can promote. Promotion by engagement would be
    self-sealing — rank drives visibility and visibility drives engagement —
    while a thing had to be seen before it could be dismissed.
    """
    parsed = parse_frame_key(body.frame_key)
    if parsed is None:
        raise HTTPException(status_code=400, detail="Malformed unit key.")
    source, entity_type, entity_id = parsed
    if source == OWN_SOURCE:
        raise HTTPException(
            status_code=400, detail="This card is muldro's own work and cannot be dismissed."
        )

    result = await db.execute(
        select(NormalizedEvent)
        .where(
            NormalizedEvent.workspace_id == workspace_id,
            NormalizedEvent.user_id == user_id,
            NormalizedEvent.source == source,
            NormalizedEvent.entity_type == entity_type,
            NormalizedEvent.entity_id == entity_id,
        )
        .order_by(NormalizedEvent.occurred_at.desc())
        .limit(1)
    )
    event = result.scalars().first()
    if event is None:
        # Deliberately does not distinguish "not yours" from "not found": a
        # caller who can tell the two apart can probe for the existence of
        # another workspace's events one key at a time.
        raise HTTPException(status_code=404, detail="Unit not found.")

    await dismiss(
        db,
        workspace_id=workspace_id,
        user_id=user_id,
        frame_key=body.frame_key,
        now=datetime.now(timezone.utc),
    )
    await EngagementService(db, workspace_id).record_engagement(
        source, event.event_type, "dismissed"
    )
    await db.commit()
    return DismissResponse(status="dismissed")
