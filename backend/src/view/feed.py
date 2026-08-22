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

from src.services.unit_dismissals import OWN_SOURCE, is_hidden, load_dismissals
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

__all__ = [
    "Feed",
    "assemble_feed",
    "drop_dismissed",
    "order_by_rank",
    "partition_for_fold",
    "quiet_units",
]


async def drop_dismissed(
    db: Any, units: Sequence[Unit], *, workspace_id: str, user_id: str
) -> list[Unit]:
    """Remove the things this person has cleared and that have not moved since.

    Total; never raises. A dismissal read that fails shows EVERYTHING: a card
    the founder dismissed is a far smaller failure than a blank workspace, and
    it is the same posture the ranker's own outage takes below.

    muldro's own units are never dropped here, whatever rows exist. The route
    refuses to dismiss one, so no such row should be written — but the review
    queue, the briefing and the runs are the founder's only route to work
    muldro is holding, and a stray key must not be able to take that away.
    """
    try:
        dismissals = await load_dismissals(db, workspace_id=workspace_id, user_id=user_id)
    except Exception as exc:  # noqa: BLE001 - a dismissal outage beats an empty feed
        logger.warning("feed_dismissals_failed workspace=%s error=%s", workspace_id, exc)
        return list(units)
    if not dismissals:
        return list(units)
    return [
        unit
        for unit in units
        if getattr(getattr(unit, "frame", None), "source", None) == OWN_SOURCE
        or not is_hidden(unit, dismissals)
    ]


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
    units: Sequence[Unit], quiet_by_key: Mapping[str, bool]
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

    What counts as quiet is `quiet_by_key`'s question, not this function's — see
        `quiet_units`.

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
        (quiet if quiet_by_key.get(key, False) else signal).append(unit)
    return signal + quiet, len(signal)


# Triage's own verdict on the one question the fold asks: does this need the
# founder? Consulted for DEMOTION ONLY, and that asymmetry is the whole safety
# argument. An attacker wants to be SEEN, so the manipulation available to them
# is to read as actionable — which leaves them exactly where they are today,
# visible. Marking yourself unactionable only hides you, which is self-harm.
# The same shape the ranker already accepts for engagement: demotion has no
# self-sealing loop, promotion does.
#
# It is a JUDGEMENT, unlike the rules-origin headers, so it is kept out of
# `RankFeatures.bulk_mail` — that value feeds the ranking SCORE and carries a
# documented rules-only guarantee. This decides visibility at the fold and
# nothing else, which is a place a founder can see and disagree with.
_TRIAGE_ACTIONABLE = "actionable"


def quiet_units(
    units: Sequence[Unit],
    features: Sequence[Any],
    events_by_key: Mapping[str, Sequence[Any]],
) -> dict[str, bool]:
    """Which units are below the founder's attention. Total; never raises.

    Two sources, OR-ed:

      * `bulk_mail` from `RankFeatures` — rules-origin headers
        (`List-Unsubscribe`, `List-Id`, `Precedence`) an attacker can only use
        against themselves, since adding one makes a message MORE bulk;
      * triage's `actionable=false`, for the transactional mail no header
        marks: bank alerts, OTPs, delivery receipts. `classify_by_rules` can
        only return "marketing", so rules alone leave those visible.

    A unit with NO evidence either way stays visible. Absent evidence is not
    evidence of quiet, and muldro's own cards — runs, findings, the briefing —
    have no triage row at all and must never be folded by a lookup that missed.
    """
    quiet: dict[str, bool] = {}
    for feature in features:
        key = getattr(feature, "key", None)
        if isinstance(key, str):
            quiet[key] = bool(getattr(feature, "bulk_mail", False))

    for key, events in (events_by_key or {}).items():
        if quiet.get(key):
            continue
        # Every event on the thing must be unactionable. One that needs the
        # founder makes the whole thing need them: a reply on a receipt thread
        # is still a reply.
        verdicts = [
            (getattr(e, "importance_signals", None) or {}).get(_TRIAGE_ACTIONABLE)
            for e in (events or ())
        ]
        if verdicts and all(v is False for v in verdicts):
            quiet[key] = True
    return quiet


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

    # Before the body lookup and before ranking: a thing the founder has
    # cleared should cost neither a stored body nor a rank slot.
    units = await drop_dismissed(db, units, workspace_id=workspace_id, user_id=user_id)

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
    quiet = quiet_units(ordered, features, events_by_key)
    shown_then_quiet, fold_after = partition_for_fold(ordered, quiet)
    return Feed(units=shown_then_quiet, fold_after=fold_after)
