"""Build and publish the poll's Units. The runner's one call into the view layer.

Lifted out of `perception_runner.run_perception_cycle` unchanged, so the
runner has a single named call site to grow rather than a widening inline
block. The grouping, the push, the diagnostic log and the
never-take-the-poll-down guard are all the block that was inline.
"""

import logging
from typing import Any

from src.orchestrator.event_publisher import EventPublisher
from src.view.contracts import Unit
from src.view.perception import units_from_events
from src.view.publish import publish_units

logger = logging.getLogger(__name__)

__all__ = ["publish_perception_units"]


async def publish_perception_units(
    raw_events: list[Any],
    *,
    source: str,
    user_id: str,
    events: EventPublisher,
) -> list[Unit]:
    """One Unit per THING, not one signal per poll cycle — and published, not counted.

    `events` vs `units` in the log line is the diagnostic: three polls of one
    thread read `events=3 units=1`.

    These are pre-ingest RawEvents, so `frame.importance` is 0.0 and nothing
    here ranks. The REST feed (`GET /v1/workspace/units`) re-derives every Unit
    from the stored rows and ranks THERE; this push exists so a card appears
    without waiting for a poll of the frontend's own.

    The bus comes from `ensure_event_bus()`, not the `event_bus` property: the
    property is lazily initialised and is None until something has awaited the
    accessor, so on the first poll after a restart the push would be a silent
    no-op.

    Wrapped because this is a live poll: a bug in the view layer must not take
    down perception for a whole source.
    """
    try:
        units = units_from_events(raw_events)
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
