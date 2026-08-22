"""Turn Units into RankFeatures. The ONLY place the ranker touches the DB.

`rank()` is a pure function over a record; everything that has to ask Postgres
a question happens here, which is what keeps the ordering testable against
cases rather than eyeballed.

What this module is allowed to read
-----------------------------------
Values muldro computed about its own history, and nothing else. Concretely:
counters muldro maintains (`Entity.interaction_count`, `last_seen_at`), rows
muldro wrote (`normalized_events`, `entity_aliases`, `entity_relationships`,
goal `memories`, `engagement_history`), one deterministic parse of verbatim
external text (`deadline.py`), and one provenance-flagged header rule
(`triage.classify_by_rules`, whose headers an attacker can only use to demote
themselves).

It reads NONE of `NormalizedEvent.importance_score`, `urgency_score`,
`importance_signals.from_priority_person` or `.related_to_active_project` —
each is an LLM's assertion over the attacker's subject and body wearing a
typed name — nor `Entity.importance_score`, a stored score whose writer has
not been audited. `tests/view/ranking/test_build.py` pins that at the syntax
tree, so this paragraph cannot quietly stop being true.

Why a Unit is not enough on its own
-----------------------------------
`events_by_key` is optional but three features need it, because the facts they
rest on live on the event and not on the frame: the counterparty's **strong
identifier** (an email or handle, never a display name), the **triage
provenance flag**, and the **event type** `engagement_history` is keyed on. It
is keyed by `frame.key`, which is exactly what `perception.group_events_by_key`
already produces. Absent it, those three degrade to their neutral values
rather than to a guess.

Totality
--------
`build_features` never raises. It runs over attacker-influenced rows on a live
path, and a raise here would cost the founder the whole feed rather than one
card — the same reasoning `perception.units_from_events` and `frame.py` already
follow. A unit whose features cannot be assembled falls back to a frame-only
record; a unit with no usable frame is dropped with a log line.
"""

from __future__ import annotations

import logging
from collections.abc import Mapping, Sequence
from datetime import date, datetime
from typing import Any, Literal

from sqlalchemy import func, select
from sqlalchemy.ext.asyncio import AsyncSession

from src.models.entities import Entity, EntityAlias, EntityRelationship
from src.models.events import NormalizedEvent
from src.models.memory import Memory
from src.services.engagement_service import EngagementService
from src.services.triage import classify_by_rules
from src.view.contracts import Unit
from src.view.frame import ensure_aware_utc
from src.view.ranking.deadline import HORIZON_DAYS, extract_deadline
from src.view.ranking.features import Counterparty, RankFeatures, ThreadState

logger = logging.getLogger(__name__)

__all__ = ["DEADLINE_SOURCE", "build_features"]

DeadlineSource = Literal["structured", "verbatim_text"]

# WHERE A DEADLINE MAY COME FROM, PER SOURCE.
#
# Same shape and same fail-closed rule as `perception.py::VERBATIM_TEXT_FIELD`,
# and for the same reason: "does this connector expose a deadline, and as what?"
# is a per-source question with three possible answers, only two of which are
# admissible:
#
#   "structured"     a typed value the PROVIDER returned. No extraction, so no
#                    parser to feed and nothing to inject. Strictly the best.
#   "verbatim_text"  a deterministic parse of text a HUMAN wrote. Bounded and
#                    checkable; an attacker can lie about *when* but cannot say
#                    anything to the ranker.
#   (absent)         nothing. An unlisted source yields NO deadline.
#
# A model's assertion about prose is the third answer and is never admissible;
# it is what `importance_signals["contains_deadline"]` was.
#
# The three sources deliberately absent, and what each has instead:
#   github  - `summary` is composed ("{reason}: {title} in {repo}"). The API
#             exposes `milestone.due_on` and project dates; the connector
#             fetches NEITHER. A connector change, not a ranker one.
#   notion  - `summary` is composed ("Notion page: {title}"). Notion date
#             properties exist; the connector stores only page_id, url and
#             last_edited_time. Also a connector change.
#   (any new connector) - answer the question explicitly before adding a line.
#             "Nothing" is a valid and common answer, and it is the default.
#             Forgetting to answer costs a signal; it never opens a hole.
#
# Calendar is "structured" and MUST NOT be extracted: its `summary` is muldro's
# own composed prose, so pointing the parser at it would be strictly worse than
# reading the typed value sitting beside it. The meeting's start IS the deadline.
DEADLINE_SOURCE: dict[str, DeadlineSource] = {
    "calendar": "structured",
    "gmail": "verbatim_text",
    "slack": "verbatim_text",
}

# The only category `classify_by_rules` can return. Listed rather than assumed
# so a widened rule set has to be re-read here before it is trusted.
RULES_CATEGORIES = frozenset({"marketing"})

# An actor payload key that identifies a PERSON strongly enough for the
# `entity_aliases` uniqueness constraint to make the lookup unforgeable, paired
# with the `alias_type` it is stored under. A display `name` is deliberately
# absent: name aliases legitimately collide (many "John"s) and carry no
# constraint, so matching on one would be a judgement, not a lookup.
#   gmail / calendar -> "email"      (the connector splits the From / organizer)
#   slack            -> "slack_id"
#   github / notion  -> neither; the actor is a repo or a bare display name.
STRONG_IDENTIFIERS: tuple[tuple[str, str], ...] = (
    ("email", "email"),
    ("slack_id", "handle"),
    ("handle", "handle"),
)


# ── small readers, none of which raise ──────────────────────────────────


def _actor_dicts(event: Any) -> list[dict]:
    """Both pipeline shapes: a NormalizedEvent's list, a RawEvent's bare dict."""
    raw = getattr(event, "actor_entities", None) or getattr(event, "actor", None)
    if isinstance(raw, dict):
        return [raw]
    if isinstance(raw, (list, tuple)):
        return [entry for entry in raw if isinstance(entry, dict)]
    return []


def _strong_identifier(event: Any) -> tuple[str, str] | None:
    """The (value, alias_type) this event's actor can be looked up by, or None."""
    for entry in _actor_dicts(event):
        for payload_key, alias_type in STRONG_IDENTIFIERS:
            value = entry.get(payload_key)
            if isinstance(value, str) and value.strip():
                return value.strip(), alias_type
    return None


def _is_bulk_mail(event: Any) -> bool:
    """True only on RULES-origin evidence. An LLM's category is not evidence."""
    signals = getattr(event, "importance_signals", None)
    if isinstance(signals, dict):
        if signals.get("triage_origin") == "rules" and signals.get("category") in RULES_CATEGORIES:
            return True
    try:
        return classify_by_rules(event) in RULES_CATEGORIES
    except Exception:  # noqa: BLE001 - an unreadable payload is not bulk mail
        return False


def _hours_between(later: datetime, earlier: Any) -> float:
    """Non-negative hours. A future or unusable timestamp yields 0.0."""
    when = ensure_aware_utc(earlier)
    if when is None:
        return 0.0
    return max(0.0, (later - when).total_seconds() / 3600.0)


def _entity_id_of(frame: Any) -> str | None:
    """The external id inside `frame.key`, which is f"{source}:{entity_type}:{id}"."""
    key = getattr(frame, "key", None)
    prefix = f"{getattr(frame, 'source', '')}:{getattr(frame, 'entity_type', '')}:"
    if not isinstance(key, str) or not key.startswith(prefix):
        return None
    return key[len(prefix) :] or None


def _verbatim_text(unit: Any) -> str:
    """The external text this Unit carries, which is exactly its quotes.

    A `Quote` is built by `perception.quotes_from_events` from the field
    `VERBATIM_TEXT_FIELD` names, and it is the ONLY route external text takes
    into the view layer. Reading it here rather than re-deriving it keeps one
    per-source verbatim-text map in the codebase instead of two.
    """
    texts = [
        quote.text
        for quote in getattr(unit, "quotes", None) or ()
        if isinstance(getattr(quote, "text", None), str)
    ]
    return "\n".join(texts)


def _bounded_days(deadline: date | None, now: datetime) -> int | None:
    """Days from now, or None when the date is past or beyond the horizon."""
    if deadline is None:
        return None
    days = (deadline - now.date()).days
    if days < 0 or days > HORIZON_DAYS:
        return None
    return days


def _deadline_in_days(unit: Any, now: datetime) -> int | None:
    source = getattr(getattr(unit, "frame", None), "source", None)
    mode = DEADLINE_SOURCE.get(source) if isinstance(source, str) else None
    if mode is None:
        return None
    if mode == "structured":
        when = ensure_aware_utc(getattr(unit.frame, "occurred_at", None))
        return _bounded_days(when.date() if when else None, now)
    text = _verbatim_text(unit)
    if not text:
        return None
    return _bounded_days(extract_deadline(text, now=now), now)


# ── database reads ──────────────────────────────────────────────────────


async def _goal_index(db: AsyncSession, workspace_id: str) -> dict[str, tuple[str, ...]]:
    """entity_id -> the goal memories that reference it. One query per build.

    A GRAPH JOIN, deliberately: `Memory.entity_ids` against the counterparty's
    entity. Never an embedding of the subject vector-searched against goals —
    that hands an attacker a promotion channel, since a crafted subject
    resembling the founder's goals would raise its own rank. An attacker
    cannot make a goal memory reference them.
    """
    stmt = select(Memory).where(
        Memory.workspace_id == workspace_id,
        Memory.memory_type == "goal",
        Memory.status == "active",
    )
    rows = (await db.execute(stmt)).scalars().all()
    index: dict[str, set[str]] = {}
    for row in rows:
        for entity_id in getattr(row, "entity_ids", None) or ():
            if isinstance(entity_id, str):
                index.setdefault(entity_id, set()).add(row.memory_id)
    return {entity_id: tuple(sorted(ids)) for entity_id, ids in index.items()}


async def _message_count(db: AsyncSession, user_id: str, source: str, entity_id: str) -> int:
    """Rows muldro wrote for this thing. Indexed on (user_id, source, entity_id)."""
    stmt = (
        select(func.count())
        .select_from(NormalizedEvent)
        .where(
            NormalizedEvent.user_id == user_id,
            NormalizedEvent.source == source,
            NormalizedEvent.entity_id == entity_id,
        )
    )
    return max(1, int((await db.execute(stmt)).scalar() or 0))


async def _alias_entity_id(
    db: AsyncSession, workspace_id: str, value: str, alias_type: str
) -> str | None:
    """A LOOKUP, not a judgement — the strong-identifier index is unique per workspace."""
    candidates = [value] if value == value.lower() else [value, value.lower()]
    for candidate in candidates:
        stmt = select(EntityAlias).where(
            EntityAlias.workspace_id == workspace_id,
            EntityAlias.alias == candidate,
            EntityAlias.alias_type == alias_type,
        )
        rows = (await db.execute(stmt)).scalars().all()
        if rows:
            return rows[0].entity_id
    return None


async def _relationship_type(db: AsyncSession, workspace_id: str, entity_id: str) -> str | None:
    """The strongest active relation naming this entity, either direction.

    Two statements rather than one `or_`, so each carries an equality the test
    double can evaluate. `active` is filtered in Python for the same reason.
    """
    rows: list[Any] = []
    for column in (EntityRelationship.to_entity_id, EntityRelationship.from_entity_id):
        stmt = select(EntityRelationship).where(
            EntityRelationship.workspace_id == workspace_id,
            column == entity_id,
        )
        rows.extend((await db.execute(stmt)).scalars().all())
    active = [row for row in rows if getattr(row, "active", True)]
    if not active:
        return None
    best = min(
        active,
        key=lambda r: (
            -(getattr(r, "strength", 0.0) or 0.0),
            getattr(r, "relation_type", "") or "",
            getattr(r, "relation_id", "") or "",
        ),
    )
    relation = getattr(best, "relation_type", None)
    return relation if isinstance(relation, str) and relation else None


async def _resolve_counterparty(
    db: AsyncSession, workspace_id: str, identifier: tuple[str, str], now: datetime
) -> tuple[Counterparty, str | None]:
    value, alias_type = identifier
    entity_id = await _alias_entity_id(db, workspace_id, value, alias_type)
    if entity_id is None:
        return Counterparty(known=False), None

    stmt = select(Entity).where(
        Entity.entity_id == entity_id,
        Entity.workspace_id == workspace_id,
    )
    rows = (await db.execute(stmt)).scalars().all()
    entity = rows[0] if rows else None

    last_seen = ensure_aware_utc(getattr(entity, "last_seen_at", None)) if entity else None
    return (
        Counterparty(
            known=True,
            relationship=await _relationship_type(db, workspace_id, entity_id),
            # prior_threads stays None: see features.py. "Distinct threads from
            # this counterparty" needs an actor-indexed query and actor_entities
            # is unindexed JSONB, so 0 here would assert a fact nobody checked.
            interaction_count=max(0, int(getattr(entity, "interaction_count", 0) or 0)),
            days_since_last_seen=(
                max(0, (now - last_seen).days) if last_seen is not None else None
            ),
        ),
        entity_id,
    )


# ── assembly ────────────────────────────────────────────────────────────


def _minimal(frame: Any) -> RankFeatures:
    """Everything the frame alone can say. The fallback when a read fails."""
    return RankFeatures(
        key=frame.key,
        kind=frame.kind,
        source=frame.source,
        counterparty=Counterparty(known=False),
        thread=ThreadState(),
        has_unresolved_affordance=bool(getattr(frame, "affordances", None)),
    )


async def _features_for(
    unit: Unit,
    *,
    db: AsyncSession,
    workspace_id: str,
    user_id: str,
    now: datetime,
    events: Sequence[Any],
    goals: Mapping[str, tuple[str, ...]],
    engagement: Any,
    resolved: dict[tuple[str, str], tuple[Counterparty, str | None]],
) -> RankFeatures:
    frame = unit.frame
    latest = events[-1] if events else None

    counterparty = Counterparty(known=False)
    entity_id = None
    identifier = _strong_identifier(latest) if latest is not None else None
    if identifier is not None:
        if identifier not in resolved:
            resolved[identifier] = await _resolve_counterparty(db, workspace_id, identifier, now)
        counterparty, entity_id = resolved[identifier]

    external_id = _entity_id_of(frame)
    message_count = (
        await _message_count(db, user_id, frame.source, external_id)
        if external_id is not None
        else 1
    )

    # `engagement_history` is keyed on the EVENT type; a frame carries the
    # ENTITY type. Substituting one for the other keys the penalty on a
    # different taxonomy, so with no event we ask nothing and demote nothing.
    penalty = 0.0
    suppressed = False
    event_type = getattr(latest, "event_type", None) if latest is not None else None
    if isinstance(event_type, str) and event_type:
        suppressed = bool(await engagement.is_suppressed(frame.source, event_type))
        penalty = min(
            1.0, max(0.0, float(await engagement.get_relevance_penalty(frame.source, event_type)))
        )

    return RankFeatures(
        key=frame.key,
        kind=frame.kind,
        source=frame.source,
        counterparty=counterparty,
        thread=ThreadState(
            message_count=message_count,
            # you_replied stays None — NOT KNOWABLE. There is no sent-mail
            # ingestion and no `email_sent` event type; False would read as
            # "you ignored them".
            hours_since_last=_hours_between(now, getattr(frame, "updated_at", None)),
        ),
        has_unresolved_affordance=bool(getattr(frame, "affordances", None)),
        bulk_mail=_is_bulk_mail(latest) if latest is not None else False,
        engagement_penalty=penalty,
        suppressed=suppressed,
        age_hours=_hours_between(now, getattr(frame, "occurred_at", None)),
        deadline_in_days=_deadline_in_days(unit, now),
        matched_goal_ids=goals.get(entity_id, ()) if entity_id else (),
    )


async def build_features(
    units: Sequence[Unit],
    *,
    db: AsyncSession,
    workspace_id: str,
    user_id: str,
    now: datetime,
    events_by_key: Mapping[str, Sequence[Any]] | None = None,
) -> list[RankFeatures]:
    """Assemble the ranker's input. Never raises.

    `now` is a required argument for the same reason `extract_deadline`'s is:
    ages and deadlines resolve against a reference point, and a function that
    reads the wall clock cannot be tested deterministically.

    `events_by_key` is keyed by `frame.key` — the shape
    `perception.group_events_by_key` already returns.
    """
    by_key = events_by_key or {}
    engagement = EngagementService(db, workspace_id)
    resolved: dict[tuple[str, str], tuple[Counterparty, str | None]] = {}

    try:
        goals = await _goal_index(db, workspace_id)
    except Exception as exc:  # noqa: BLE001 - a goal read must not cost the feed
        logger.warning("rank_goal_index_failed error=%s", exc)
        goals = {}

    features: list[RankFeatures] = []
    for unit in units:
        frame = getattr(unit, "frame", None)
        key = getattr(frame, "key", None)
        if not isinstance(key, str) or not key:
            logger.warning("rank_features_skipped_unusable_frame unit=%r", type(unit).__name__)
            continue
        try:
            features.append(
                await _features_for(
                    unit,
                    db=db,
                    workspace_id=workspace_id,
                    user_id=user_id,
                    now=now,
                    events=list(by_key.get(key) or ()),
                    goals=goals,
                    engagement=engagement,
                    resolved=resolved,
                )
            )
            continue
        except Exception as exc:  # noqa: BLE001 - one bad unit costs its own features
            logger.warning("rank_features_degraded key=%s error=%s", key, exc)
        try:
            features.append(_minimal(frame))
        except Exception as exc:  # noqa: BLE001 - and if even that fails, the card
            logger.warning("rank_features_dropped key=%s error=%s", key, exc)
    return features
