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

    units = await assemble_feed(_StubDB(), workspace_id="ws_1", user_id="usr_1", now=NOW)
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

    units = await assemble_feed(_StubDB(), workspace_id="ws_1", user_id="usr_1", now=NOW)
    assert len(units) == 5


@pytest.mark.parametrize("bad", [None, "nope", 42])
def test_order_by_rank_refuses_nothing_and_returns_a_list(bad):
    """Totality: a malformed ranked list must not blank the feed."""
    units = [_unit("a")]
    assert order_by_rank(units, [bad]) == []
