"""The unit feed route returns typed Units and nothing else.

The old GET /v1/workspace/surfaces returned WorkspaceSurfacePush, whose
`preview` and `detail_config` are typed `Any` — so nothing on the wire had a
shape. A Unit is a frozen Pydantic model all the way down.
"""

from datetime import datetime, timezone

from src.api.routes_units import UnitFeedResponse, parse_frame_key
from src.view.contracts import Frame, Unit

NOW = datetime(2026, 8, 22, 12, 0, tzinfo=timezone.utc)


def _unit(key="gmail:email_thread:t1") -> Unit:
    return Unit(
        frame=Frame(
            key=key,
            kind="proposal",
            status="needs_you",
            headline="Sarah Chen - Series A term sheet",
            source="gmail",
            entity_type="email_thread",
            occurred_at=NOW,
            updated_at=NOW,
        ),
        body="",
    )


def test_the_response_carries_typed_units():
    resp = UnitFeedResponse(units=[_unit()], count=1)
    assert resp.units[0].frame.headline == "Sarah Chen - Series A term sheet"


def test_the_response_serializes_the_frame_verbatim():
    dumped = UnitFeedResponse(units=[_unit()], count=1).model_dump(mode="json")
    frame = dumped["units"][0]["frame"]
    assert frame["key"] == "gmail:email_thread:t1"
    assert frame["event_count"] == 1
    assert frame["affordances"] == []


def test_a_frame_key_splits_into_three_on_the_first_two_colons():
    """entity_id may contain colons; source and entity_type may not."""
    assert parse_frame_key("gmail:email_thread:t1") == ("gmail", "email_thread", "t1")


def test_an_entity_id_containing_colons_survives():
    assert parse_frame_key("calendar:meeting:a:b:c") == ("calendar", "meeting", "a:b:c")


def test_a_malformed_frame_key_is_refused():
    assert parse_frame_key("gmail") is None
    assert parse_frame_key("gmail:email_thread") is None
    assert parse_frame_key("") is None
    assert parse_frame_key(":x:y") is None
