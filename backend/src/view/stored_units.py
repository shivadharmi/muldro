"""Units for the feed, projected from stored `normalized_events` rows.

`perception.units_from_events` groups ONE POLL's raw events. The feed needs
the same grouping over the rows ingest already wrote, for a window rather
than a poll — which is what makes the feed a pure function of domain rows
rather than something that exists only while a poll
is running. `frame_for_event` reads both shapes, so this is a query plus a
call, not a second projection.

It also returns the `events_by_key` map, because `ranking.build_features`
needs it and it is free here: the same rows, grouped the same way. Building
it separately would mean a second query and a second chance to disagree.
"""

from __future__ import annotations

import logging
from collections.abc import Sequence
from datetime import datetime, timedelta
from typing import Any

from sqlalchemy import select

from src.models.events import NormalizedEvent
from src.view.contracts import Unit
from src.view.perception import group_events_by_key, units_from_events

logger = logging.getLogger(__name__)

__all__ = [
    "FEED_HORIZON_DAYS",
    "FEED_WINDOW_DAYS",
    "MAX_FEED_EVENTS",
    "stored_perception_units",
]

# How far back the feed looks. NOT a TTL and not an expiry: the row is still
# there, and widening this number brings it straight back. A window is an
# attention bound; an expiry is a deletion.
FEED_WINDOW_DAYS = 14

# How far FORWARD it looks. The window used to bound only the past, which is
# the right shape for a source whose events have already happened and the wrong
# one for a calendar: `timeMin=now` with no `timeMax` returns every future
# occurrence, so a weekly meeting put one card on the workspace for each of the
# next several months. An attention bound has two sides.
FEED_HORIZON_DAYS = 7

# Rows read per feed build. Grouping collapses these, so the unit count is far
# smaller — an inbox is many rows over few threads.
MAX_FEED_EVENTS = 500


async def stored_perception_units(
    db: Any,
    *,
    workspace_id: str,
    user_id: str,
    now: datetime,
) -> tuple[list[Unit], dict[str, Sequence[Any]]]:
    """Return (units, events_by_key). Never raises.

    A read failure costs the perception family and nothing else — the runs,
    the briefing and the prepared queue still render. That is the same rule
    `units_from_events` and `build_features` already follow: one bad thing
    must never cost the whole feed.
    """
    since = now - timedelta(days=FEED_WINDOW_DAYS)
    until = now + timedelta(days=FEED_HORIZON_DAYS)
    try:
        result = await db.execute(
            select(NormalizedEvent)
            .where(
                NormalizedEvent.workspace_id == workspace_id,
                NormalizedEvent.user_id == user_id,
                NormalizedEvent.occurred_at >= since,
                NormalizedEvent.occurred_at <= until,
            )
            .order_by(NormalizedEvent.occurred_at.desc())
            .limit(MAX_FEED_EVENTS)
        )
        rows = list(result.scalars().all())
    except Exception as exc:  # noqa: BLE001 - one family must not cost the feed
        logger.warning("feed_perception_read_failed workspace=%s error=%s", workspace_id, exc)
        return [], {}

    units = units_from_events(rows)
    by_key: dict[str, Sequence[Any]] = {g.key: g.events for g in group_events_by_key(rows)}
    return units, by_key
