"""A poll that fetched only rows it had already stored has observed nothing.

Connectors re-fetch an overlap window on purpose (clock-skew insurance), so a
steady poll normally returns rows that are all already in normalized_events.
`ingest_raw_events` drops those from its summaries — they are not new
observations — and the cycle used to carry on regardless, handing the relevance
assessor and the Planner an EMPTY summary list under a header that still
counted the fetched rows.

The models said exactly what that felt like, in the briefing items they wrote:
"You have two new Gmail events. Without knowing sender, subject, or context,
they may be important". One of those every five minutes, plus an invented goal
about the same invisible events, at two model calls a time.
"""

from unittest.mock import AsyncMock, MagicMock

import pytest

from tests.conftest import TEST_USER_ID, TEST_WORKSPACE_ID, make_mock_settings
from tests.test_perception_librarian_gate import _make_orchestrator, _wire_common_mocks


def _wire(pr, *, fetched: int, ingested: list[str]):
    _wire_common_mocks(pr)
    pr._poller.poll = AsyncMock(
        return_value=([MagicMock(entity_id=None) for _ in range(fetched)], "cur", None, "opaque")
    )
    pr._poller.ingest_raw_events = AsyncMock(return_value=ingested)
    pr._invoker.call_agent = AsyncMock(return_value="planner output")


async def _run(pr, orch):
    return await orch.run_perception_cycle(
        source="gmail", user_id=TEST_USER_ID, workspace_id=TEST_WORKSPACE_ID
    )


class TestAllDuplicatesObserveNothing:
    @pytest.mark.asyncio
    async def test_no_model_call_when_every_fetched_row_was_already_stored(self):
        orch = _make_orchestrator(make_mock_settings())
        pr = orch._perception
        _wire(pr, fetched=2, ingested=[])

        result = await _run(pr, orch)

        assert result["events"] == 0
        assert result["fetched"] == 2
        pr._invoker.call_agent.assert_not_awaited()

    @pytest.mark.asyncio
    async def test_no_plan_is_queued_from_nothing(self):
        orch = _make_orchestrator(make_mock_settings())
        pr = orch._perception
        _wire(pr, fetched=3, ingested=[])

        await _run(pr, orch)

        pr._queue_perception_plan.assert_not_awaited()

    @pytest.mark.asyncio
    async def test_units_are_still_published_for_the_fetched_rows(self):
        """The cards are built from the events, not from who stored them first."""
        import src.orchestrator.perception_runner as pr_mod

        orch = _make_orchestrator(make_mock_settings())
        pr = orch._perception
        _wire(pr, fetched=2, ingested=[])
        published = AsyncMock()
        original = pr_mod.publish_perception_units
        pr_mod.publish_perception_units = published
        try:
            await _run(pr, orch)
        finally:
            pr_mod.publish_perception_units = original
        published.assert_awaited_once()


class TestPartialDedupStillObserves:
    @pytest.mark.asyncio
    async def test_one_new_row_among_duplicates_still_runs_the_cycle(self):
        orch = _make_orchestrator(make_mock_settings())
        pr = orch._perception
        _wire(pr, fetched=5, ingested=["[gmail] email_received: Series A term sheet"])

        result = await _run(pr, orch)

        assert result["events"] == 5
        called = [c.args[0] for c in pr._invoker.call_agent.call_args_list]
        assert "planner" in called

    @pytest.mark.asyncio
    async def test_the_header_counts_what_it_actually_lists(self):
        """It reported len(raw_events) over a list holding only the new ones,
        so it contradicted itself the moment anything deduped."""
        orch = _make_orchestrator(make_mock_settings())
        pr = orch._perception
        _wire(pr, fetched=5, ingested=["[gmail] email_received: Series A term sheet"])

        await _run(pr, orch)

        prompt = pr._invoker.call_agent.call_args.kwargs["message"]
        assert "1 new event(s)" in prompt
        assert "5 new event(s)" not in prompt
        assert "Series A term sheet" in prompt


class TestOnlyProseBecomesAnInsight:
    """`extract_plan` falls back to `PlanOutput(goal=response_text, ...)` when
    the Planner's reply will not parse — the entire raw output, kept as a
    diagnostic and already logged. This branch turns a goal into prose the
    founder reads, and a raw JSON blob reached the workspace as a card whose
    headline was "{".
    """

    @pytest.mark.parametrize(
        "goal",
        [
            '{\n  "goal": "Review the single new Gmail event"',
            '[{"step": 1}]',
            "```json\n{}\n```",
            '"goal": "x"',
            'Triage the inbox and then "steps": [{...}]',
            "ok",
            "",
            None,
        ],
    )
    def test_a_dump_is_not_an_insight(self, goal):
        from src.orchestrator.perception_runner import _is_publishable_insight

        assert _is_publishable_insight(goal) is False

    @pytest.mark.parametrize(
        "goal",
        [
            "Dana needs an answer on the liability cap before Tuesday.",
            "Two invoices are overdue and one is from a new counterparty.",
        ],
    )
    def test_a_sentence_is(self, goal):
        from src.orchestrator.perception_runner import _is_publishable_insight

        assert _is_publishable_insight(goal) is True
