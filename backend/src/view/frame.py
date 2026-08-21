"""Build a Frame from a NormalizedEvent.

NormalizedEvent already carries everything a frame needs - source,
entity_type, entity_id, occurred_at, actor_entities, importance_score - and is
indexed on (user_id, source, entity_id). The perception layer previously
discarded it and rebuilt a worse frame by concatenating rows into prose, which
is why the unit was a poll cycle rather than a thing.

This module is the ONLY place a Frame is constructed from perception.
"""

import math
import re
from datetime import datetime, timezone
from typing import Any

from src.view.contracts import Affordance, Frame, FrameKind, FrameStatus

# Frame.headline's validator REFUSES markdown, all three GFM autolink forms,
# CommonMark protocol autolinks, control characters and bidi overrides. A real
# inbound subject must still produce a card, so every one of those constructs
# is neutralized HERE and the validator stands as the backstop for every other
# caller. The two must stay aligned: whatever the validator refuses, _plain has
# already removed. tests/view/test_frame.py pins that relationship against the
# validator's own pattern so neither side can be changed alone.
_STRIP_CONTROL = re.compile(r"[\x00-\x1f\x7f-\x9f‪-‮⁦-⁩]")
_STRIP_LINK = re.compile(r"\[([^\]]*)\]\([^)]*\)")
_STRIP_MARKS = re.compile(r"[*_`#\[\]()~<>]")
# Trailing `\S*` (not `\S+`) so a bare truncated scheme - "https://" with
# nothing after it - is removed too; the validator refuses it either way.
_STRIP_AUTOLINK = re.compile(r"https?://\S*|www\.\S*|\S+@\S+\.\S+")


def _plain(text: str | None) -> str:
    """Reduce external text to inert plain text. May return ''."""
    if not text:
        return ""
    out = _STRIP_CONTROL.sub(" ", text)
    out = _STRIP_LINK.sub(r"\1", out)
    out = _STRIP_MARKS.sub("", out)
    # Autolinks are removed AFTER the markdown punctuation, because stripping
    # that punctuation can reveal a link it was hiding: "a[.]b@c[.]d" only
    # becomes the email "a.b@c.d" once the brackets are gone. Substituting a
    # space rather than "" keeps the text either side of a removed link from
    # splicing into a fresh one.
    out = _STRIP_AUTOLINK.sub(" ", out)
    return " ".join(out.split())


def _actor_name(actor_entities: Any) -> str:
    """The counterparty's name, or '' when the event names nobody usable.

    Production stores a LIST of actor dicts - EventProcessor writes
    `[raw.actor] if raw.actor else None` at both writer sites - despite
    NormalizedEvent annotating `actor_entities: Mapped[dict | None]`. The list
    is therefore the shape that matters; the bare dict is accepted too, since
    it is what the model's own annotation claims.
    """
    if isinstance(actor_entities, dict):
        candidates: list[Any] = [actor_entities]
    elif isinstance(actor_entities, (list, tuple)):
        candidates = list(actor_entities)
    else:
        return ""

    for entry in candidates:
        if not isinstance(entry, dict):
            continue
        name = entry.get("name") or entry.get("canonical_name")
        if isinstance(name, str):
            plain = _plain(name)
            if plain:
                return plain
    return ""


def event_actor_name(event: Any) -> str:
    """The counterparty on an event, whichever pipeline stage it came from.

    A Unit is built at TWO points: perception_runner groups a poll's
    pre-ingest RawEvents, and everything downstream reads the NormalizedEvent
    rows ingest wrote. The two disagree on one field name - a RawEvent's
    counterparty is `actor` (a bare dict), a NormalizedEvent's is
    `actor_entities` (a list of dicts). Reading only one of them does not
    fail loudly: it silently yields "", which drops the person from a
    headline and drops a quote entirely.

    Both readers go through here so the pair cannot drift.
    """
    return _actor_name(getattr(event, "actor_entities", None) or getattr(event, "actor", None))


def _importance(raw: Any) -> float:
    """Clamp to Frame.importance's [0.0, 1.0]. Never raises.

    importance_score is a bare nullable Float written straight from LLM JSON,
    bounded only by the words "float 0.0-1.0" in a prompt - nothing clamps it
    on the way in. A model answering `85` (percent) would otherwise raise a
    ValidationError inside frame_for_event and the card would silently never
    exist, which is the same outcome the design rejected when it chose to
    neutralize a hostile subject rather than refuse it.
    """
    try:
        value = float(raw)
    except (TypeError, ValueError):
        return 0.0
    if math.isnan(value):
        return 0.0
    return min(1.0, max(0.0, value))


def frame_key(source: str, entity_type: str, entity_id: str) -> str:
    """The identity of a thing, supplied by the source system.

    Deterministic by construction: same event in, same key out. Two messages
    on one thread share it, so the second updates the card rather than
    minting another.
    """
    return f"{source}:{entity_type}:{entity_id}"


def frame_for_event(
    event: Any,
    *,
    kind: FrameKind = "proposal",
    status: FrameStatus = "needs_you",
    group_key: str | None = None,
    event_count: int = 1,
    updated_at: datetime | None = None,
    affordances: list[Affordance] | None = None,
) -> Frame:
    """Project a NormalizedEvent - or a pre-ingest RawEvent - onto a Frame.

    Both shapes exist because a Unit is built at TWO points in the pipeline:
    perception_runner groups a poll's RawEvents before ingest has run, and
    everything downstream reads the NormalizedEvent rows ingest wrote. They
    name the counterparty differently, which is why the actor is read through
    `event_actor_name` rather than off one field.

    A RawEvent carries no `importance_score` at all, so `importance` is 0.0
    for pre-ingest events. That is correct and deliberate: importance is
    assigned at ingest by the scorer, and inventing a placeholder here would
    be muldro asserting a judgement it has not made.

    `kind` and `status` are the CALLER's decision - they depend on what the
    domain row means, which the event alone does not say. They are never the
    model's.
    """
    subject = _plain(getattr(event, "title", None))
    actor = event_actor_name(event)

    if actor and subject:
        headline = f"{actor} - {subject}"
    elif subject:
        headline = subject
    elif actor:
        headline = actor
    else:
        # Both unusable. Name what muldro actually knows rather than inventing
        # a constant like "New activity", which made three cards look alike.
        headline = f"{event.source} {event.entity_type}".strip()

    occurred = getattr(event, "occurred_at", None) or datetime.now(timezone.utc)

    return Frame(
        key=frame_key(event.source, event.entity_type, event.entity_id),
        group_key=group_key,
        kind=kind,
        status=status,
        headline=headline,
        source=event.source,
        entity_type=event.entity_type,
        occurred_at=occurred,
        updated_at=updated_at or occurred,
        importance=_importance(getattr(event, "importance_score", None)),
        event_count=event_count,
        affordances=affordances or [],
    )
