"""Muldro's own rows become Units: runs, briefings, the prepared queue.

The old builder made `run` and `alert` two surface kinds from one table.
FrameKind has one `run`; FrameStatus carries the difference. Two cards of one
kind are the same shape by construction — which is what ranking needs.
"""

from datetime import datetime, timezone
from types import SimpleNamespace

from src.view.domain_units import (
    briefing_units,
    connector_health_unit,
    prepared_work_unit,
    run_headline,
    run_unit,
    run_units,
)

NOW = datetime(2026, 8, 22, 12, 0, tzinfo=timezone.utc)


def _run(run_id="run_01A", status="running", **kw):
    """A TaskRun row. NOTE: TaskRun has no `goal` column — the goal is on Plan."""
    defaults = dict(
        run_id=run_id,
        plan_id="plan_01A",
        status=status,
        source="background",
        workspace_id="ws_1",
        started_at=datetime(2026, 8, 22, 11, 0, tzinfo=timezone.utc),
        updated_at=datetime(2026, 8, 22, 11, 30, tzinfo=timezone.utc),
        completed_at=None,
        created_at=datetime(2026, 8, 22, 11, 0, tzinfo=timezone.utc),
        error=None,
    )
    defaults.update(kw)
    return SimpleNamespace(**defaults)


class _Result:
    def __init__(self, rows):
        self._rows = rows

    def scalars(self):
        return self

    def all(self):
        return self._rows


class _DB:
    def __init__(self, rows, step_rows=()):
        self._rows = rows
        self._step_rows = list(step_rows)
        self._calls = 0

    async def execute(self, stmt):
        self._calls += 1
        return _Result(self._rows if self._calls == 1 else self._step_rows)


# ── run_headline: the naming rules, tested without a database ──────────


def test_the_plans_goal_names_the_run():
    assert run_headline(plan_goal="Draft the investor update", step_name=None) == (
        "Draft the investor update"
    )


def test_the_first_step_name_is_the_fallback():
    assert run_headline(plan_goal=None, step_name="Fetch the Q3 numbers") == (
        "Fetch the Q3 numbers"
    )


def test_the_goal_wins_over_the_step_name():
    assert run_headline(plan_goal="Ship it", step_name="step one") == "Ship it"


def test_with_neither_it_returns_empty_rather_than_the_word_Run():  # noqa: N802
    """The old builder fell back to the literal 'Run', so every unnamed run read
    alike — the many-identical-cards failure in miniature. Empty lets
    frame_for_row name what muldro actually knows: `muldro run`."""
    assert run_headline(plan_goal=None, step_name=None) == ""
    assert run_headline(plan_goal="  ", step_name="") == ""


# ── run_unit: the frame shaping, also without a database ───────────────


def test_a_running_run_is_a_run_frame_with_running_status():
    unit = run_unit(_run(), headline="Draft the investor update")
    assert unit.frame.kind == "run"
    assert unit.frame.status == "running"


def test_the_key_names_the_run():
    unit = run_unit(_run(run_id="run_01A"), headline="x")
    assert unit.frame.key == "muldro:run:run_01A"


def test_a_failed_run_is_the_same_kind_with_a_failed_status():
    """The old builder made this a second surface kind. Status carries it now."""
    unit = run_unit(_run(status="failed"), headline="x")
    assert unit.frame.kind == "run"
    assert unit.frame.status == "failed"


def test_a_run_awaiting_approval_needs_you():
    assert run_unit(_run(status="awaiting_approval"), headline="x").frame.status == "needs_you"


def test_a_blocked_run_needs_you():
    assert run_unit(_run(status="blocked"), headline="x").frame.status == "needs_you"


def test_a_markdown_goal_is_neutralized_rather_than_losing_the_card():
    unit = run_unit(_run(), headline="**Ship** the [pack](https://x.example)")
    assert "**" not in unit.frame.headline
    assert "https://" not in unit.frame.headline
    assert unit.frame.headline


def test_a_run_with_no_headline_still_produces_a_card():
    assert run_unit(_run(), headline="").frame.headline == "muldro run"


def test_a_failed_run_quotes_nothing():
    """The error is muldro's own prose. A Quote is a named human's words."""
    unit = run_unit(_run(status="failed", error={"message": "connector timeout"}), headline="x")
    assert unit.quotes == ()


def test_the_body_is_empty_until_the_generator_lands():
    assert run_unit(_run(), headline="x").body == ""


def test_an_unshapeable_row_costs_its_own_card_and_returns_None():  # noqa: N802
    assert run_unit(SimpleNamespace(), headline="x") is None


# ── run_units: the query wrapper, only its totality ────────────────────


async def test_run_units_returns_one_unit_per_row():
    units = await run_units(
        _DB([(_run(), "Draft the investor update")]), workspace_id="ws_1", now=NOW
    )
    assert [u.frame.key for u in units] == ["muldro:run:run_01A"]
    assert units[0].frame.headline == "Draft the investor update"


async def test_run_units_falls_back_to_the_first_step_name():
    db = _DB(
        [(_run(), None)],
        step_rows=[SimpleNamespace(run_id="run_01A", name="Fetch the Q3 numbers")],
    )
    units = await run_units(db, workspace_id="ws_1", now=NOW)
    assert units[0].frame.headline == "Fetch the Q3 numbers"


async def test_a_read_failure_costs_the_run_family_only():
    class _Boom:
        async def execute(self, stmt):
            raise RuntimeError("no")

    assert await run_units(_Boom(), workspace_id="ws_1", now=NOW) == []


async def test_a_step_name_read_failure_still_yields_cards():
    """A missing headline is a worse card; a raise is no card at all."""

    class _HalfBoom:
        def __init__(self):
            self._calls = 0

        async def execute(self, stmt):
            self._calls += 1
            if self._calls == 1:
                return _Result([(_run(), None)])
            raise RuntimeError("step read failed")

    units = await run_units(_HalfBoom(), workspace_id="ws_1", now=NOW)
    assert [u.frame.headline for u in units] == ["muldro run"]


# ── briefing_units / prepared_work_unit / connector_health_unit ────────


def _briefing(**kw):
    defaults = dict(
        briefing_id="brf_01A",
        headline="Three things need you today",
        briefing_date=datetime(2026, 8, 22, tzinfo=timezone.utc).date(),
        created_at=datetime(2026, 8, 22, 7, 0, tzinfo=timezone.utc),
        full_text="Long body.",
    )
    defaults.update(kw)
    return SimpleNamespace(**defaults)


def _approval(approval_id="apr_01A", risk_level="high", **kw):
    defaults = dict(
        approval_id=approval_id,
        risk_level=risk_level,
        created_at=datetime(2026, 8, 22, 10, 0, tzinfo=timezone.utc),
        title="Send the term sheet reply",
    )
    defaults.update(kw)
    return SimpleNamespace(**defaults)


async def test_a_briefing_is_a_briefing_frame():
    units = await briefing_units(_DB([_briefing()]), workspace_id="ws_1", user_id="usr_1")
    assert units[0].frame.kind == "briefing"
    assert units[0].frame.key == "muldro:briefing:brf_01A"


async def test_a_markdown_briefing_headline_is_neutralized_not_dropped():
    units = await briefing_units(
        _DB([_briefing(headline="**Three** things")]), workspace_id="ws_1", user_id="usr_1"
    )
    assert units[0].frame.headline == "Three things"


async def test_no_briefing_yields_no_unit():
    assert await briefing_units(_DB([]), workspace_id="ws_1", user_id="usr_1") == []


async def test_prepared_work_is_a_singleton_keyed_on_the_workspace():
    unit = await prepared_work_unit(_DB([_approval()]), workspace_id="ws_1", user_id="usr_1")
    assert unit is not None
    assert unit.frame.key == "muldro:prepared_work:ws_1"
    assert unit.frame.status == "needs_you"


async def test_prepared_work_counts_what_is_waiting():
    unit = await prepared_work_unit(
        _DB([_approval("a"), _approval("b")]), workspace_id="ws_1", user_id="usr_1"
    )
    assert unit.frame.event_count == 2


async def test_an_empty_queue_is_absent_rather_than_a_card_announcing_idleness():
    assert await prepared_work_unit(_DB([]), workspace_id="ws_1", user_id="usr_1") is None


async def test_prepared_work_offers_a_code_authored_affordance():
    """An affordance names a real capability, and is labelled in code."""
    unit = await prepared_work_unit(_DB([_approval()]), workspace_id="ws_1", user_id="usr_1")
    assert [a.capability for a in unit.frame.affordances] == ["internal.approve_action"]
    assert unit.frame.affordances[0].label == "Review"


async def test_failing_sources_become_one_record_unit_with_a_real_key():
    """`rec_{i}` was the canonical example of an id that resolves to nothing."""
    state = SimpleNamespace(source="gmail", circuit_state="open")
    unit = await connector_health_unit(_DB([state]), workspace_id="ws_1")
    assert unit is not None
    assert unit.frame.key == "muldro:connector_health:ws_1"
    assert unit.frame.kind == "record"
    assert unit.frame.status == "failed"


async def test_healthy_connectors_produce_no_card():
    assert await connector_health_unit(_DB([]), workspace_id="ws_1") is None


class TestFinishedRunsStaySeeable:
    """A success that vanishes the moment it lands is work nobody can see.

    Only active and failed runs surfaced, so autonomous work appeared while it
    ran and then disappeared — the workspace could not answer "what did you do
    overnight" and the only record was a briefing generated at 07:00.
    """

    def test_completed_maps_to_done_not_to_running(self):
        from src.view.domain_units import _RUN_STATUS

        assert _RUN_STATUS["completed"] == "done"

    def test_a_contradicted_read_back_wants_a_human(self):
        """`partially_completed` is neither a success nor a clean failure."""
        from src.view.domain_units import _RUN_STATUS

        assert _RUN_STATUS["partially_completed"] == "needs_you"

    def test_a_timeout_reads_as_a_failure(self):
        from src.view.domain_units import _RUN_STATUS

        assert _RUN_STATUS["timed_out"] == "failed"

    def test_every_lingering_status_has_a_frame_status(self):
        """The map must be exhaustive over what the query selects, or a row
        falls through to the "running" default and a finished run reads live."""
        from src.view.domain_units import (
            _ACTIVE,
            _RECENT_DONE,
            _RECENT_FAILED,
            _RUN_STATUS,
        )

        for status in (*_ACTIVE, *_RECENT_FAILED, *_RECENT_DONE):
            assert status in _RUN_STATUS, status

    def test_a_success_is_reported_not_chased(self):
        """Shorter window than a failure, long enough to span a night."""
        from src.view.domain_units import COMPLETED_RUN_WINDOW_HOURS, FAILED_RUN_WINDOW_HOURS

        assert COMPLETED_RUN_WINDOW_HOURS < FAILED_RUN_WINDOW_HOURS
        assert COMPLETED_RUN_WINDOW_HOURS >= 8  # an overnight


class TestInsightUnits:
    """What muldro CONCLUDED, not what it merely saw.

    The soul's initiative sequence is observe -> interpret -> surface. The view
    layer had observe and surface and no home for interpret: a conclusion went
    into a `briefing_item` memory and waited for a briefing generated at 07:00,
    which on a workspace with no briefing row yet meant never.
    """

    def test_the_headline_is_the_first_claim_not_a_truncation(self):
        from src.view.domain_units import _insight_headline

        text = (
            "Dana needs an answer on the liability cap before Tuesday. "
            "She has written twice and the board meets that morning."
        )
        assert _insight_headline(text) == (
            "Dana needs an answer on the liability cap before Tuesday."
        )

    def test_a_short_leading_fragment_does_not_become_the_headline(self):
        """ "Re. " is a sentence end by punctuation and a nonsense headline."""
        from src.view.domain_units import _insight_headline

        assert _insight_headline("Re. the renewal, legal wants $2M.").startswith("Re. the renewal")

    def test_a_single_line_with_no_terminator_is_used_whole(self):
        from src.view.domain_units import _insight_headline

        assert _insight_headline("Two invoices are overdue") == "Two invoices are overdue"

    def test_only_the_first_line_is_considered(self):
        from src.view.domain_units import _insight_headline

        assert _insight_headline("The claim.\nSupporting detail.") == "The claim."

    def test_blank_text_yields_a_blank_headline_rather_than_raising(self):
        from src.view.domain_units import _insight_headline

        assert _insight_headline("") == ""
        assert _insight_headline("   \n  ") == ""

    def test_a_conclusion_is_a_finding_and_muldros_own(self):
        """`finding` carries the largest lede budget of any kind, because
        research and synthesis are legitimately long."""
        from src.view.body import LEDE_BUDGETS

        assert "finding" in LEDE_BUDGETS
        assert LEDE_BUDGETS["finding"] >= LEDE_BUDGETS["proposal"]

    def test_the_window_cannot_outlive_the_memory_it_reads(self):
        """These memories carry ttl_days=1; a wider window would select rows
        that expire out from under the feed."""
        from src.view.domain_units import INSIGHT_WINDOW_HOURS

        assert 0 < INSIGHT_WINDOW_HOURS <= 24
