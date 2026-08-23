"""External text reaches the screen only as an attributed quote.

The body slot never carries external provenance, because external values
arrive on a different field that only code renders. This is the field.

Two shapes are exercised below and they are NOT equally real. The
``_event`` fixture hand-writes ``raw_payload={"snippet": ...}``; no connector
in this codebase writes that key, so those cases pin the FALLBACK branch. The
"real connector output" section at the bottom drives the actual normalizers
and is the path production takes.
"""

from datetime import datetime, timezone
from types import SimpleNamespace

from src.connectors.calendar import CalendarConnector
from src.connectors.gmail import GmailConnector
from src.connectors.notion_connector import NotionConnector
from src.connectors.slack_connector import SlackConnector
from src.view.perception import quotes_from_events
from tests.conftest import TEST_USER_ID, make_mock_settings


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


def test_a_non_datetime_timestamp_produces_no_quote_either():
    """The same rule, decided by the same policy rather than by `is None`.

    `occurred_at` arrives from external payloads, so it can be a string or an
    int as easily as it can be missing. `ensure_aware_utc` calls all three
    absent and `_occurred` gives all three the year-1 ordering sentinel - so
    a guard that tested the raw attribute against None would drop the missing
    one and render the malformed one dated to year 1, which is the fabricated
    attribution this rule exists to prevent.
    """
    for value in ("2026-08-21T14:00:00Z", 1755784800, object()):
        event = _event()
        event.occurred_at = value
        assert quotes_from_events([event]) == [], value


# --- Real connector output --------------------------------------------------
#
# Everything above drives a hand-written `raw_payload={"snippet": ...}`. That
# shape is nobody's: gmail's raw_payload carries message_id / labels / headers,
# slack's carries channel_id / channel_name / ts, and `NormalizedEvent` has no
# raw_payload column at all. The tests below drive the connectors' own
# normalizers, so they fail if the field a source actually puts verbatim text
# on ever moves.


def _gmail_raw(text="Can you get back to me by Friday?", sender="Sarah Chen <sarah@acme.com>"):
    """One OpenConnector Gmail DTO through the real normalizer.

    `preview` is the schema-declared optional text object (see
    tests/test_gmail_connector.py's header for the recorded outputSchema).
    `sender` is an RFC 5322 From - a display name plus an address - which is
    what a real inbox mostly carries; see the bare-address test below for what
    happens when the display name is absent.
    """
    conn = GmailConnector(settings=make_mock_settings(), caller=None)
    return conn._to_event(
        {
            "messageId": "msg_001",
            "threadId": "thr_001",
            "labelIds": ["INBOX", "UNREAD"],
            "subject": "Series A term sheet",
            "sender": sender,
            "to": "founder@muldro.example",
            "messageTimestamp": "2026-08-21T14:00:00Z",
            "preview": {"text": text},
        }
    )


def _slack_raw(text="Can you review the deck before standup?"):
    return SlackConnector._normalize_message(
        {"text": text, "user": "U08SARAH", "ts": "1755784800.000100"},
        "C01FOUNDERS",
        "founders",
    )


def test_a_real_gmail_event_produces_an_attributed_quote():
    """The regression this section exists for: gmail's raw_payload has no
    `snippet` and no `body` key, so reading only those produced zero quotes on
    every real event. The snippet lands on `summary`."""
    quotes = quotes_from_events([_gmail_raw()])
    assert len(quotes) == 1
    assert quotes[0].text == "Can you get back to me by Friday?"
    assert quotes[0].who == "Sarah Chen"
    assert quotes[0].when == datetime(2026, 8, 21, 14, 0, tzinfo=timezone.utc)


def test_a_gmail_sender_with_no_display_name_keeps_its_quote():
    """A bare `From` must still attribute, because most senders are bare.

    gmail.py splits the RFC 5322 `From` and falls back to the address's LOCAL
    PART when there is no display name. The full address could not be used:
    `frame.py::_plain` strips one (the headline validator refuses it), so
    writing the whole From into `actor["name"]` yielded "" - which dropped the
    counterparty from the headline AND, because an unattributed quote is
    discarded by design, dropped this quote entirely.
    """
    raw = _gmail_raw(sender="sarah@acme.com")
    assert raw.actor == {"type": "person", "email": "sarah@acme.com", "name": "sarah"}
    quotes = quotes_from_events([raw])
    assert len(quotes) == 1
    assert quotes[0].text == "Can you get back to me by Friday?"
    assert quotes[0].who == "sarah"


def test_a_gmail_sender_with_a_display_name_is_attributed_to_the_person():
    """The other half of the pair: a display name is never discarded for the
    local part it sits next to."""
    raw = _gmail_raw(sender="Sarah Chen <sarah@acme.com>")
    assert raw.actor == {"type": "person", "email": "sarah@acme.com", "name": "Sarah Chen"}
    assert quotes_from_events([raw])[0].who == "Sarah Chen"


def test_a_noreply_gmail_sender_is_still_named():
    """The bare-address case is not an edge: every no-reply notification is
    one, and they are exactly the senders a founder needs to identify."""
    raw = _gmail_raw(sender="noreply@acme.com")
    assert quotes_from_events([raw])[0].who == "noreply"


def test_a_real_slack_event_produces_an_attributed_quote():
    quotes = quotes_from_events([_slack_raw()])
    assert len(quotes) == 1
    assert quotes[0].text == "Can you review the deck before standup?"
    # slack_connector puts the slack user id in actor["name"] - an account,
    # which is an allowed `who`: naming the account beats an unattributed quote.
    assert quotes[0].who == "U08SARAH"


def test_markdown_in_a_real_gmail_event_is_still_verbatim():
    quotes = quotes_from_events([_gmail_raw(text="**URGENT** [click](http://x.example)")])
    assert quotes[0].text == "**URGENT** [click](http://x.example)"


def test_a_real_calendar_event_is_never_quoted_because_its_summary_is_muldros_prose():
    """calendar's `summary` is f"{title} from {start} to {end} with {attendees}"
    - composed by muldro, not typed by a human. Quoting it under the
    organizer's name would put muldro's own words in a person's mouth, which
    is a misattribution, not a missing feature."""
    raw = CalendarConnector._normalize_event(
        {
            "id": "cal_001",
            "status": "confirmed",
            "summary": "Board meeting",
            "start": {"dateTime": "2026-08-21T14:00:00Z"},
            "end": {"dateTime": "2026-08-21T15:00:00Z"},
            "organizer": {"email": "sarah@acme.com", "displayName": "Sarah Chen"},
            "attendees": [{"displayName": "Sarah Chen"}],
        },
        TEST_USER_ID,
    )
    assert "Board meeting from" in raw.summary  # the composed string exists
    assert quotes_from_events([raw]) == []


def test_a_real_notion_event_is_never_quoted_because_its_summary_is_muldros_prose():
    """notion's `summary` is f"Notion page: {title}"."""
    raw = NotionConnector._normalize_page(
        {
            "id": "page_001",
            "url": "https://notion.so/page_001",
            "created_time": "2026-08-20T10:00:00.000Z",
            "last_edited_time": "2026-08-21T14:00:00.000Z",
            "last_edited_by": {"type": "person", "name": "Sarah Chen"},
            "properties": {"title": {"title": [{"plain_text": "Fundraise plan"}]}},
        }
    )
    assert raw.summary == "Notion page: Fundraise plan"
    assert quotes_from_events([raw]) == []


def test_an_unknown_source_produces_no_quote():
    """Fail closed. A connector nobody has mapped yet must stay silent rather
    than have this module guess which of its fields a human actually wrote."""
    event = SimpleNamespace(
        source="linear",
        entity_type="issue",
        entity_id="ENG-1",
        title="Card opens to nothing",
        occurred_at=datetime(2026, 8, 21, 14, 0, tzinfo=timezone.utc),
        actor={"name": "Sarah Chen"},
        summary="whatever linear decides to put here",
        raw_payload={"snippet": "and whatever it puts here"},
    )
    assert quotes_from_events([event]) == []


def test_a_blank_source_produces_no_quote():
    event = _event()
    event.source = ""
    assert quotes_from_events([event]) == []


def test_a_non_dict_raw_payload_does_not_raise():
    """raw_payload is external shape like everything else in it."""
    event = _event()
    event.raw_payload = "not a dict"
    assert quotes_from_events([event]) == []
