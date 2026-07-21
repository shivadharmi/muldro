"""EventProcessor batch scoring uses TriageService (Task 4)."""

import asyncio

from src.services.event_processor import EventProcessor, RawEvent
from tests.conftest import make_mock_settings


def _raw(title):
    return RawEvent(
        source="gmail",
        source_account_id="acc",
        event_type="email_received",
        entity_type="email",
        entity_id=f"e_{title}",
        title=title,
        summary="s",
        actor={"email": "x@y.com"},
        raw_payload={"headers": {"List-Unsubscribe": "<x>"}},
    )


def test_batch_scoring_writes_tier_to_importance_signals():
    proc = EventProcessor(settings=make_mock_settings(), db=None)
    events = [_raw("Sale A"), _raw("Sale B")]
    # all marketing → rules classify → no LLM call
    scores = asyncio.run(proc._score_events_batch(events, "user_1"))
    assert scores[0]["importance_signals"]["tier"] == "skip"
    assert scores[0]["importance_signals"]["category"] == "marketing"
    assert scores[0]["importance_score"] == 0.05
