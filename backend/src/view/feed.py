"""Compose the five Unit families and put them in rank order.

spec §6: there was no ranking function at all. Server order was the order
builders ran in, client order was arrival order, and `gridAutoFlow: dense`
repacked both — three independent non-decisions, stacked. `src/view/ranking`
has been built and tested since spec step 4 and NOTHING IMPORTED IT. This is
where it is wired.

The composition order below is the fallback, not the product: it is what the
feed looks like when the ranker cannot run. It must therefore be a defensible
order on its own, which is why the review queue and the briefing come first.
"""

from __future__ import annotations

import logging
from collections.abc import Sequence
from datetime import datetime
from typing import Any

from src.view.contracts import Unit
from src.view.domain_units import (
    briefing_units,
    connector_health_unit,
    prepared_work_unit,
    run_units,
)
from src.view.ranking.build import build_features
from src.view.ranking.rank import rank
from src.view.stored_units import stored_perception_units

logger = logging.getLogger(__name__)

__all__ = ["assemble_feed", "order_by_rank"]


def order_by_rank(units: Sequence[Unit], ranked_keys: Sequence[Any]) -> list[Unit]:
    """Re-order `units` to match `ranked_keys`. Total; never raises.

    A key `rank()` did not return is DROPPED, not appended. `rank()` omits a
    key for exactly two reasons and both are decisions: a duplicate handle
    names one thing, and a `suppressed` item is one the founder dismissed five
    times running. Re-appending the omitted keys would undo the demotion §6.2
    exists to apply.

    A key in `ranked_keys` with no matching Unit is ignored — the ranker sees
    the same list, but a malformed response must cost nothing.
    """
    by_key: dict[str, Unit] = {}
    for unit in units:
        key = getattr(getattr(unit, "frame", None), "key", None)
        if isinstance(key, str) and key and key not in by_key:
            by_key[key] = unit
    ordered: list[Unit] = []
    seen: set[str] = set()
    for key in ranked_keys:
        if not isinstance(key, str) or key in seen:
            continue
        unit = by_key.get(key)
        if unit is not None:
            seen.add(key)
            ordered.append(unit)
    return ordered


async def assemble_feed(
    db: Any,
    *,
    workspace_id: str,
    user_id: str,
    now: datetime,
) -> list[Unit]:
    """Every Unit the workspace shows, in rank order. Never raises.

    Each family is already total on its own read; this adds one more guarantee:
    a RANKER outage falls back to the deterministic composition order rather
    than blanking the workspace. An unordered feed is a worse feed; an empty
    one is not a feed.
    """
    perception, events_by_key = await stored_perception_units(
        db, workspace_id=workspace_id, user_id=user_id, now=now
    )
    units: list[Unit] = []
    prepared = await prepared_work_unit(db, workspace_id=workspace_id, user_id=user_id)
    if prepared is not None:
        units.append(prepared)
    units.extend(await briefing_units(db, workspace_id=workspace_id, user_id=user_id))
    units.extend(await run_units(db, workspace_id=workspace_id, now=now))
    health = await connector_health_unit(db, workspace_id=workspace_id)
    if health is not None:
        units.append(health)
    units.extend(perception)

    try:
        features = await build_features(
            units,
            db=db,
            workspace_id=workspace_id,
            user_id=user_id,
            now=now,
            events_by_key=events_by_key,
        )
        ranked = rank(features)
    except Exception as exc:  # noqa: BLE001 - an unordered feed beats no feed
        logger.warning("feed_rank_failed workspace=%s error=%s", workspace_id, exc)
        return units

    ordered = order_by_rank(units, ranked)
    if not ordered and units:
        # The ranker returned nothing usable for a non-empty feed. Suppression
        # can legitimately empty a PERCEPTION feed, but it can never suppress a
        # run or the review queue, so an all-empty result here means the ranker
        # disagreed with itself rather than that everything was dismissed.
        logger.warning("feed_rank_returned_nothing workspace=%s units=%d", workspace_id, len(units))
        return units
    return ordered
