"""Group a poll's raw events into one group per thing.

The unit of perception was the POLL CYCLE: one PerceptionSignal whose summary
concatenated everything in that tick. Nothing could be "the same as" anything
else, so three polls of one inbox produced three cards. The unit is now the
thing the connector already named.
"""

from dataclasses import dataclass
from datetime import datetime, timezone
from typing import Any

from src.view.frame import frame_key

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

    Missing timestamps sort oldest (see _NO_TIMESTAMP above). A NAIVE
    timestamp is coerced to UTC rather than left alone: comparing a naive
    and an aware datetime raises the same TypeError a missing one would,
    and calendar.py already established the fix for this codebase - "so
    occurred_at is uniformly aware and downstream comparisons never raise
    on mixed naive/aware values." notion_connector.py is the live source of
    a naive value here: it parses last_edited_time with no normalization,
    so any timestamp lacking an offset reaches this function as-is.
    """
    value = getattr(event, "occurred_at", None)
    if value is None:
        return _NO_TIMESTAMP
    if value.tzinfo is None:
        return value.replace(tzinfo=timezone.utc)
    return value


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
