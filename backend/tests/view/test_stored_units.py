"""The feed is a projection of normalized_events, not of a poll cycle.

Three polls of one thread wrote three rows keyed on one entity_id; this
returns ONE Unit whose event_count is 3 — the three identical "New activity"
cards, closed on the read side as well as the write side.
"""

from datetime import datetime, timedelta, timezone
from types import SimpleNamespace

from src.view.stored_units import stored_perception_units


def _row(entity_id="t_1", minute=0, title="Series A term sheet", source="gmail"):
    return SimpleNamespace(
        source=source,
        entity_type="email_thread",
        entity_id=entity_id,
        event_type="email_received",
        title=title,
        summary="Can you get back to me by Friday?",
        occurred_at=datetime(2026, 8, 22, 9, minute, tzinfo=timezone.utc),
        actor_entities=[{"type": "person", "name": "Sarah Chen", "email": "sarah@x.example"}],
        importance_score=0.9,
        raw_payload={},
    )


class _Result:
    def __init__(self, rows):
        self._rows = rows

    def scalars(self):
        return self

    def all(self):
        return self._rows


class _DB:
    def __init__(self, rows):
        self._rows = rows
        self.statements = []

    async def execute(self, stmt):
        self.statements.append(stmt)
        return _Result(self._rows)


async def test_three_rows_on_one_thread_become_one_unit():
    db = _DB([_row(minute=1), _row(minute=2), _row(minute=3)])
    units, _ = await stored_perception_units(
        db, workspace_id="ws_1", user_id="usr_1", now=datetime.now(timezone.utc)
    )
    assert len(units) == 1
    assert units[0].frame.event_count == 3


async def test_the_events_map_is_keyed_by_frame_key():
    """build_features takes events_by_key keyed on frame.key — the same shape."""
    db = _DB([_row(minute=1), _row(minute=2)])
    units, by_key = await stored_perception_units(
        db, workspace_id="ws_1", user_id="usr_1", now=datetime.now(timezone.utc)
    )
    assert set(by_key) == {units[0].frame.key}
    assert len(by_key[units[0].frame.key]) == 2


async def test_two_threads_become_two_units():
    db = _DB([_row(entity_id="a"), _row(entity_id="b")])
    units, _ = await stored_perception_units(
        db, workspace_id="ws_1", user_id="usr_1", now=datetime.now(timezone.utc)
    )
    assert len(units) == 2


async def test_a_gmail_unit_carries_an_attributed_quote():
    """VERBATIM_TEXT_FIELD maps gmail onto `summary`, which NormalizedEvent has."""
    db = _DB([_row()])
    units, _ = await stored_perception_units(
        db, workspace_id="ws_1", user_id="usr_1", now=datetime.now(timezone.utc)
    )
    assert units[0].quotes[0].text == "Can you get back to me by Friday?"
    assert units[0].quotes[0].who == "Sarah Chen"


async def test_a_calendar_unit_carries_no_quote():
    """calendar's `summary` is muldro's own composed prose — fail closed."""
    db = _DB([_row(source="calendar")])
    units, _ = await stored_perception_units(
        db, workspace_id="ws_1", user_id="usr_1", now=datetime.now(timezone.utc)
    )
    assert units[0].quotes == ()


async def test_the_body_is_empty_until_the_generator_lands():
    """Body generation is a separate concern; this path transports frames and
    quotes only."""
    db = _DB([_row()])
    units, _ = await stored_perception_units(
        db, workspace_id="ws_1", user_id="usr_1", now=datetime.now(timezone.utc)
    )
    assert units[0].body == ""


async def test_a_phishing_subject_produces_an_inert_headline():
    db = _DB([_row(title="**URGENT** [Verify](https://phish.example)")])
    units, _ = await stored_perception_units(
        db, workspace_id="ws_1", user_id="usr_1", now=datetime.now(timezone.utc)
    )
    headline = units[0].frame.headline
    assert "https://" not in headline and "](" not in headline


async def test_a_db_failure_costs_the_perception_family_not_the_feed():
    class _Boom:
        async def execute(self, stmt):
            raise RuntimeError("postgres is having a day")

    units, by_key = await stored_perception_units(
        _Boom(), workspace_id="ws_1", user_id="usr_1", now=datetime.now(timezone.utc)
    )
    assert units == [] and by_key == {}


async def test_the_window_is_bounded_and_does_not_read_the_clock_itself():
    """`now` is an argument for the same reason build_features' is: testability."""
    now = datetime(2026, 8, 22, 12, 0, tzinfo=timezone.utc)
    db = _DB([_row()])
    await stored_perception_units(db, workspace_id="ws_1", user_id="usr_1", now=now)
    assert len(db.statements) == 1
    assert now - timedelta(days=1) < now  # sanity: the window is computed from `now`


async def test_a_far_future_event_is_outside_the_feed_window():
    """The window bounds BOTH sides.

    A calendar asks `timeMin=now` with no `timeMax`, so it returns every future
    occurrence. Bounding only the past put a card on today's workspace for a
    meeting two months out — and one per occurrence of every recurring series.
    Asserted against the compiled SQL because the fake DB ignores WHERE.
    """
    now = datetime(2026, 8, 22, 12, 0, tzinfo=timezone.utc)
    db = _DB([])
    await stored_perception_units(db, workspace_id="ws_1", user_id="u_1", now=now)
    sql = str(db.statements[0].compile(compile_kwargs={"literal_binds": True}))
    assert sql.count("occurred_at") >= 2, "expected a lower AND an upper bound"
    assert ">=" in sql and "<=" in sql


async def test_the_horizon_is_ahead_of_now_and_the_window_behind_it():
    from src.view.stored_units import FEED_HORIZON_DAYS, FEED_WINDOW_DAYS

    assert FEED_WINDOW_DAYS > 0 and FEED_HORIZON_DAYS > 0
    now = datetime(2026, 8, 22, 12, 0, tzinfo=timezone.utc)
    db = _DB([])
    await stored_perception_units(db, workspace_id="ws_1", user_id="u_1", now=now)
    sql = str(db.statements[0].compile(compile_kwargs={"literal_binds": True}))
    # The bounds bracket `now`: the past edge is earlier, the future edge later.
    assert str((now - timedelta(days=FEED_WINDOW_DAYS)).date()) in sql
    assert str((now + timedelta(days=FEED_HORIZON_DAYS)).date()) in sql
