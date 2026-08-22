"""Decide which Units are worth a model call, and give them their bodies.

COST LIVES HERE. One model call per unit per poll is real money, and two things
bound it:

  * THE STRUCTURAL SKIP does most of the work. A unit whose stored body was
    written over the SAME event ids costs zero calls, forever, until a new
    message arrives. Steady state on a quiet inbox is zero calls per poll.
  * MAX_BODIES_PER_POLL caps a burst.

RANK-BEFORE-GENERATE IS DEFERRED, NOT MISSED. `RankFeatures` holds no prose, so
`src/view/ranking` could legitimately run first and generate only for the units
that will actually surface. It is not wired here because `build_features` has
no production caller yet, and folding that cutover into this change would make
both harder to verify. Until then the cap prefers the order it is given, which
`group_events_by_key` makes newest-first. Substituting "most attention-worthy"
for "newest" is a change to the ORDER of `units`, not to anything below.
"""

import logging
from collections.abc import Mapping, Sequence
from datetime import datetime, timezone

from sqlalchemy.ext.asyncio import AsyncSession

from src.view.body_generator import BodyUnavailable, generate_body
from src.view.body_store import is_current, load_bodies, save_body
from src.view.contracts import Unit

logger = logging.getLogger(__name__)

# A burst ceiling, not a ranking. Deliberately small: an unbounded poll of a
# newly-connected mailbox would otherwise mint a model call per thread.
MAX_BODIES_PER_POLL = 8


def _with_body(unit: Unit, body: str) -> Unit:
    """A NEW Unit. `Unit` is frozen, and rebuilding revalidates it."""
    return Unit(frame=unit.frame, body=body, quotes=unit.quotes)


async def fill_bodies(
    units: Sequence[Unit],
    *,
    db: AsyncSession,
    workspace_id: str,
    ids_by_key: Mapping[str, Sequence[str]],
    now: datetime | None = None,
) -> list[Unit]:
    """Return the units with their bodies filled from storage or from a call.

    The caller commits. Nothing here raises: a unit that cannot get prose gets
    an empty body, because a card with no prose is strictly better than no card
    - the frame still carries the headline, the source, the count, the
    timestamps, the quotes and the affordances.
    """
    if not units:
        return []

    as_of = now or datetime.now(timezone.utc)
    rows = await load_bodies(
        db, workspace_id=workspace_id, frame_keys=[unit.frame.key for unit in units]
    )

    filled: list[Unit] = []
    generated = 0
    for unit in units:
        key = unit.frame.key
        row = rows.get(key)
        stored = row.body if row is not None else ""
        event_ids = tuple(ids_by_key.get(key, ()))

        if row is not None and is_current(row, event_ids):
            filled.append(_with_body(unit, stored))
            continue

        if generated >= MAX_BODIES_PER_POLL:
            # Keep whatever prose exists. It was true of the earlier messages,
            # and the frame's own count and timestamp are the code-authored
            # truth about what changed since. It is regenerated next poll.
            #
            # Logged rather than dropped quietly: a poll that silently skipped
            # work reads as one that covered everything.
            logger.info(
                "view_body_deferred key=%s cap=%d",
                key,
                MAX_BODIES_PER_POLL,
                extra={"frame_key": key},
            )
            filled.append(_with_body(unit, stored))
            continue

        generated += 1
        try:
            body = await generate_body(unit.frame, unit.quotes, workspace_id=workspace_id)
        except BodyUnavailable as exc:
            # TRANSIENT: persist nothing so the next poll retries. Saving here
            # would freeze an outage into the row and stop that retry from
            # ever happening.
            logger.warning(
                "view_body_unavailable key=%s error=%s",
                key,
                exc,
                extra={"frame_key": key, "error": str(exc)},
            )
            filled.append(_with_body(unit, stored))
            continue

        # An empty body here is the repair cap's give-up, which is
        # DETERMINISTIC for this event set - persisting it is what stops it
        # being re-attempted on every poll for ever. It is a result, not a
        # missing value, so it must not be treated as falsy and skipped.
        await save_body(
            db,
            workspace_id=workspace_id,
            frame_key=key,
            body=body,
            event_ids=event_ids,
            as_of=as_of,
        )
        filled.append(_with_body(unit, body))

    return filled
