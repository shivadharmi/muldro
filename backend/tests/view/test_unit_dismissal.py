"""Dismissing a card hides it, and stops hiding it when the thing changes.

The dismiss endpoint used to write one row — an `engagement_history` signal
telling the RANKER "less of this kind". Nothing recorded "not THIS one", and
the feed is a pure projection of live rows, so the card came back on the next
poll fifteen seconds later. A write with no reader.

What the founder cleared is what they SAW. The re-surfacing rule below is the
whole design: hidden while `frame.updated_at <= dismissed_at`, back the moment
the thing moves past it. A reply on a dismissed thread is new information; the
same thread untouched is not. No timer, and no permanence.
"""

import asyncio
from datetime import datetime, timedelta, timezone

import pytest
from sqlalchemy import select

from src.config.settings import get_settings
from src.models.unit_dismissal import UnitDismissal
from src.services.unit_dismissals import dismiss, is_hidden, load_dismissals
from src.view.contracts import Frame, Unit
from src.view.feed import assemble_feed, drop_dismissed
from tests.conftest import make_test_db, seed_user_workspace

NOW = datetime(2026, 8, 22, 12, 0, tzinfo=timezone.utc)

DISMISS_USER_ID = "usr_01JDSM00000000000000000000"
DISMISS_WORKSPACE_ID = "ws_dismissal_test"


def _unit(key: str, *, updated_at: datetime | None = NOW, source: str = "gmail") -> Unit:
    return Unit(
        frame=Frame(
            key=key,
            kind="proposal",
            status="needs_you",
            headline=f"Thing {key}",
            source=source,
            entity_type="email_thread",
            occurred_at=NOW,
            updated_at=updated_at or NOW,
        ),
        body="",
    )


class TestIsHidden:
    def test_a_dismissed_thing_that_has_not_moved_is_hidden(self):
        unit = _unit("gmail:email_thread:t1", updated_at=NOW - timedelta(hours=1))
        assert is_hidden(unit, {"gmail:email_thread:t1": NOW}) is True

    def test_a_thing_updated_exactly_at_the_dismissal_is_hidden(self):
        """The boundary belongs to the dismissal: what the founder saw when they
        cleared the card is precisely the state stamped on it."""
        unit = _unit("gmail:email_thread:t1", updated_at=NOW)
        assert is_hidden(unit, {"gmail:email_thread:t1": NOW}) is True

    def test_a_thing_that_moved_after_the_dismissal_comes_back(self):
        """THE RE-SURFACING RULE. A dismissal hides what the founder SAW, not the
        thing for ever. A reply landed on the thread after they cleared it, so
        `frame.updated_at` is past `dismissed_at` and the card must reappear.

        Do not weaken this into a boolean or a timer. A boolean buries a thread
        the moment it goes quiet and keeps it buried through everything that
        happens next; a timer un-hides things nothing has happened to."""
        unit = _unit("gmail:email_thread:t1", updated_at=NOW + timedelta(seconds=1))
        assert is_hidden(unit, {"gmail:email_thread:t1": NOW}) is False

    def test_a_thing_nobody_dismissed_is_not_hidden(self):
        assert is_hidden(_unit("gmail:email_thread:t1"), {}) is False
        assert is_hidden(_unit("gmail:email_thread:t1"), {"gmail:email_thread:other": NOW}) is False

    def test_a_naive_updated_at_is_not_hidden(self):
        """An offset-less stamp does not name an instant, and `dismissed_at` is
        always UTC — ordering them would be decided by an unknown offset. Fails
        OPEN: showing a card the founder cleared beats swallowing one they did
        not."""
        unit = _unit("gmail:email_thread:t1", updated_at=datetime(2026, 8, 22, 11, 0))
        assert is_hidden(unit, {"gmail:email_thread:t1": NOW}) is False

    def test_an_undatable_unit_is_not_hidden_and_does_not_raise(self):
        class _Frame:
            key = "gmail:email_thread:t1"
            updated_at = None

        class _Undatable:
            frame = _Frame()

        assert is_hidden(_Undatable(), {"gmail:email_thread:t1": NOW}) is False
        assert is_hidden(object(), {"gmail:email_thread:t1": NOW}) is False


class _StubDB:
    async def execute(self, stmt):  # pragma: no cover - assemble_feed stubs the families
        raise AssertionError("assemble_feed must not query directly")


def _stub_families(monkeypatch, *, perception, runs=(), briefings=(), prepared=None):
    async def _stored(db, **kw):
        return list(perception), {}

    async def _runs(db, **kw):
        return list(runs)

    async def _briefings(db, **kw):
        return list(briefings)

    async def _prepared(db, **kw):
        return prepared

    async def _health(db, **kw):
        return None

    async def _insights(db, **kw):
        return []

    async def _bodies(units, **kw):
        return list(units)

    async def _features(units, **kw):
        return []

    monkeypatch.setattr("src.view.feed.stored_perception_units", _stored)
    monkeypatch.setattr("src.view.feed.run_units", _runs)
    monkeypatch.setattr("src.view.feed.briefing_units", _briefings)
    monkeypatch.setattr("src.view.feed.prepared_work_unit", _prepared)
    monkeypatch.setattr("src.view.feed.connector_health_unit", _health)
    monkeypatch.setattr("src.view.feed.insight_units", _insights)
    monkeypatch.setattr("src.view.feed.attach_stored_bodies", _bodies)
    monkeypatch.setattr("src.view.feed.build_features", _features)
    monkeypatch.setattr("src.view.feed.rank", lambda features: [])


def _dismissals(monkeypatch, rows):
    async def _load(db, **kw):
        return dict(rows)

    monkeypatch.setattr("src.view.feed.load_dismissals", _load)


class TestFeedDropsDismissed:
    async def test_the_feed_omits_a_dismissed_unit_and_keeps_the_rest(self, monkeypatch):
        _stub_families(
            monkeypatch,
            perception=[_unit("gmail:email_thread:t1"), _unit("gmail:email_thread:t2")],
        )
        _dismissals(monkeypatch, {"gmail:email_thread:t1": NOW})

        feed = await assemble_feed(
            _StubDB(), workspace_id="ws_1", user_id="usr_1", now=NOW + timedelta(minutes=1)
        )
        assert [u.frame.key for u in feed.units] == ["gmail:email_thread:t2"]

    async def test_a_dismissed_unit_that_moved_since_is_back_in_the_feed(self, monkeypatch):
        _stub_families(
            monkeypatch,
            perception=[_unit("gmail:email_thread:t1", updated_at=NOW + timedelta(hours=1))],
        )
        _dismissals(monkeypatch, {"gmail:email_thread:t1": NOW})

        feed = await assemble_feed(_StubDB(), workspace_id="ws_1", user_id="usr_1", now=NOW)
        assert [u.frame.key for u in feed.units] == ["gmail:email_thread:t1"]

    async def test_muldros_own_card_is_never_hidden_by_a_dismissal_row(self, monkeypatch):
        """The route refuses to dismiss one, so no such row should exist — but the
        review queue is the founder's only route to work muldro is holding, and a
        stray key must not be able to take it away."""
        _stub_families(
            monkeypatch,
            perception=[],
            runs=[_unit("muldro:run:run_1", source="muldro")],
            prepared=_unit("muldro:prepared_work:ws_1", source="muldro"),
        )
        _dismissals(
            monkeypatch,
            {"muldro:run:run_1": NOW, "muldro:prepared_work:ws_1": NOW},
        )

        feed = await assemble_feed(_StubDB(), workspace_id="ws_1", user_id="usr_1", now=NOW)
        assert {u.frame.key for u in feed.units} == {
            "muldro:run:run_1",
            "muldro:prepared_work:ws_1",
        }

    async def test_a_failing_dismissal_read_leaves_the_feed_intact(self, monkeypatch):
        """A card the founder dismissed is a far smaller failure than a blank
        workspace — the same posture the ranker's own outage takes."""
        _stub_families(
            monkeypatch,
            perception=[_unit("gmail:email_thread:t1"), _unit("gmail:email_thread:t2")],
        )

        async def _load(db, **kw):
            raise RuntimeError("dismissals are down")

        monkeypatch.setattr("src.view.feed.load_dismissals", _load)

        feed = await assemble_feed(_StubDB(), workspace_id="ws_1", user_id="usr_1", now=NOW)
        assert len(feed.units) == 2

    async def test_drop_dismissed_makes_no_second_query_when_there_is_nothing(self, monkeypatch):
        units = [_unit("gmail:email_thread:t1")]
        assert await drop_dismissed(_StubDB(), units, workspace_id="ws_1", user_id="usr_1") == units


def _db_reachable() -> bool:
    import asyncpg

    dsn = get_settings().database_url.replace("+asyncpg", "", 1)

    async def _probe() -> None:
        conn = await asyncpg.connect(dsn=dsn)
        try:
            await conn.execute("SELECT 1")
        finally:
            await conn.close()

    try:
        asyncio.run(_probe())
        return True
    except Exception:  # pragma: no cover
        return False


@pytest.mark.skipif(not _db_reachable(), reason="Postgres not reachable")
async def test_dismissing_twice_moves_the_stamp_and_leaves_one_row():
    """Against real Postgres, because the unique constraint and the upsert ARE
    the behaviour. A second row would leave the older, weaker stamp in play, so
    a thing that came back and was cleared again would resurface at once — and a
    dict-backed fake would agree with a version that did exactly that."""
    factory, engine = make_test_db()
    key = "gmail:email_thread:dismissal_test"
    later = NOW + timedelta(days=1)
    try:
        await seed_user_workspace(factory, DISMISS_USER_ID, DISMISS_WORKSPACE_ID)

        async with factory() as db:
            await dismiss(
                db,
                workspace_id=DISMISS_WORKSPACE_ID,
                user_id=DISMISS_USER_ID,
                frame_key=key,
                now=NOW,
            )
            await db.commit()

        async with factory() as db:
            await dismiss(
                db,
                workspace_id=DISMISS_WORKSPACE_ID,
                user_id=DISMISS_USER_ID,
                frame_key=key,
                now=later,
            )
            await db.commit()

        async with factory() as db:
            rows = (
                (
                    await db.execute(
                        select(UnitDismissal).where(
                            UnitDismissal.workspace_id == DISMISS_WORKSPACE_ID,
                            UnitDismissal.user_id == DISMISS_USER_ID,
                            UnitDismissal.frame_key == key,
                        )
                    )
                )
                .scalars()
                .all()
            )
            loaded = await load_dismissals(
                db, workspace_id=DISMISS_WORKSPACE_ID, user_id=DISMISS_USER_ID
            )

        assert len(rows) == 1
        assert rows[0].dismissed_at == later
        assert loaded[key] == later
        # And the rule reads it back: the thing as the founder last saw it is
        # hidden, the same thing after a reply is not.
        assert is_hidden(_unit(key, updated_at=later), loaded) is True
        assert is_hidden(_unit(key, updated_at=later + timedelta(seconds=1)), loaded) is False
    finally:
        # The workspace and user this test seeded go too. A dev database that
        # accumulates test principals stops being a place a red test can be
        # believed.
        try:
            from src.models.users import User, Workspace, WorkspaceMember

            async with factory() as db:
                await db.execute(
                    UnitDismissal.__table__.delete().where(
                        UnitDismissal.workspace_id == DISMISS_WORKSPACE_ID
                    )
                )
                await db.execute(
                    WorkspaceMember.__table__.delete().where(
                        WorkspaceMember.workspace_id == DISMISS_WORKSPACE_ID
                    )
                )
                await db.execute(
                    Workspace.__table__.delete().where(
                        Workspace.workspace_id == DISMISS_WORKSPACE_ID
                    )
                )
                await db.execute(User.__table__.delete().where(User.user_id == DISMISS_USER_ID))
                await db.commit()
        except Exception:  # pragma: no cover - teardown best-effort
            pass
        await engine.dispose()
