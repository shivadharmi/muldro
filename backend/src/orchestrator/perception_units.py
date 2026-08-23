"""Build, fill and publish the poll's Units. The runner's one call into the view layer.

The grouping, the push, the diagnostic log and the never-take-the-poll-down
guard were lifted here from `perception_runner.run_perception_cycle` so the
runner has a single named call site to grow rather than a widening inline
block. This is that growth: between the grouping and the push sits the one
field the model authors.
"""

import logging
from collections.abc import Callable
from typing import Any

from src.orchestrator.event_publisher import EventPublisher
from src.view.body_fill import fill_bodies
from src.view.contracts import Unit
from src.view.perception import event_ids_by_key, units_from_events
from src.view.publish import publish_units

logger = logging.getLogger(__name__)

__all__ = ["publish_perception_units"]


async def _fill_or_keep(
    units: list[Unit],
    raw_events: list[Any],
    *,
    source: str,
    workspace_id: str,
    db_factory: Callable[[], Any],
) -> list[Unit]:
    """The units with their bodies, from storage or from a model call.

    A body is a stored row rather than a derived value: it costs a model call,
    so recomputing it on every feed refresh is not affordable, and a row is
    what makes a card's prose survive a restart. Hence the session and the
    commit - `fill_bodies` writes but deliberately does not commit, leaving
    that to whoever owns the transaction.

    A failure here costs the PROSE, never the units. The frame is code's and is
    already built, and a card with no prose is strictly better than no card -
    the frame still carries the headline, the source, the count, the timestamps
    and the quotes.
    """
    if not units or not workspace_id:
        # A body is workspace-scoped, so with no workspace there is nowhere to
        # store one. Generating first and discovering that on the write would
        # spend real money to learn it.
        return units
    try:
        async with db_factory() as db:
            filled = await fill_bodies(
                units,
                db=db,
                workspace_id=workspace_id,
                ids_by_key=event_ids_by_key(raw_events),
            )
            await db.commit()
            return filled
    except Exception as fill_error:  # noqa: BLE001 - costs the prose, not the poll
        logger.warning(
            "perception_bodies_failed source=%s error=%s",
            source,
            fill_error,
            extra={"source": source, "error": str(fill_error)},
        )
        return units


async def publish_perception_units(
    raw_events: list[Any],
    *,
    source: str,
    user_id: str,
    workspace_id: str,
    events: EventPublisher,
    db_factory: Callable[[], Any],
) -> list[Unit]:
    """One Unit per THING, not one signal per poll cycle — and published, not counted.

    `events` vs `units` in the log line is the diagnostic: three polls of one
    thread read `events=3 units=1`.

    These are pre-ingest RawEvents, so `frame.importance` is 0.0 and nothing
    here ranks. The REST feed (`GET /v1/workspace/units`) re-derives every Unit
    from the stored rows and ranks THERE; this push exists so a card appears
    without waiting for a poll of the frontend's own.

    The push comes AFTER the fill, so the live card is the whole card. Pushing
    first would put a blank prose line on screen and leave it there until the
    next feed refresh — and on a steady-state poll, where every body is already
    stored and no model call is made at all, it would push blank over prose
    that already exists. Nothing waits on this: a poll is background work whose
    latency is the poll interval, not a model call.

    The bus comes from `ensure_event_bus()`, not the `event_bus` property: the
    property is lazily initialised and is None until something has awaited the
    accessor, so on the first poll after a restart the push would be a silent
    no-op.

    Wrapped because this is a live poll: a bug in the view layer must not take
    down perception for a whole source.
    """
    try:
        units = units_from_events(raw_events)
        units = await _fill_or_keep(
            units,
            raw_events,
            source=source,
            workspace_id=workspace_id,
            db_factory=db_factory,
        )
        await publish_units(await events.ensure_event_bus(), units, user_id=user_id)
        logger.info(
            "perception_units_published source=%s events=%d units=%d",
            source,
            len(raw_events),
            len(units),
            extra={
                "source": source,
                "events": len(raw_events),
                "units": len(units),
            },
        )
        return units
    except Exception as unit_error:  # noqa: BLE001 - never costs the poll
        logger.warning(
            "perception_units_failed source=%s error=%s",
            source,
            unit_error,
            extra={"source": source, "error": str(unit_error)},
        )
        return []
