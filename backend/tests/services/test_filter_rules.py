"""Filters the founder confirmed, applied where they cost nothing.

Applied at INGEST rather than in the connector query, deliberately. A query
filter (`-from:x` in the gmail search) is cheapest and irreversible: the cursor
is a timestamp watermark advanced past everything the query returned, so mail
excluded by the query sits permanently behind it — delete the rule a week later
and those messages are never ingested, ever. Reversible-looking in the product,
unrecoverable in fact.

At ingest the row is still written, so the filter is reversible, auditable, and
still visible to entity extraction. What is skipped is the EXPENSIVE work: the
batched LLM call, and everything the fold does downstream.
"""

from types import SimpleNamespace as N

import pytest

from src.services.filter_rules import (
    load_sender_rules,
    matching_rule_id,
    normalize_sender,
    sender_of,
)


class TestNormalizingAnAddress:
    @pytest.mark.parametrize(
        ("raw", "want"),
        [
            ("Axis Bank <Alerts@AxisBank.com>", "alerts@axisbank.com"),
            ("alerts@axisbank.com", "alerts@axisbank.com"),
            ("  ALERTS@AxisBank.COM  ", "alerts@axisbank.com"),
            ("<alerts@axisbank.com>", "alerts@axisbank.com"),
        ],
    )
    def test_one_canonical_form(self, raw, want):
        """Applied on write as well as read, so matching is a dict lookup."""
        assert normalize_sender(raw) == want

    @pytest.mark.parametrize("raw", [None, 42, {}, ""])
    def test_a_non_address_normalizes_to_nothing(self, raw):
        assert normalize_sender(raw) == ""


class TestReadingTheSender:
    def test_a_pre_ingest_raw_event(self):
        assert sender_of(N(actor={"email": "A@b.com"})) == "a@b.com"

    def test_a_stored_event_whose_actors_are_a_list(self):
        """Production stores `actor_entities` as a LIST despite the model
        annotating `dict | None` — the same split `event_actor_name` exists
        for."""
        assert sender_of(N(actor_entities=[{"email": "A@b.com"}])) == "a@b.com"

    @pytest.mark.parametrize(
        "event",
        [N(actor=None), N(actor_entities=[]), N(actor="nope"), N(actor={}), N()],
    )
    def test_anything_unreadable_is_no_sender(self, event):
        assert sender_of(event) == ""


class TestMatching:
    RULES = {("gmail", "alerts@axisbank.com"): "fltr_1"}

    def test_an_exact_match_names_the_rule(self):
        ev = N(source="gmail", actor={"email": "alerts@axisbank.com"})
        assert matching_rule_id(ev, self.RULES) == "fltr_1"

    def test_matching_is_case_insensitive(self):
        ev = N(source="gmail", actor={"email": "Alerts@AxisBank.com"})
        assert matching_rule_id(ev, self.RULES) == "fltr_1"

    def test_a_rule_is_scoped_to_its_source(self):
        """ "alerts@axisbank.com" means something in gmail and nothing in slack;
        an unscoped rule would claim authority it was never granted."""
        ev = N(source="slack", actor={"email": "alerts@axisbank.com"})
        assert matching_rule_id(ev, self.RULES) is None

    def test_no_domain_wildcarding(self):
        """The founder confirmed one address. Widening it here would exercise
        an authority they did not grant, on mail they never saw."""
        ev = N(source="gmail", actor={"email": "statements@axisbank.com"})
        assert matching_rule_id(ev, self.RULES) is None

    def test_no_prefix_matching(self):
        ev = N(source="gmail", actor={"email": "alerts@axisbank.com.evil.test"})
        assert matching_rule_id(ev, self.RULES) is None

    @pytest.mark.parametrize(
        "event",
        [N(source="gmail", actor=None), N(source="", actor={"email": "alerts@axisbank.com"}), N()],
    )
    def test_an_unmatchable_event_matches_nothing(self, event):
        assert matching_rule_id(event, self.RULES) is None

    def test_no_rules_means_no_match(self):
        assert matching_rule_id(N(source="gmail", actor={"email": "a@b.com"}), {}) is None


class TestLoadingIsTotal:
    async def test_a_read_failure_costs_the_filters_not_the_ingest(self):
        """Failing the other way would silently drop mail on a DB hiccup."""

        class _Boom:
            async def execute(self, *_a, **_k):
                raise RuntimeError("db down")

        assert await load_sender_rules(_Boom(), workspace_id="ws_1") == {}

    async def test_no_workspace_means_no_rules(self):
        assert await load_sender_rules(object(), workspace_id="") == {}


class TestTriageHonoursAConfirmedRule:
    """A founder-confirmed rule is the strongest rules-origin evidence there
    is — not a model's judgement about the mail, the founder's own standing
    instruction — so it belongs in the deterministic pass and not the LLM one.

    Everything follows from that placement: the Haiku call is skipped, the
    verdict is `actionable=False` so the view layer's fold already hides it,
    and the row is still written.
    """

    @staticmethod
    def _ev(email, source="gmail"):
        return N(
            source=source,
            actor={"email": email},
            raw_payload={"headers": {}},
            title="Some subject",
            summary="body",
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

        async def execute(self, *_a, **_k):
            return TestTriageHonoursAConfirmedRule._Result(self._rows)

    @staticmethod
    def _rule(rule_id="fltr_1", value="alerts@axisbank.com", source="gmail"):
        return N(rule_id=rule_id, match_value=value, source=source)

    @staticmethod
    def _llm_stub():
        """`_classify_llm` returns one TriageResult per event it was given —
        `triage_batch` zips them strictly, so a stub must honour the count."""
        from unittest.mock import AsyncMock

        from src.services.triage import TriageResult

        async def _fake(events, **_kw):
            return [
                TriageResult(
                    category="work_thread",
                    tier="full",
                    actionable=True,
                    importance_score=0.6,
                    urgency_score=0.5,
                    confidence_score=0.8,
                    origin="llm",
                )
                for _ in events
            ]

        return AsyncMock(side_effect=_fake)

    async def _triage(self, events, rows):
        from unittest.mock import patch

        from src.services.triage import TriageService

        svc = TriageService(self._DB(rows))
        llm = self._llm_stub()
        with patch.object(svc, "_classify_llm", llm):
            results = await svc.triage_batch(events, "u_1", "ws_1")
        return results, llm

    async def test_a_filtered_event_never_reaches_the_model(self):
        results, llm = await self._triage([self._ev("alerts@axisbank.com")], [self._rule()])
        llm.assert_not_awaited()
        assert results[0].origin == "rules"
        assert results[0].actionable is False
        assert results[0].tier == "skip"

    async def test_the_rule_that_fired_is_recorded(self):
        """Without this a verdict frozen at ingest outlives the rule that
        caused it, and "why is this hidden?" has no answer."""
        results, _ = await self._triage([self._ev("alerts@axisbank.com")], [self._rule()])
        assert results[0].filtered_by == "fltr_1"
        assert results[0].to_signals()["filtered_by"] == "fltr_1"

    async def test_an_unfiltered_event_still_reaches_the_model(self):
        _, llm = await self._triage([self._ev("dana@acme.com")], [self._rule()])
        llm.assert_awaited()

    async def test_no_rule_no_stamp(self):
        """Absence means "no rule touched this", not "a rule whose id we lost"."""
        results, _ = await self._triage([self._ev("dana@acme.com")], [])
        assert results[0].filtered_by is None
        assert "filtered_by" not in results[0].to_signals()

    async def test_a_filtered_event_is_distinguishable_from_marketing(self):
        """One is muldro reading a header, the other is the founder saying so."""
        from src.services.triage import FILTERED_CATEGORY

        results, _ = await self._triage([self._ev("alerts@axisbank.com")], [self._rule()])
        assert results[0].category == FILTERED_CATEGORY

    async def test_without_a_db_it_behaves_exactly_as_before(self):
        """Every caller that only wants classification passes no db."""
        from unittest.mock import patch

        from src.services.triage import TriageService

        svc = TriageService()
        llm = self._llm_stub()
        with patch.object(svc, "_classify_llm", llm):
            await svc.triage_batch([self._ev("alerts@axisbank.com")], "u_1", "ws_1")
        llm.assert_awaited()
