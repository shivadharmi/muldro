"""Group a poll's raw events into one group per thing.

The unit of perception was the POLL CYCLE: one PerceptionSignal whose summary
concatenated everything in that tick. Nothing could be "the same as" anything
else, so three polls of one inbox produced three cards. The unit is now the
thing the connector already named.
"""

import logging
from dataclasses import dataclass
from datetime import datetime, timezone
from typing import Any

from src.view.contracts import Quote, Unit
from src.view.frame import ensure_aware_utc, event_actor_name, frame_for_event, frame_key

logger = logging.getLogger(__name__)

# `NormalizedEvent.occurred_at` is NOT NULL at the DB level, but this module
# takes any object with the right attributes - including pre-ingest RawEvents
# that have not been timestamped yet (github_connector.py never sets
# occurred_at on a RawEvent at all). Treating a missing timestamp as the
# oldest possible value keeps ordering total (never raises) and keeps it
# correct: an event with no known time can never outrank one that actually
# has a time, so `latest` still means "the newest event that has a time"
# whenever any event in the group has one at all.
_NO_TIMESTAMP = datetime.min.replace(tzinfo=timezone.utc)


@dataclass(frozen=True)
class EventGroup:
    """Every event seen this poll for one durable thing."""

    key: str
    events: tuple[Any, ...]

    @property
    def latest(self) -> Any:
        """The newest event - it supplies the headline and the timestamp."""
        return self.events[-1]

    @property
    def event_count(self) -> int:
        return len(self.events)


def _occurred(event: Any) -> datetime:
    """Never raises, always returns a tz-aware datetime.

    The coercion itself is `frame.py::ensure_aware_utc` - the view layer's
    ONE timestamp policy - not a second implementation of it. This function
    adds exactly one thing on top: an ORDERING sentinel. A value the policy
    calls absent sorts oldest (see _NO_TIMESTAMP above) so `sorted` never
    raises on a mixed naive/aware list and `latest` still means "the newest
    event that has a time" whenever any event in the group has one.

    The sentinel is for ordering ONLY. Anything that DISPLAYS a timestamp
    must ask `ensure_aware_utc` directly and treat None as absent - see
    `quotes_from_events`, which drops a quote it cannot date rather than
    showing the sentinel.
    """
    return ensure_aware_utc(getattr(event, "occurred_at", None)) or _NO_TIMESTAMP


def group_events_by_key(events: list[Any]) -> list[EventGroup]:
    """Group by (source, entity_type, entity_id), newest group first.

    Events within a group are ordered oldest-first so `latest` is the last.
    """
    buckets: dict[str, list[Any]] = {}
    for event in events:
        key = frame_key(event.source, event.entity_type, event.entity_id)
        buckets.setdefault(key, []).append(event)

    groups = [
        EventGroup(key=key, events=tuple(sorted(items, key=_occurred)))
        for key, items in buckets.items()
    ]
    groups.sort(key=lambda g: _occurred(g.latest), reverse=True)
    return groups


# The card shows one quote and the Full shows the thread; more than a handful
# on the Unit is payload with no reader.
MAX_QUOTES = 3


# Which sources carry VERBATIM, HUMAN-AUTHORED text, and on which field.
#
# A source absent from this map yields NO quote. That is deliberate and it is
# the fail-closed half of the same rule that drops an unattributed quote: a
# Quote is external text shown under a named human's name, so guessing which
# field holds it risks attributing MULDRO's own composed prose to a person -
# a misattribution, which is worse than a missing quote.
#
# The three sources deliberately absent, and what each puts in `summary`:
#   calendar - f"{title} from {start} to {end} with {attendees}" (calendar.py)
#   github   - f"{reason}: {title} in {repo}"     (github_connector.py)
#   notion   - f"Notion page: {title}"            (notion_connector.py)
# All three are muldro's own composition over structured fields; not one word
# of any of them was typed by the human whose name the quote would carry.
#
# A NEW CONNECTOR: answer this question explicitly before adding a line here -
# which field, if any, holds text a person actually wrote? "None" is a valid
# and common answer, and it is the default.
VERBATIM_TEXT_FIELD: dict[str, str] = {
    "gmail": "summary",  # _snippet_of(message)[:500] - the message snippet
    "slack": "summary",  # text[:500] - the message text
}

# Read only when the source's declared field is empty. No connector writes
# either key today - gmail's raw_payload carries message_id/labels/headers and
# slack's carries channel_id/channel_name/ts - so this is a fallback for
# shapes that DO carry one (a future connector, a hand-built event), never the
# production path.
_PAYLOAD_TEXT_KEYS = ("snippet", "body")


def _quote_text(event: Any) -> str:
    """The verbatim external text on an event, or "" when there isn't any.

    Both reads are untrusted external shape - a summary, snippet or body field
    can be a dict, a list, or None just as easily as a string (malformed
    upstream data, a connector schema change). Calling `.strip()` on a
    non-string would raise and take the whole perception tick down with it, so
    a non-string value is treated as absent rather than fatal and falls
    through to the next candidate exactly as an empty one would.
    """
    source = getattr(event, "source", None)
    if not isinstance(source, str):
        return ""
    field = VERBATIM_TEXT_FIELD.get(source)
    if field is None:
        return ""

    value = getattr(event, field, None)
    if isinstance(value, str) and value.strip():
        return value.strip()

    payload = getattr(event, "raw_payload", None)
    if not isinstance(payload, dict):
        return ""
    for key in _PAYLOAD_TEXT_KEYS:
        candidate = payload.get(key)
        if isinstance(candidate, str) and candidate.strip():
            return candidate.strip()
    return ""


def quotes_from_events(events: list[Any]) -> list[Quote]:
    """Lift external text off events into attributed quotes, newest last.

    Text is carried VERBATIM - neutralizing it would misrepresent what the
    sender wrote. Safety comes from the renderer never passing a quote to a
    markdown renderer. An unattributed quote is dropped: with no name on it,
    it is indistinguishable from muldro's own voice.

    An event with no real `occurred_at` is dropped too, for the same reason
    as an unattributed one: `_occurred()` substitutes `datetime.min` UTC so
    *ordering* stays total, but a Quote is evidence shown next to a real
    human's name, and dating a person's words to year 1 is a fabricated
    attribution, not a harmless default - worse than the quote not
    appearing at all.

    "No real timestamp" is decided by `ensure_aware_utc`, NOT by testing the
    raw attribute against None. The two are not the same question: a
    non-datetime `occurred_at` - a string, an int, whatever a malformed
    external payload carried - is not None, but it is just as undatable, and
    an `is None` test would let it through to be rendered as the year-1
    sentinel. The policy that decides a timestamp is absent must be the same
    one that produced the sentinel, or the guard protects against one half of
    what the sentinel covers.

    Which field holds that text is a per-source FACT, declared in
    `VERBATIM_TEXT_FIELD`; an unlisted source yields nothing. See that map
    for why silence rather than a guess.

    KNOWN LIMITATION - quotes can only be built PRE-INGEST, from RawEvents.
    `NormalizedEvent` has no payload column and keeps only `title` and
    `summary`, so a Unit rebuilt from stored rows can quote gmail and slack
    (whose verbatim text IS the summary) and nothing richer - no second
    message of a thread, no body beyond the connector's truncation. Carrying
    quotes past ingest is the Unit transport work's problem to solve, not
    this function's.

    `who` is read via frame.py's `event_actor_name`, not a second extractor:
    production stores `actor_entities` as a LIST of dicts despite the model
    annotating `dict | None` (Task 3), and a pre-ingest RawEvent calls the
    field `actor` instead. That helper is where both shapes are already
    handled, and sharing it is what keeps a quote's attribution and a
    headline's from disagreeing about who wrote something.
    """
    quotes: list[Quote] = []
    for event in sorted(events, key=_occurred):
        when = ensure_aware_utc(getattr(event, "occurred_at", None))
        if when is None:
            continue
        text = _quote_text(event)
        if not text:
            continue
        who = event_actor_name(event)
        if not who:
            continue
        quotes.append(Quote(text=text, who=who, when=when))
    return quotes[-MAX_QUOTES:]


def units_from_events(events: list[Any]) -> list[Unit]:
    """Turn one poll's events into one Unit per thing, newest first.

    `body` is left empty: the frame and the quotes are code's, and the body
    is the model's, written in a later step against the frame's kind budget.

    One malformed thing costs one Unit, never the poll. `Frame` is a
    validating model - `headline` caps at 200 characters and refuses blank,
    and `source` refuses empty - so an ordinary long email subject is enough
    to raise inside `frame_for_event`. Letting that propagate would lose
    every OTHER card in the same poll, which is the outcome frame.py already
    rejected when it chose to clamp a hostile `importance_score` rather than
    raise on it.
    """
    units: list[Unit] = []
    for group in group_events_by_key(list(events)):
        try:
            frame = frame_for_event(
                group.latest,
                kind="proposal",
                status="needs_you",
                event_count=group.event_count,
                updated_at=_occurred(group.latest),
            )
            quotes = quotes_from_events(list(group.events))
        except Exception as exc:  # noqa: BLE001 - one bad thing must not cost the poll
            logger.warning(
                "view_unit_build_failed key=%s error=%s",
                group.key,
                exc,
                extra={"frame_key": group.key, "error": str(exc)},
            )
            continue
        units.append(Unit(frame=frame, body="", quotes=tuple(quotes)))
    return units
