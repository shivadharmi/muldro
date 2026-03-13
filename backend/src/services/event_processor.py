"""Event Processor — normalize, score, and deduplicate incoming events.

Responsibilities:
- Receive raw events from connectors
- Normalize to NormalizedEvent schema
- Score importance/urgency/confidence
- Deduplicate by idempotency key
- Store and trigger downstream processing
"""

from dataclasses import dataclass


@dataclass
class RawEvent:
    source: str
    source_account_id: str
    event_type: str
    entity_type: str
    entity_id: str
    title: str | None = None
    summary: str | None = None
    actor: dict | None = None
    raw_payload: dict | None = None


class EventProcessor:
    """Process raw events into normalized, scored events."""

    async def process(self, raw: RawEvent, user_id: str) -> str | None:
        """Process a raw event. Returns event_id if stored, None if duplicate."""
        # TODO: Implement
        # 1. Generate idempotency key from source + entity_id + event_type
        # 2. Check for duplicates
        # 3. Score importance/urgency/confidence
        # 4. Store as NormalizedEvent
        # 5. Trigger planner if importance > threshold
        return None
