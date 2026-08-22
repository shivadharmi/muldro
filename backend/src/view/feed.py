"""Compose the five Unit families and put them in rank order.

Before this there was no ranking function at all. Server order was the order
builders ran in, client order was arrival order, and `gridAutoFlow: dense`
repacked both — three independent non-decisions, stacked. `src/view/ranking`
was built and tested and then NOTHING IMPORTED IT. This is where it is wired.

The composition order below is the fallback, not the product: it is what the
feed looks like when the ranker cannot run. It must therefore be a defensible
order on its own, which is why the review queue and the briefing come first.
"""

from __future__ import annotations

import logging
from collections.abc import Mapping, Sequence
from dataclasses import dataclass
from datetime import datetime
from typing import Any

from src.view.body_fill import attach_stored_bodies
from src.view.contracts import Unit
from src.view.domain_units import (
    briefing_units,
    connector_health_unit,
    insight_units,
    prepared_work_unit,
    run_units,
)
from src.view.ranking.build import build_features
from src.view.ranking.rank import rank
from src.view.stored_units import stored_perception_units

logger = logging.getLogger(__name__)

__all__ = ["Feed", "assemble_feed", "order_by_rank", "partition_for_fold"]


def order_by_rank(units: Sequence[Unit], ranked_keys: Sequence[Any]) -> list[Unit]:
    """Re-order `units` to match `ranked_keys`. Total; never raises.

    A key `rank()` did not return is DROPPED, not appended. `rank()` omits a
    key for exactly two reasons and both are decisions: a duplicate handle
    names one thing, and a `suppressed` item is one the founder dismissed five
    times running. Re-appending the omitted keys would undo the very demotion
    those omissions exist to apply.

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


@dataclass(frozen=True)
class Feed:
    """The ordered units, and where attention stops.

    `fold_after` is an index into `units`: everything before it is shown, and
    everything from it onward is collapsed behind one row. It is NOT a filter —
    the tail is still on the wire, still ranked, still reachable. A hidden thing
    that cannot be reached is a lie about coverage, and it would also make the
    ordering unfalsifiable: a `WHERE` clause that never returned the row leaves
    nothing to check the ranker against.
    """

    units: list[Unit]
    fold_after: int


def partition_for_fold(
    units: Sequence[Unit], bulk_by_key: Mapping[str, bool]
) -> tuple[list[Unit], int]:
    """Split the ranked feed into what is shown and what is collapsed.

    Returns `(units, fold_after)`: a STABLE partition — signal first, bulk
    after, each group in the rank order it arrived in — and the index where the
    second group starts.

    A POSITIONAL boundary was tried first and does not work, for a reason only
    real data shows: the ranker deliberately interleaves the two classes.
    `W_BULK_MAIL` demotes bulk by 2.5 but `W_RECENCY` lifts a recent item by up
    to 2.0, so a marketing mail from an hour ago outranks a real thread from
    yesterday. Bulk is therefore SCATTERED through the order, not gathered at
    the end. Cutting before the first bulk item folds real signal below it;
    cutting after the last signal item shows everything above it — measured on
    a live inbox, that left 42 of 85 visible, most of them delivery receipts
    and card alerts. No threshold fixes it, because the instrument is wrong.

    Partitioning makes the boundary the PREDICATE, which is what it always
    meant. Ordering and membership are orthogonal questions: `rank()` answers
    "in what sequence", this answers "is it worth the founder's attention", and
    neither needs to be bent to serve the other. `rank()` stays a pure
    permutation — nothing is re-scored here, only grouped for presentation.

    `bulk_mail` is rules-origin evidence — `List-Unsubscribe`, `List-Id`,
    `Precedence` — headers an attacker can only use against themselves, since
    adding one makes a message MORE bulk. An LLM's category is deliberately not
    consulted: letting model-authored judgement decide what the founder sees
    would hand external text a lever on its own visibility, in the direction
    that hides rather than shows.

    Nothing is dropped. The tail is still on the wire, still ordered, still
    reachable — a hidden thing that cannot be reached is a lie about coverage,
    and it would leave the ranker unfalsifiable besides.
    """
    signal: list[Unit] = []
    quiet: list[Unit] = []
    for unit in units:
        key = getattr(getattr(unit, "frame", None), "key", None)
        # Absent evidence is not evidence of bulk: a key the ranker never
        # scored stays visible rather than being hidden by a failed lookup.
        (quiet if bulk_by_key.get(key, False) else signal).append(unit)
    return signal + quiet, len(signal)


async def assemble_feed(
    db: Any,
    *,
    workspace_id: str,
    user_id: str,
    now: datetime,
) -> Feed:
    """Every Unit the workspace shows, in rank order, and where to fold.

    Never raises.

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
    # What muldro concluded, ahead of the perceived things it concluded it
    # FROM: an interpretation is worth more than the raw signal behind it.
    units.extend(await insight_units(db, workspace_id=workspace_id, user_id=user_id, now=now))
    health = await connector_health_unit(db, workspace_id=workspace_id)
    if health is not None:
        units.append(health)
    units.extend(perception)

    # The prose is written by the poll and stored; the feed reads it back. A
    # feed read never generates - see `attach_stored_bodies`.
    units = await attach_stored_bodies(units, db=db, workspace_id=workspace_id)

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
        # No fold on an unranked list. The fold says "attention stops here",
        # which is only true of an order something actually decided; folding a
        # composition order would hide things on the strength of the sequence
        # builders happened to run in.
        logger.warning("feed_rank_failed workspace=%s error=%s", workspace_id, exc)
        return Feed(units=units, fold_after=len(units))

    ordered = order_by_rank(units, ranked)
    if not ordered and units:
        # The ranker returned nothing usable for a non-empty feed. Suppression
        # can legitimately empty a PERCEPTION feed, but it can never suppress a
        # run or the review queue, so an all-empty result here means the ranker
        # disagreed with itself rather than that everything was dismissed.
        logger.warning("feed_rank_returned_nothing workspace=%s units=%d", workspace_id, len(units))
        return Feed(units=units, fold_after=len(units))

    # Built from the SAME features the order came from, so the boundary cannot
    # disagree with the sequence it cuts.
    bulk_by_key = {f.key: bool(getattr(f, "bulk_mail", False)) for f in features}
    shown_then_quiet, fold_after = partition_for_fold(ordered, bulk_by_key)
    return Feed(units=shown_then_quiet, fold_after=fold_after)
