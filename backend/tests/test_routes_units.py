"""The unit feed route returns typed Units and nothing else.

The old GET /v1/workspace/surfaces returned a push model whose `preview` and
`detail_config` were typed `Any` — so nothing on the wire had a shape. A Unit
is a frozen Pydantic model all the way down.
"""

from datetime import datetime, timezone
from types import SimpleNamespace

import pytest
from fastapi import HTTPException

from src.api.routes_units import DismissRequest, UnitFeedResponse, dismiss_unit, parse_frame_key
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


class _Result:
    def __init__(self, rows):
        self._rows = rows

    def scalars(self):
        return self

    def all(self):
        return self._rows

    def first(self):
        return self._rows[0] if self._rows else None


class _DB:
    def __init__(self, rows):
        self._rows = rows
        self.committed = False

    async def execute(self, stmt):
        return _Result(self._rows)

    async def commit(self):
        self.committed = True


class _Engagement:
    def __init__(self):
        self.calls = []

    async def record_engagement(self, signal_source, signal_category, action):
        self.calls.append((signal_source, signal_category, action))


async def test_a_dismissal_is_recorded_against_the_key_the_ranker_reads(monkeypatch):
    """build.py reads (frame.source, event.event_type). The old insight route
    wrote (source, f"perception_{source}") — the two never met."""
    eng = _Engagement()
    monkeypatch.setattr("src.api.routes_units.EngagementService", lambda db, ws: eng)
    db = _DB([SimpleNamespace(event_type="email_received")])

    result = await dismiss_unit(
        DismissRequest(frame_key="gmail:email_thread:t1"),
        user_id="usr_1",
        workspace_id="ws_1",
        db=db,
    )

    assert eng.calls == [("gmail", "email_received", "dismissed")]
    assert result.status == "dismissed"
    assert db.committed is True


async def test_a_muldro_own_row_cannot_be_dismissed(monkeypatch):
    """A run, a briefing and the review queue are muldro's own work. Dismissing
    one would write an engagement row keyed on a source the ranker never sees,
    and would teach demotion from a card that is not a perception signal."""
    eng = _Engagement()
    monkeypatch.setattr("src.api.routes_units.EngagementService", lambda db, ws: eng)
    with pytest.raises(HTTPException) as exc:
        await dismiss_unit(
            DismissRequest(frame_key="muldro:run:run_1"),
            user_id="usr_1",
            workspace_id="ws_1",
            db=_DB([]),
        )
    assert exc.value.status_code == 400
    assert eng.calls == []


async def test_a_malformed_key_is_a_400(monkeypatch):
    monkeypatch.setattr("src.api.routes_units.EngagementService", lambda db, ws: _Engagement())
    with pytest.raises(HTTPException) as exc:
        await dismiss_unit(
            DismissRequest(frame_key="nonsense"),
            user_id="usr_1",
            workspace_id="ws_1",
            db=_DB([]),
        )
    assert exc.value.status_code == 400


async def test_an_unknown_key_is_a_404_and_records_nothing(monkeypatch):
    """A caller who can tell 'not yours' from 'not found' can probe for the existence
    of another workspace's events one key at a time, so both answer 404."""
    eng = _Engagement()
    monkeypatch.setattr("src.api.routes_units.EngagementService", lambda db, ws: eng)
    with pytest.raises(HTTPException) as exc:
        await dismiss_unit(
            DismissRequest(frame_key="gmail:email_thread:ghost"),
            user_id="usr_1",
            workspace_id="ws_1",
            db=_DB([]),
        )
    assert exc.value.status_code == 404
    assert eng.calls == []
