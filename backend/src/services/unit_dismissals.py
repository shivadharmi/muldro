"""Read and write the founder's per-thing dismissals, and decide what they hide.

A dismissal is two facts, and this module owns one of them. `engagement_history`
already carried "less of this kind" to the ranker; nothing carried "not this
one" to the feed, so the card came back on the next poll. Neither replaces the
other: one is evidence about a CATEGORY, the other is an instruction about a
THING.

`is_hidden` is pure and takes no session, so the rule that decides what the
founder sees can be read and tested without a database in the way.
"""

from __future__ import annotations

from collections.abc import Iterable, Mapping, Sequence
from datetime import datetime
from typing import Any

from sqlalchemy import func, select
from sqlalchemy.dialects.postgresql import insert as pg_insert
from sqlalchemy.ext.asyncio import AsyncSession

from src.models.ids import generate_id
from src.models.unit_dismissal import UnitDismissal
from src.view.frame import ensure_aware_utc

# Units whose source is muldro's own work — runs, briefings, the review queue,
# connector health. They are not perception signals and they are the founder's
# only route to work muldro is holding for them, so hiding one loses it.
OWN_SOURCE = "muldro"


async def dismiss(
    db: AsyncSession,
    *,
    workspace_id: str,
    user_id: str,
    frame_key: str,
    now: datetime,
) -> None:
    """Record that this person has seen and cleared this thing, as of `now`.

    Upsert on `(workspace_id, user_id, frame_key)`: one row per person per
    thing. A thing that came back and was dismissed a second time must be
    hidden against the LATER instant — a second row would leave the older,
    weaker stamp in play and the card would resurface immediately.

    `updated_at` is set explicitly because the column-level `onupdate` fires on
    an UPDATE statement, and this is an INSERT whose conflict clause SQLAlchemy
    does not fill in for us.
    """
    statement = (
        pg_insert(UnitDismissal)
        .values(
            dismissal_id=generate_id("dsm"),
            workspace_id=workspace_id,
            user_id=user_id,
            frame_key=frame_key,
            dismissed_at=now,
        )
        .on_conflict_do_update(
            constraint="uq_unit_dismissal_ws_user_frame",
            set_={"dismissed_at": now, "updated_at": func.now()},
        )
    )
    await db.execute(statement)


async def load_dismissals(
    db: AsyncSession, *, workspace_id: str, user_id: str
) -> dict[str, datetime]:
    """Every dismissal this person holds, as `frame_key -> dismissed_at`.

    One query for the whole feed, not one per unit: the feed asks this question
    of every card it is about to show.
    """
    result = await db.execute(
        select(UnitDismissal.frame_key, UnitDismissal.dismissed_at).where(
            UnitDismissal.workspace_id == workspace_id,
            UnitDismissal.user_id == user_id,
        )
    )
    return {row.frame_key: row.dismissed_at for row in result.all()}


def last_observed(events: Iterable[Any]) -> datetime | None:
    """When muldro last INGESTED something about this thing, or None.

    `created_at` is the row's arrival, not the event's subject-time. That
    distinction is the whole reason this function exists: `frame.updated_at`
    comes from `occurred_at`, and for a calendar unit `occurred_at` is when the
    MEETING IS. Every meeting on the founder's calendar therefore carries a
    future `updated_at`, so a rule comparing it against "now" can never call
    one settled and a dismissed meeting reappeared on the very next poll.

    Ingest time moves when, and only when, a connector brings something new —
    a reply on a thread, a rescheduled meeting — which is exactly the question
    "has this changed since I cleared it?" is asking.
    """
    stamps = [
        stamp
        for stamp in (ensure_aware_utc(getattr(event, "created_at", None)) for event in events)
        if stamp is not None
    ]
    return max(stamps) if stamps else None


def is_hidden(
    unit: Any,
    dismissals: Mapping[str, datetime],
    events_by_key: Mapping[str, Sequence[Any]],
) -> bool:
    """Has this unit been dismissed, and has it stood still since? Never raises.

    The re-surfacing rule lives here, and it is one comparison: hidden while
    nothing has been observed about the thing since `dismissed_at`, visible
    again the moment something is. A reply on a dismissed thread is new
    information and must come back; the same thread untouched must stay gone.
    No timer, and no permanence.

    A unit whose events cannot be dated stays VISIBLE. Showing a card the
    founder cleared is a smaller failure than silently swallowing one they
    never touched, and it is the posture the rest of the feed already takes.
    """
    frame = getattr(unit, "frame", None)
    key = getattr(frame, "key", None)
    if not isinstance(key, str) or not key:
        return False
    dismissed_at = ensure_aware_utc(dismissals.get(key))
    if dismissed_at is None:
        return False
    observed_at = last_observed(events_by_key.get(key) or ())
    if observed_at is None:
        return False
    return observed_at <= dismissed_at
