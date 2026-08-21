"""External text reaches the screen only as an attributed quote.

Spec §2.1: the body slot never carries external provenance because external
values arrive on a different field that only code renders. This is the field.
"""

from datetime import datetime, timezone
from types import SimpleNamespace

from src.view.perception import quotes_from_events


def _event(snippet="Can you get back to me by Friday?", who="Sarah Chen", minute=0):
    return SimpleNamespace(
        source="gmail",
        entity_type="email_thread",
        entity_id="t_1",
        title="Series A term sheet",
        occurred_at=datetime(2026, 8, 21, 14, minute, tzinfo=timezone.utc),
        actor_entities={"name": who},
        raw_payload={"snippet": snippet},
    )


def test_a_quote_carries_the_text_verbatim():
    quotes = quotes_from_events([_event()])
    assert quotes[0].text == "Can you get back to me by Friday?"


def test_markdown_in_external_text_is_preserved_verbatim():
    """A quote is rendered by code as plain text, so it is NOT sanitized.

    Neutralizing here would misrepresent what the sender actually wrote. The
    renderer never passes a quote to a markdown renderer, which is where the
    safety lives.
    """
    quotes = quotes_from_events([_event(snippet="**URGENT** click here")])
    assert quotes[0].text == "**URGENT** click here"


def test_a_quote_names_its_author():
    assert quotes_from_events([_event(who="Sarah Chen")])[0].who == "Sarah Chen"


def test_an_unattributed_quote_is_dropped():
    """An unattributed quote is indistinguishable from muldro's own voice."""
    event = _event()
    event.actor_entities = None
    assert quotes_from_events([event]) == []


def test_an_empty_snippet_produces_no_quote():
    assert quotes_from_events([_event(snippet="")]) == []


def test_quotes_are_newest_last_and_capped_at_three():
    events = [_event(snippet=f"message {i}", minute=i) for i in range(6)]
    quotes = quotes_from_events(events)
    assert len(quotes) == 3
    assert quotes[-1].text == "message 5"


# --- Corrections beyond the plan ---


def test_actor_entities_as_a_list_is_supported():
    """Production stores actor_entities as a LIST of dicts (Task 3 finding).

    quotes_from_events must reuse frame.py's `_actor_name`, which already
    handles this shape, rather than a second divergent extractor that only
    understands a bare dict.
    """
    event = _event()
    event.actor_entities = [{"name": "Sarah Chen"}]
    quotes = quotes_from_events([event])
    assert quotes[0].who == "Sarah Chen"


def test_a_non_string_snippet_does_not_raise_and_produces_no_quote():
    """payload.get('snippet') may not be a string - a dict, list, or None.

    Calling .strip() on a non-string would raise and take down the whole
    perception tick. A malformed value is treated as absent, not fatal.
    """
    event = _event()
    event.raw_payload = {"snippet": {"nested": "not text"}}
    assert quotes_from_events([event]) == []


def test_a_list_valued_snippet_does_not_raise_and_produces_no_quote():
    event = _event()
    event.raw_payload = {"snippet": ["a", "b"]}
    assert quotes_from_events([event]) == []


def test_falls_back_to_body_when_snippet_is_missing():
    event = _event()
    event.raw_payload = {"body": "the real message text"}
    quotes = quotes_from_events([event])
    assert quotes[0].text == "the real message text"


def test_falls_back_to_body_when_snippet_is_non_string():
    """A malformed snippet does not poison a usable body - it falls through."""
    event = _event()
    event.raw_payload = {"snippet": None, "body": "the real message text"}
    quotes = quotes_from_events([event])
    assert quotes[0].text == "the real message text"


def test_a_non_string_body_does_not_raise_and_produces_no_quote():
    event = _event()
    event.raw_payload = {"snippet": None, "body": 12345}
    assert quotes_from_events([event]) == []


def test_an_event_with_no_real_timestamp_produces_no_quote():
    """A missing occurred_at must not become a fabricated attribution date.

    _occurred() never raises - it substitutes datetime.min UTC so ordering
    stays total - but rendering that sentinel as a quote's `when` would show
    a real human's words dated to year 1, which is worse than no quote at
    all. This mirrors the unattributed-quote rule: evidence that cannot be
    dated correctly is dropped rather than faked, same as evidence that
    cannot be attributed correctly.
    """
    event = _event()
    event.occurred_at = None
    assert quotes_from_events([event]) == []
