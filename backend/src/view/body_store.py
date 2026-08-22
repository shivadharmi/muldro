"""The stored body: read it, write it, and decide when it stopped being true.

A view is a pure function of a domain row, and no view reads a cache. A body
costs a model call, so it cannot be recomputed on every feed refresh —
therefore it must BE a row rather than a value derived per read, which is what
makes `unit_bodies` a domain table and not a cache table.
"""

from collections.abc import Iterable, Sequence
from datetime import datetime
from typing import Any

from sqlalchemy import func, select
from sqlalchemy.dialects.postgresql import insert as pg_insert
from sqlalchemy.ext.asyncio import AsyncSession

from src.models.ids import generate_id
from src.models.unit_body import UnitBody


async def load_bodies(
    db: AsyncSession, *, workspace_id: str, frame_keys: Sequence[str]
) -> dict[str, UnitBody]:
    """Every stored body for these things, keyed by `frame_key`.

    One query for the whole poll, not one per unit. A frame key names a thing
    but not its owner, so the workspace is part of the lookup and not an
    afterthought applied to the result.
    """
    if not frame_keys:
        return {}
    result = await db.execute(
        select(UnitBody).where(
            UnitBody.workspace_id == workspace_id,
            UnitBody.frame_key.in_(list(frame_keys)),
        )
    )
    return {row.frame_key: row for row in result.scalars().all()}


async def save_body(
    db: AsyncSession,
    *,
    workspace_id: str,
    frame_key: str,
    body: str,
    event_ids: Sequence[str],
    as_of: datetime,
) -> None:
    """Write the body for one thing, replacing whatever was there.

    Upsert on `(workspace_id, frame_key)`: one body per thing per workspace, so
    a second message on a thread REPLACES the prose rather than minting a
    second row — the row-level counterpart of the frame's own dedup, and the
    reason the frame key is derived deterministically from the thing rather
    than from the poll that saw it.

    `event_ids` is rewritten alongside `body`, because a replaced body that
    still carries the old event set is stale the moment it is written.
    `updated_at` is set explicitly: a column-level `onupdate` fires on an UPDATE
    statement, and this is an INSERT whose conflict clause SQLAlchemy does not
    fill in for us.
    """
    values = {
        "unit_body_id": generate_id("ubody"),
        "workspace_id": workspace_id,
        "frame_key": frame_key,
        "body": body,
        "event_ids": list(event_ids),
        "as_of": as_of,
    }
    statement = (
        pg_insert(UnitBody)
        .values(**values)
        .on_conflict_do_update(
            constraint="uq_unit_bodies_ws_frame",
            set_={
                "body": body,
                "event_ids": list(event_ids),
                "as_of": as_of,
                "updated_at": func.now(),
            },
        )
    )
    await db.execute(statement)


def is_current(row: Any, event_ids: Iterable[str]) -> bool:
    """Was this body written over exactly these events?

    Staleness is STRUCTURAL, NOT A TIMER: a perception body stops being true
    when a new message arrives, because the prose then describes a set of
    events that no longer exists. Nothing about a clock says that.

    The comparison is by SET. Order is not information — the grouping that
    produces these ids sorts a group oldest-first, but an order that shifted
    with connector paging would make every stored body look stale and force a
    regeneration that changed nothing, which is the exact cost this row exists
    to avoid. A missing or empty recorded set is never current: a body nothing
    can invalidate would stay up forever.
    """
    return bool(row.event_ids) and set(row.event_ids) == set(event_ids)
