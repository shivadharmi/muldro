"""Reading and writing the stored body, and deciding when it is stale.

The AsyncSession is a MagicMock: these are statement-shape and control-flow
assertions, not a database test.
"""

from datetime import datetime, timezone
from types import SimpleNamespace
from unittest.mock import AsyncMock, MagicMock

from src.view.body_store import is_current, load_bodies, save_body

NOW = datetime(2026, 8, 22, 14, 0, tzinfo=timezone.utc)


def _db(rows=()):
    db = MagicMock()
    result = MagicMock()
    result.scalars.return_value.all.return_value = list(rows)
    db.execute = AsyncMock(return_value=result)
    return db


def _row(frame_key="gmail:email_thread:t_1", body="prose", event_ids=("e1",)):
    return SimpleNamespace(frame_key=frame_key, body=body, event_ids=list(event_ids))


async def test_load_returns_rows_keyed_by_frame_key():
    db = _db([_row()])
    rows = await load_bodies(db, workspace_id="ws_1", frame_keys=["gmail:email_thread:t_1"])
    assert rows["gmail:email_thread:t_1"].body == "prose"


async def test_load_with_no_keys_makes_no_query():
    db = _db()
    assert await load_bodies(db, workspace_id="ws_1", frame_keys=[]) == {}
    assert db.execute.await_count == 0


async def test_load_is_scoped_to_one_workspace():
    """Every table here is workspace-scoped; a frame key alone is not an owner."""
    db = _db()
    await load_bodies(db, workspace_id="ws_1", frame_keys=["gmail:email_thread:t_1"])
    statement = str(db.execute.await_args.args[0]).lower()
    assert "unit_bodies.workspace_id =" in statement


async def test_load_asks_for_every_key_in_one_query():
    """One query for the whole poll, not one per unit."""
    db = _db()
    await load_bodies(db, workspace_id="ws_1", frame_keys=["a", "b", "c"])
    assert db.execute.await_count == 1
    assert "in (" in str(db.execute.await_args.args[0]).lower()


async def test_save_issues_one_statement():
    db = _db()
    await save_body(
        db,
        workspace_id="ws_1",
        frame_key="gmail:email_thread:t_1",
        body="prose",
        event_ids=("e1", "e2"),
        as_of=NOW,
    )
    assert db.execute.await_count == 1


async def test_save_upserts_rather_than_duplicating_a_thing():
    """One body per (workspace, thing). A second write must replace, not add."""
    db = _db()
    await save_body(
        db,
        workspace_id="ws_1",
        frame_key="gmail:email_thread:t_1",
        body="prose",
        event_ids=("e1",),
        as_of=NOW,
    )
    statement = str(db.execute.await_args.args[0]).lower()
    assert "on conflict" in statement
    assert "uq_unit_bodies_ws_frame" in statement


async def test_save_rewrites_the_events_the_body_was_written_over():
    """The replaced row must carry the NEW event set, or it is stale on arrival."""
    db = _db()
    await save_body(
        db,
        workspace_id="ws_1",
        frame_key="gmail:email_thread:t_1",
        body="prose",
        event_ids=("e1", "e2"),
        as_of=NOW,
    )
    set_clause = str(db.execute.await_args.args[0]).lower().split("do update set")[1]
    for column in ("body", "event_ids", "as_of"):
        assert f"{column} =" in set_clause


def test_a_row_written_over_the_same_events_is_current():
    assert is_current(_row(event_ids=("e1", "e2")), ("e1", "e2"))


def test_order_does_not_make_a_row_stale():
    """Comparison is by SET. The poll's grouping order is not information, and
    an order that shifted with paging would make every stored body look stale
    and force a regeneration that changed nothing."""
    assert is_current(_row(event_ids=("e1", "e2")), ("e2", "e1"))


def test_a_repeated_event_id_does_not_make_a_row_stale():
    """Set semantics, not multiset: identity is membership, not arrival count."""
    assert is_current(_row(event_ids=("e1", "e1", "e2")), ("e1", "e2"))


def test_a_new_message_makes_a_row_stale():
    assert not is_current(_row(event_ids=("e1",)), ("e1", "e2"))


def test_a_removed_event_makes_a_row_stale():
    assert not is_current(_row(event_ids=("e1", "e2")), ("e1",))


def test_a_row_with_no_recorded_events_is_stale():
    assert not is_current(_row(event_ids=()), ("e1",))


def test_a_missing_event_ids_value_is_stale():
    """A NULL column must never read as 'current' — that would pin bad prose up
    forever, since nothing would ever regenerate it."""
    assert not is_current(SimpleNamespace(event_ids=None), ("e1",))
