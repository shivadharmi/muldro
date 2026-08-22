"""Build a Frame from a NormalizedEvent.

NormalizedEvent already carries almost everything a frame needs - source,
entity_type, entity_id, occurred_at, actor_entities - and is indexed on
(user_id, source, entity_id). The perception layer previously discarded it and
rebuilt a worse frame by concatenating rows into prose, which is why the unit
was a poll cycle rather than a thing.

`importance_score` is the one field on the event that a frame must NOT read:
it is LLM-authored from the event's title and summary, i.e. from the
attacker-controlled subject and body. It comes from a caller instead - see
`frame_for_event`.

This module is the ONLY place a Frame is constructed from perception.
"""

import math
import re
from datetime import datetime, timezone
from typing import Any

from src.view.contracts import MAX_HEADLINE_CHARS, Affordance, Frame, FrameKind, FrameStatus

# Frame.headline's validator REFUSES markdown, all three GFM autolink forms,
# CommonMark protocol autolinks, control characters and bidi overrides. A real
# inbound subject must still produce a card, so every one of those constructs
# is neutralized HERE and the validator stands as the backstop for every other
# caller. The two must stay aligned: whatever the validator refuses, _plain has
# already removed. tests/view/test_frame.py pins that relationship against the
# validator's own pattern so neither side can be changed alone.
_STRIP_CONTROL = re.compile(
    r"[\x00-\x1f\x7f-\x9f"  # C0 / C1 control characters, newline included
    r"\u202a-\u202e"  # bidi embedding + override (RLO can reverse a headline)
    r"\u2066-\u2069]"  # bidi isolates
)
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


def ensure_aware_utc(value: Any) -> datetime | None:
    """Coerce an event timestamp to a tz-aware datetime, or None. Never raises.

    ONE timestamp policy for the view layer. A Frame carries two datetimes
    (`occurred_at`, `updated_at`) which are frequently derived from the same
    event by different call sites; when only one of them coerced, a single
    Frame ended up with a naive `occurred_at` and an aware `updated_at`.
    Subtracting the frame's own two fields then raised TypeError, and - worse,
    because it is silent - `model_dump_json()` emitted "…T10:00:00" for one and
    "…T10:00:00Z" for the other. JavaScript parses the offsetless form as LOCAL
    time, so the same instant renders hours apart on the card (5.5h for an IST
    reader).

    A naive value is assumed UTC rather than rejected: notion_connector.py
    parses `last_edited_time` with no offset guarantee, and github_connector.py
    already articulates this exact hazard at its own boundary. An offset that
    is present is preserved rather than converted - it names the same instant,
    and the source's own offset is information.

    A non-datetime is treated as absent rather than fatal: external payloads
    are the source of these values, and a malformed one must cost its own
    event, not the poll.

    `perception.py::_occurred` and `frame_for_event` are both expected to route
    through here, so a Frame's two timestamps cannot disagree about tz policy.
    """
    if not isinstance(value, datetime):
        return None
    if value.tzinfo is None:
        return value.replace(tzinfo=timezone.utc)
    return value


def _raw_names(actor_entities: Any) -> list[str]:
    """Every name string an actor field offers, in preference order."""
    if isinstance(actor_entities, dict):
        candidates: list[Any] = [actor_entities]
    elif isinstance(actor_entities, (list, tuple)):
        candidates = list(actor_entities)
    else:
        return []

    names: list[str] = []
    for entry in candidates:
        if not isinstance(entry, dict):
            continue
        name = entry.get("name") or entry.get("canonical_name")
        if isinstance(name, str):
            names.append(name)
    return names


def _local_part(name: str) -> str:
    """BACKSTOP: salvage a display name from a value that is a bare address.

    `_plain` removes a bare email address, because the headline validator
    refuses one - so an actor whose `name` IS an address resolves to '' and
    the person vanishes from the headline and their quote is dropped.

    The fix belongs where the knowledge is, and it is there: gmail.py splits
    its RFC 5322 `From` and writes the local part when there is no display
    name. This is the backstop for everything that ISN'T that - a
    NormalizedEvent row written before that fix, a source that puts an
    address in `name` (calendar's organizer `displayName` can be blank), a
    hand-built event. It parses nothing: there is no header here to parse,
    only an unusable string to salvage a readable fragment from.

    The result goes back through `_plain`, so this cannot reintroduce a
    construct the validator refuses - a name like "www.evil.example@x.example"
    salvages to nothing and stays unattributed, which is the right way for
    this to fail.
    """
    if "@" not in name:
        return ""
    return _plain(name.split("@", 1)[0])


def _actor_name(actor_entities: Any) -> str:
    """The counterparty's name, or '' when the event names nobody usable.

    Production stores a LIST of actor dicts - EventProcessor writes
    `[raw.actor] if raw.actor else None` at both writer sites - despite
    NormalizedEvent annotating `actor_entities: Mapped[dict | None]`. The list
    is therefore the shape that matters; the bare dict is accepted too, since
    it is what the model's own annotation claims.

    A real display name always wins, on ANY entry, before the bare-address
    backstop is tried on any of them - hence two passes. An entry naming a
    person is better attribution than a fragment of another entry's address.
    """
    names = _raw_names(actor_entities)
    for name in names:
        plain = _plain(name)
        if plain:
            return plain
    for name in names:
        salvaged = _local_part(name)
        if salvaged:
            return salvaged
    return ""


# How far back from the limit to look for a space. A headline that ends on a
# word is worth losing a few characters for; losing a third of the budget to
# reach an early space is not, so a subject with no boundary in this window is
# cut where it falls.
_WORD_BOUNDARY_WINDOW = 40


def _clamp_headline(text: str) -> str:
    """Bound the composed headline to MAX_HEADLINE_CHARS. Never raises.

    The same rule `_plain` and `_importance` already follow: BOUND, NEVER
    REFUSE - a refused headline is a card the founder never sees. A 258-char
    subject really did produce zero Units, silently, via a ValidationError
    caught upstream.

    The body's overrun IS a validation failure, correctly: the model authors
    the body, so the typed-generation repair loop can ask it for a shorter
    one. The headline is authored by CODE from an external subject. There is
    nobody to repair it, so a raise here has no recovery path at all and can
    only drop the card.

    Truncation cannot reintroduce a construct the validator refuses: `_plain`
    has already removed every one of them, and removing a suffix cannot mint
    a new link, autolink or control character.
    """
    if len(text) <= MAX_HEADLINE_CHARS:
        return text
    cut = text[:MAX_HEADLINE_CHARS]
    boundary = cut.rfind(" ", MAX_HEADLINE_CHARS - _WORD_BOUNDARY_WINDOW)
    if boundary > 0:
        cut = cut[:boundary]
    # No ellipsis and no "read more": CSS line-clamp-2 already signals visual
    # truncation, and a "..." says "this was cut" a second time.
    return cut.rstrip()


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
    """Clamp a CALLER-supplied score to Frame.importance's [0.0, 1.0].

    Never raises. This no longer guards LLM JSON - the event's score is not
    read at all - it guards whatever a caller passes, and eventually that
    caller is the ranker. A caller answering `85` (percent) would otherwise raise a
    ValidationError inside frame_for_event and the card would silently never
    exist, which is the same outcome the design rejected when it chose to
    neutralize a hostile subject rather than refuse it. A ranker bug should
    degrade a score, not delete a card.
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
    importance: float = 0.0,
    affordances: list[Affordance] | None = None,
) -> Frame:
    """Project a NormalizedEvent - or a pre-ingest RawEvent - onto a Frame.

    Both shapes exist because a Unit is built at TWO points in the pipeline:
    perception_runner groups a poll's RawEvents before ingest has run, and
    everything downstream reads the NormalizedEvent rows ingest wrote. They
    name the counterparty differently, which is why the actor is read through
    `event_actor_name` rather than off one field.

    `kind`, `status` and `importance` are the CALLER's decision - they depend
    on what the domain row means, which the event alone does not say. They are
    never the model's.

    `importance` in particular is NOT read off `event.importance_score`, even
    though NormalizedEvent carries one: that score is LLM-authored from the
    event's title and summary - the attacker-controlled subject and body - and
    Frame carries no model-authored field, so external prose cannot raise its
    own rank. It defaults to 0.0 until the ranker supplies one from features
    muldro derived itself.
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

    # After composition, not before: the actor prefix spends the same budget.
    headline = _clamp_headline(headline)

    # Both timestamps go through the SAME normalizer: a Frame whose two
    # datetimes disagree about tz-awareness cannot be compared with itself and
    # serializes to two different wire formats. See `ensure_aware_utc`.
    occurred = ensure_aware_utc(getattr(event, "occurred_at", None)) or datetime.now(timezone.utc)
    updated = ensure_aware_utc(updated_at) or occurred

    return Frame(
        key=frame_key(event.source, event.entity_type, event.entity_id),
        group_key=group_key,
        kind=kind,
        status=status,
        headline=headline,
        source=event.source,
        entity_type=event.entity_type,
        occurred_at=occurred,
        updated_at=updated,
        importance=_importance(importance),
        event_count=event_count,
        affordances=affordances or [],
    )


def frame_for_row(
    *,
    source: str,
    entity_type: str,
    entity_id: str,
    kind: FrameKind,
    status: FrameStatus,
    headline: str,
    occurred_at: datetime | None,
    updated_at: datetime | None = None,
    group_key: str | None = None,
    event_count: int = 1,
    affordances: list[Affordance] | None = None,
) -> Frame:
    """Project one of MULDRO'S OWN rows - a run, a briefing, a queue - onto a Frame.

    The sibling of `frame_for_event`, and deliberately the same neutralizer.
    A row's headline is not external text, but it is frequently MODEL-authored
    prose (a briefing headline, a plan step name), and `Frame.headline`'s
    validator refuses every markdown construct without caring where it came
    from. Constructing a Frame directly here would raise on an ordinary
    `**Board pack**` and cost the founder the card, so the text goes through
    `_plain` and `_clamp_headline` exactly as an email subject does.

    `kind` and `status` are the CALLER's decision, as in `frame_for_event`:
    they depend on what the row means, which the row alone does not say. They
    are never the model's.
    """
    text = _plain(headline)
    if not text:
        # Name what muldro knows rather than inventing a constant.
        text = f"{source} {entity_type}".strip()
    occurred = ensure_aware_utc(occurred_at) or datetime.now(timezone.utc)
    return Frame(
        key=frame_key(source, entity_type, entity_id),
        group_key=group_key,
        kind=kind,
        status=status,
        headline=_clamp_headline(text),
        source=source,
        entity_type=entity_type,
        occurred_at=occurred,
        updated_at=ensure_aware_utc(updated_at) or occurred,
        event_count=event_count,
        affordances=affordances or [],
    )
