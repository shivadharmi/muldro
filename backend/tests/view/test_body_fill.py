"""A model call is spent only on a unit whose prose is actually out of date.

`load_bodies`, `save_body` and `generate_body` are patched in body_fill's own
namespace, so these tests are about the DECISION — which units cost money —
and not about SQL or providers.
"""

from datetime import datetime, timezone
from types import SimpleNamespace
from unittest.mock import AsyncMock, MagicMock, patch

from src.view.body_fill import attach_stored_bodies, fill_bodies
from src.view.body_generator import BodyUnavailable
from src.view.contracts import Frame, Unit

NOW = datetime(2026, 8, 22, 14, 0, tzinfo=timezone.utc)


def _unit(entity_id="t_1", count=1):
    return Unit(
        frame=Frame(
            key=f"gmail:email_thread:{entity_id}",
            kind="proposal",
            status="needs_you",
            headline="Sarah Chen - Series A term sheet",
            source="gmail",
            entity_type="email_thread",
            occurred_at=NOW,
            updated_at=NOW,
            event_count=count,
        ),
        body="",
    )


def _row(body="stored prose", event_ids=("e1",)):
    return SimpleNamespace(body=body, event_ids=list(event_ids))


def _patched(*, rows=None, generated="fresh prose", generate=None):
    """Patch the three seams fill_bodies depends on."""
    return (
        patch("src.view.body_fill.load_bodies", AsyncMock(return_value=rows or {})),
        patch("src.view.body_fill.save_body", AsyncMock()),
        patch(
            "src.view.body_fill.generate_body",
            generate or AsyncMock(return_value=generated),
        ),
    )


async def test_a_body_written_over_the_same_events_costs_nothing():
    unit = _unit()
    rows = {unit.frame.key: _row(event_ids=("e1", "e2"))}
    generate = AsyncMock()
    load, save, gen = _patched(rows=rows, generate=generate)
    with load, save, gen:
        out = await fill_bodies(
            [unit],
            db=MagicMock(),
            workspace_id="ws_1",
            ids_by_key={unit.frame.key: ("e2", "e1")},
            now=NOW,
        )
    assert out[0].body == "stored prose"
    assert generate.await_count == 0


async def test_a_new_message_buys_new_prose():
    unit = _unit(count=2)
    rows = {unit.frame.key: _row(event_ids=("e1",))}
    save = AsyncMock()
    with (
        patch("src.view.body_fill.load_bodies", AsyncMock(return_value=rows)),
        patch("src.view.body_fill.save_body", save),
        patch("src.view.body_fill.generate_body", AsyncMock(return_value="fresh prose")),
    ):
        out = await fill_bodies(
            [unit],
            db=MagicMock(),
            workspace_id="ws_1",
            ids_by_key={unit.frame.key: ("e1", "e2")},
            now=NOW,
        )
    assert out[0].body == "fresh prose"
    assert save.await_count == 1


async def test_a_thing_with_no_stored_body_gets_one():
    unit = _unit()
    load, save, gen = _patched()
    with load, save, gen:
        out = await fill_bodies(
            [unit],
            db=MagicMock(),
            workspace_id="ws_1",
            ids_by_key={unit.frame.key: ("e1",)},
            now=NOW,
        )
    assert out[0].body == "fresh prose"


async def test_a_give_up_is_persisted_so_it_is_not_retried_every_poll():
    """The empty string is a RESULT, not a missing value.

    A body the model could not fit in its budget is deterministic for this
    event set: three more calls next poll would produce the same nothing.
    Treating "" as falsy and skipping the save would regenerate it on every
    poll for ever, at three calls a time, and no test would go red.
    """
    unit = _unit()
    save = AsyncMock()
    with (
        patch("src.view.body_fill.load_bodies", AsyncMock(return_value={})),
        patch("src.view.body_fill.save_body", save),
        patch("src.view.body_fill.generate_body", AsyncMock(return_value="")),
    ):
        out = await fill_bodies(
            [unit],
            db=MagicMock(),
            workspace_id="ws_1",
            ids_by_key={unit.frame.key: ("e1",)},
            now=NOW,
        )
    assert out[0].body == ""
    assert save.await_count == 1
    assert save.await_args.kwargs["body"] == ""
    assert save.await_args.kwargs["event_ids"] == ("e1",)
    assert save.await_args.kwargs["frame_key"] == unit.frame.key


async def test_a_persisted_give_up_is_not_regenerated_on_the_next_poll():
    """The saved empty body must read back as current, or the save bought nothing."""
    unit = _unit()
    rows = {unit.frame.key: _row(body="", event_ids=("e1",))}
    generate = AsyncMock()
    with (
        patch("src.view.body_fill.load_bodies", AsyncMock(return_value=rows)),
        patch("src.view.body_fill.save_body", AsyncMock()),
        patch("src.view.body_fill.generate_body", generate),
    ):
        out = await fill_bodies(
            [unit],
            db=MagicMock(),
            workspace_id="ws_1",
            ids_by_key={unit.frame.key: ("e1",)},
            now=NOW,
        )
    assert out[0].body == ""
    assert generate.await_count == 0


async def test_a_transient_failure_persists_nothing_and_keeps_what_was_there():
    unit = _unit()
    rows = {unit.frame.key: _row(body="older prose", event_ids=("e1",))}
    save = AsyncMock()
    with (
        patch("src.view.body_fill.load_bodies", AsyncMock(return_value=rows)),
        patch("src.view.body_fill.save_body", save),
        patch(
            "src.view.body_fill.generate_body",
            AsyncMock(side_effect=BodyUnavailable("429")),
        ),
    ):
        out = await fill_bodies(
            [unit],
            db=MagicMock(),
            workspace_id="ws_1",
            ids_by_key={unit.frame.key: ("e1", "e2")},
            now=NOW,
        )
    assert out[0].body == "older prose"
    assert save.await_count == 0


async def test_one_units_failure_does_not_cost_the_next_unit_its_body():
    first, second = _unit("a"), _unit("b")
    with (
        patch("src.view.body_fill.load_bodies", AsyncMock(return_value={})),
        patch("src.view.body_fill.save_body", AsyncMock()),
        patch(
            "src.view.body_fill.generate_body",
            AsyncMock(side_effect=[BodyUnavailable("429"), "fresh prose"]),
        ),
    ):
        out = await fill_bodies(
            [first, second],
            db=MagicMock(),
            workspace_id="ws_1",
            ids_by_key={first.frame.key: ("e1",), second.frame.key: ("e2",)},
            now=NOW,
        )
    assert [u.body for u in out] == ["", "fresh prose"]


async def test_a_burst_is_capped_and_the_cap_prefers_the_units_it_was_given_first():
    units = [_unit("a"), _unit("b"), _unit("c")]
    generate = AsyncMock(return_value="fresh prose")
    with (
        patch("src.view.body_fill.MAX_BODIES_PER_POLL", 1),
        patch("src.view.body_fill.load_bodies", AsyncMock(return_value={})),
        patch("src.view.body_fill.save_body", AsyncMock()),
        patch("src.view.body_fill.generate_body", generate),
    ):
        out = await fill_bodies(
            units,
            db=MagicMock(),
            workspace_id="ws_1",
            ids_by_key={u.frame.key: ("e1",) for u in units},
            now=NOW,
        )
    assert generate.await_count == 1
    assert [u.body for u in out] == ["fresh prose", "", ""]


async def test_what_the_cap_dropped_is_logged_rather_than_silently_omitted(caplog):
    """A silent truncation reads as "covered everything" when it did not."""
    units = [_unit("a"), _unit("b"), _unit("c")]
    with (
        caplog.at_level("INFO", logger="src.view.body_fill"),
        patch("src.view.body_fill.MAX_BODIES_PER_POLL", 1),
        patch("src.view.body_fill.load_bodies", AsyncMock(return_value={})),
        patch("src.view.body_fill.save_body", AsyncMock()),
        patch("src.view.body_fill.generate_body", AsyncMock(return_value="fresh prose")),
    ):
        await fill_bodies(
            units,
            db=MagicMock(),
            workspace_id="ws_1",
            ids_by_key={u.frame.key: ("e1",) for u in units},
            now=NOW,
        )
    deferred = [r for r in caplog.records if "view_body_deferred" in r.getMessage()]
    assert len(deferred) == 2
    assert units[1].frame.key in deferred[0].getMessage()
    assert units[2].frame.key in deferred[1].getMessage()


async def test_over_the_cap_a_stale_body_is_kept_rather_than_blanked():
    """Prose written over the first two messages is still muldro's honest
    account of them, and the frame's own count and timestamp say what changed.
    Blanking it would make prose flicker off a card between polls."""
    units = [_unit("a"), _unit("b")]
    rows = {units[1].frame.key: _row(body="older prose", event_ids=("e1",))}
    with (
        patch("src.view.body_fill.MAX_BODIES_PER_POLL", 1),
        patch("src.view.body_fill.load_bodies", AsyncMock(return_value=rows)),
        patch("src.view.body_fill.save_body", AsyncMock()),
        patch("src.view.body_fill.generate_body", AsyncMock(return_value="fresh prose")),
    ):
        out = await fill_bodies(
            units,
            db=MagicMock(),
            workspace_id="ws_1",
            ids_by_key={u.frame.key: ("e1", "e2") for u in units},
            now=NOW,
        )
    assert out[1].body == "older prose"


async def test_a_current_body_does_not_consume_the_burst_cap():
    """The cap counts calls made, not units seen — otherwise a settled inbox
    would push a genuinely new thread past the ceiling for nothing."""
    settled, fresh = _unit("a"), _unit("b")
    rows = {settled.frame.key: _row(body="stored prose", event_ids=("e1",))}
    generate = AsyncMock(return_value="fresh prose")
    with (
        patch("src.view.body_fill.MAX_BODIES_PER_POLL", 1),
        patch("src.view.body_fill.load_bodies", AsyncMock(return_value=rows)),
        patch("src.view.body_fill.save_body", AsyncMock()),
        patch("src.view.body_fill.generate_body", generate),
    ):
        out = await fill_bodies(
            [settled, fresh],
            db=MagicMock(),
            workspace_id="ws_1",
            ids_by_key={settled.frame.key: ("e1",), fresh.frame.key: ("e2",)},
            now=NOW,
        )
    assert [u.body for u in out] == ["stored prose", "fresh prose"]
    assert generate.await_count == 1


async def test_the_frame_and_quotes_are_carried_through_untouched():
    unit = _unit()
    load, save, gen = _patched()
    with load, save, gen:
        out = await fill_bodies(
            [unit],
            db=MagicMock(),
            workspace_id="ws_1",
            ids_by_key={unit.frame.key: ("e1",)},
            now=NOW,
        )
    assert out[0].frame == unit.frame
    assert out[0].quotes == unit.quotes


async def test_no_units_makes_no_call_and_no_query():
    load = AsyncMock(return_value={})
    generate = AsyncMock()
    with (
        patch("src.view.body_fill.load_bodies", load),
        patch("src.view.body_fill.save_body", AsyncMock()),
        patch("src.view.body_fill.generate_body", generate),
    ):
        assert await fill_bodies([], db=MagicMock(), workspace_id="ws_1", ids_by_key={}) == []
    assert load.await_count == 0
    assert generate.await_count == 0


class TestAttachStoredBodies:
    """The feed READS prose; it never generates it.

    Every test here also asserts `generate_body` was not called, because the
    whole point of a separate read path is that a page refresh costs nothing.
    """

    async def test_stored_body_reaches_an_empty_unit(self):
        rows = {"gmail:email_thread:t_1": _row(body="stored prose")}
        with (
            patch("src.view.body_fill.load_bodies", AsyncMock(return_value=rows)),
            patch("src.view.body_fill.generate_body", AsyncMock()) as gen,
        ):
            out = await attach_stored_bodies([_unit()], db=MagicMock(), workspace_id="ws_1")
        assert out[0].body == "stored prose"
        gen.assert_not_awaited()

    async def test_a_unit_with_its_own_body_is_never_overwritten(self):
        """The briefing writes its own text; a stored row must not replace it.

        An EMPTY unit sits alongside it on purpose: with only the body-carrying
        unit in the list the early return fires and the loop's guard is never
        reached, so the test would pass with the guard removed.
        """
        own = Unit(frame=_unit("t_1").frame, body="the briefing's own words")
        empty = _unit("t_2")
        rows = {
            "gmail:email_thread:t_1": _row(body="stored prose"),
            "gmail:email_thread:t_2": _row(body="prose for the empty one"),
        }
        with (
            patch("src.view.body_fill.load_bodies", AsyncMock(return_value=rows)),
            patch("src.view.body_fill.generate_body", AsyncMock()),
        ):
            out = await attach_stored_bodies([own, empty], db=MagicMock(), workspace_id="ws_1")
        assert out[0].body == "the briefing's own words"
        assert out[1].body == "prose for the empty one"

    async def test_a_body_carrying_unit_alone_costs_no_query(self):
        own = Unit(frame=_unit().frame, body="the briefing's own words")
        with patch("src.view.body_fill.load_bodies", AsyncMock()) as load:
            out = await attach_stored_bodies([own], db=MagicMock(), workspace_id="ws_1")
        assert out[0].body == "the briefing's own words"
        load.assert_not_awaited()

    async def test_a_unit_with_no_stored_row_keeps_its_empty_body(self):
        with (
            patch("src.view.body_fill.load_bodies", AsyncMock(return_value={})),
            patch("src.view.body_fill.generate_body", AsyncMock()) as gen,
        ):
            out = await attach_stored_bodies([_unit()], db=MagicMock(), workspace_id="ws_1")
        assert out[0].body == ""
        gen.assert_not_awaited()

    async def test_a_stale_body_is_shown_rather_than_blanked(self):
        """`is_current` is deliberately not consulted on the read path."""
        rows = {"gmail:email_thread:t_1": _row(body="prose about message one", event_ids=("e1",))}
        unit = _unit(count=2)  # a second message has since arrived
        with (
            patch("src.view.body_fill.load_bodies", AsyncMock(return_value=rows)),
            patch("src.view.body_fill.generate_body", AsyncMock()) as gen,
        ):
            out = await attach_stored_bodies([unit], db=MagicMock(), workspace_id="ws_1")
        assert out[0].body == "prose about message one"
        gen.assert_not_awaited()

    async def test_an_empty_stored_body_leaves_the_unit_empty(self):
        """The repair cap's give-up is persisted as "" and must not be 'filled'."""
        rows = {"gmail:email_thread:t_1": _row(body="")}
        with patch("src.view.body_fill.load_bodies", AsyncMock(return_value=rows)):
            out = await attach_stored_bodies([_unit()], db=MagicMock(), workspace_id="ws_1")
        assert out[0].body == ""

    async def test_a_read_failure_costs_the_prose_and_not_the_feed(self):
        with patch(
            "src.view.body_fill.load_bodies", AsyncMock(side_effect=RuntimeError("db down"))
        ):
            out = await attach_stored_bodies([_unit()], db=MagicMock(), workspace_id="ws_1")
        assert len(out) == 1
        assert out[0].body == ""

    async def test_no_units_means_no_query(self):
        with patch("src.view.body_fill.load_bodies", AsyncMock()) as load:
            assert await attach_stored_bodies([], db=MagicMock(), workspace_id="ws_1") == []
        load.assert_not_awaited()

    async def test_order_is_preserved(self):
        units = [_unit("t_1"), _unit("t_2"), _unit("t_3")]
        rows = {"gmail:email_thread:t_2": _row(body="only the middle one")}
        with patch("src.view.body_fill.load_bodies", AsyncMock(return_value=rows)):
            out = await attach_stored_bodies(units, db=MagicMock(), workspace_id="ws_1")
        assert [u.frame.key for u in out] == [u.frame.key for u in units]
        assert [u.body for u in out] == ["", "only the middle one", ""]
