"""The feed is ordered by rank(), not by which builder ran first.

Server order used to be the order builders ran in, client order was arrival
order, and a dense CSS grid repacked both. Three independent non-decisions,
stacked. This is the decision.
"""

from datetime import datetime, timezone

import pytest

from src.view.contracts import Frame, Unit
from src.view.feed import assemble_feed, order_by_rank

NOW = datetime(2026, 8, 22, 12, 0, tzinfo=timezone.utc)


def _unit(key: str) -> Unit:
    return Unit(
        frame=Frame(
            key=key,
            kind="proposal",
            status="needs_you",
            headline=f"Thing {key}",
            source="gmail",
            entity_type="email_thread",
            occurred_at=NOW,
            updated_at=NOW,
        ),
        body="",
    )


def test_order_by_rank_follows_the_ranked_keys():
    units = [_unit("a"), _unit("b"), _unit("c")]
    assert [u.frame.key for u in order_by_rank(units, ["c", "a", "b"])] == ["c", "a", "b"]


def test_a_key_rank_dropped_is_dropped_from_the_feed():
    """rank() drops `suppressed` items on purpose — the founder dismissed that
    (source, category) five times running. Re-appending them undoes the demotion."""
    units = [_unit("a"), _unit("b")]
    assert [u.frame.key for u in order_by_rank(units, ["a"])] == ["a"]


def test_a_ranked_key_with_no_unit_is_ignored_rather_than_raising():
    assert [u.frame.key for u in order_by_rank([_unit("a")], ["ghost", "a"])] == ["a"]


def test_order_by_rank_is_total_on_duplicate_keys():
    """A handle names one thing; the first occurrence wins, as rank() decides."""
    units = [_unit("a"), _unit("a")]
    assert len(order_by_rank(units, ["a"])) == 1


class _StubDB:
    async def execute(self, stmt):  # pragma: no cover - assemble_feed stubs the families
        raise AssertionError("assemble_feed must not query directly")


async def test_assemble_feed_ranks_and_never_raises(monkeypatch):
    perception = [_unit("gmail:email_thread:t1")]
    runs = [_unit("muldro:run:run_1")]

    async def _stored(db, **kw):
        return perception, {}

    async def _runs(db, **kw):
        return runs

    async def _briefings(db, **kw):
        return []

    async def _prepared(db, **kw):
        return None

    async def _health(db, **kw):
        return None

    async def _features(units, **kw):
        raise RuntimeError("ranker is down")

    monkeypatch.setattr("src.view.feed.stored_perception_units", _stored)
    monkeypatch.setattr("src.view.feed.run_units", _runs)
    monkeypatch.setattr("src.view.feed.briefing_units", _briefings)
    monkeypatch.setattr("src.view.feed.prepared_work_unit", _prepared)
    monkeypatch.setattr("src.view.feed.connector_health_unit", _health)
    monkeypatch.setattr("src.view.feed.build_features", _features)

    units = (await assemble_feed(_StubDB(), workspace_id="ws_1", user_id="usr_1", now=NOW)).units
    # A ranker outage must not blank the workspace: the deterministic
    # composition order stands in.
    assert {u.frame.key for u in units} == {
        "gmail:email_thread:t1",
        "muldro:run:run_1",
    }


async def test_assemble_feed_puts_every_family_in(monkeypatch):
    async def _stored(db, **kw):
        return [_unit("gmail:email_thread:t1")], {}

    async def _runs(db, **kw):
        return [_unit("muldro:run:run_1")]

    async def _briefings(db, **kw):
        return [_unit("muldro:briefing:brf_1")]

    async def _prepared(db, **kw):
        return _unit("muldro:prepared_work:ws_1")

    async def _health(db, **kw):
        return _unit("muldro:connector_health:ws_1")

    async def _features(units, **kw):
        return []

    def _rank(features):
        return []

    monkeypatch.setattr("src.view.feed.stored_perception_units", _stored)
    monkeypatch.setattr("src.view.feed.run_units", _runs)
    monkeypatch.setattr("src.view.feed.briefing_units", _briefings)
    monkeypatch.setattr("src.view.feed.prepared_work_unit", _prepared)
    monkeypatch.setattr("src.view.feed.connector_health_unit", _health)
    monkeypatch.setattr("src.view.feed.build_features", _features)
    monkeypatch.setattr("src.view.feed.rank", _rank)

    units = (await assemble_feed(_StubDB(), workspace_id="ws_1", user_id="usr_1", now=NOW)).units
    assert len(units) == 5


@pytest.mark.parametrize("bad", [None, "nope", 42])
def test_order_by_rank_refuses_nothing_and_returns_a_list(bad):
    """Totality: a malformed ranked list must not blank the feed."""
    units = [_unit("a")]
    assert order_by_rank(units, [bad]) == []


class TestPartitionForFold:
    """Where the founder's attention stops.

    The ranker orders and never cuts — `rank()` is a permutation, every input
    key out exactly once. So nothing rendered the order AS an order: the client
    drew every element of a carefully sequenced list.

    A POSITIONAL boundary was tried first and fails, because the ranker
    interleaves the classes: W_BULK_MAIL demotes by 2.5 while W_RECENCY lifts
    by up to 2.0, so recent marketing outranks an older real thread. Measured
    on a live inbox, a last-signal cut left 42 of 85 visible, most of them
    delivery receipts and card alerts.
    """

    @staticmethod
    def _u(key):
        from src.view.contracts import Frame, Unit

        return Unit(
            frame=Frame(
                key=key,
                kind="finding",
                status="new",
                headline=f"headline {key}",
                source="gmail",
                entity_type="email_thread",
                occurred_at=NOW,
                updated_at=NOW,
            ),
            body="",
        )

    def _keys(self, units):
        return [u.frame.key for u in units]

    def test_nothing_bulk_folds_nothing(self):
        from src.view.feed import partition_for_fold

        units = [self._u("a"), self._u("b")]
        out, fold = partition_for_fold(units, {"a": False, "b": False})
        assert fold == 2
        assert self._keys(out) == ["a", "b"]

    def test_interleaved_bulk_is_gathered_below_the_fold(self):
        """The case a positional cut cannot express: bulk scattered through the
        order, because recency lifts a recent marketing mail above real mail."""
        from src.view.feed import partition_for_fold

        units = [self._u(k) for k in "abcd"]
        bulk = {"a": True, "b": False, "c": True, "d": False}
        out, fold = partition_for_fold(units, bulk)
        assert fold == 2
        assert self._keys(out) == ["b", "d", "a", "c"]

    def test_rank_order_survives_inside_each_group(self):
        """Nothing is re-scored — `rank()` stays a pure permutation."""
        from src.view.feed import partition_for_fold

        units = [self._u(k) for k in "abcdef"]
        bulk = dict(a=True, b=False, c=True, d=False, e=True, f=False)
        out, fold = partition_for_fold(units, bulk)
        assert self._keys(out[:fold]) == ["b", "d", "f"]
        assert self._keys(out[fold:]) == ["a", "c", "e"]

    def test_every_unit_survives_the_partition(self):
        """Nothing is dropped: the tail is folded, not filtered."""
        from src.view.feed import partition_for_fold

        units = [self._u(k) for k in "abcde"]
        bulk = dict(a=True, b=False, c=True, d=True, e=False)
        out, _ = partition_for_fold(units, bulk)
        assert sorted(self._keys(out)) == list("abcde")

    def test_an_all_bulk_feed_folds_entirely(self):
        """The honest answer to an inbox holding nothing but marketing."""
        from src.view.feed import partition_for_fold

        out, fold = partition_for_fold([self._u("a"), self._u("b")], {"a": True, "b": True})
        assert fold == 0
        assert len(out) == 2

    def test_an_empty_feed_folds_nothing(self):
        from src.view.feed import partition_for_fold

        assert partition_for_fold([], {}) == ([], 0)

    def test_a_key_the_ranker_never_scored_stays_visible(self):
        """Absent evidence is not evidence of bulk. Defaulting the other way
        would hide a card because a feature lookup missed."""
        from src.view.feed import partition_for_fold

        units = [self._u("a"), self._u("b")]
        _, fold = partition_for_fold(units, {})
        assert fold == 2

    def test_a_malformed_unit_does_not_raise(self):
        from types import SimpleNamespace

        from src.view.feed import partition_for_fold

        out, fold = partition_for_fold([SimpleNamespace(frame=None)], {})
        assert fold == 1 and len(out) == 1


class TestFoldOnTheAssembledFeed:
    async def test_a_ranker_outage_folds_nothing(self, monkeypatch):
        """The fold says "attention stops here", which is only true of an order
        something decided. Folding a composition order would hide things on the
        strength of the sequence builders happened to run in."""

        async def _stored(db, **kw):
            return [_unit("gmail:email_thread:t1"), _unit("gmail:email_thread:t2")], {}

        async def _none_list(db, **kw):
            return []

        async def _none(db, **kw):
            return None

        async def _boom(*a, **k):
            raise RuntimeError("ranker down")

        monkeypatch.setattr("src.view.feed.stored_perception_units", _stored)
        monkeypatch.setattr("src.view.feed.run_units", _none_list)
        monkeypatch.setattr("src.view.feed.briefing_units", _none_list)
        monkeypatch.setattr("src.view.feed.insight_units", _none_list)
        monkeypatch.setattr("src.view.feed.prepared_work_unit", _none)
        monkeypatch.setattr("src.view.feed.connector_health_unit", _none)
        monkeypatch.setattr("src.view.feed.build_features", _boom)

        feed = await assemble_feed(_StubDB(), workspace_id="ws_1", user_id="usr_1", now=NOW)
        assert len(feed.units) == 2
        assert feed.fold_after == len(feed.units)
