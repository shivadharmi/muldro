"""Continuous recency decay: exp(-lambda * days_since(last_seen_at)), replacing
the old binary 0.8/0.2. Pure — 'now' is injected so the test is deterministic."""

from datetime import datetime, timedelta, timezone

from src.services.context_builder import (
    _RECENCY_HALFLIFE_DAYS,
    _rank_entities,
    _recency_score,
)

_NOW = datetime(2026, 7, 5, 12, 0, tzinfo=timezone.utc)


def test_zero_days_is_one():
    assert _recency_score(_NOW.isoformat(), now=_NOW) == 1.0


def test_half_life_is_one_half():
    half = (_NOW - timedelta(days=_RECENCY_HALFLIFE_DAYS)).isoformat()
    assert abs(_recency_score(half, now=_NOW) - 0.5) < 1e-6


def test_decays_monotonically():
    d10 = _recency_score((_NOW - timedelta(days=10)).isoformat(), now=_NOW)
    d40 = _recency_score((_NOW - timedelta(days=40)).isoformat(), now=_NOW)
    assert 1.0 > d10 > d40 > 0.0


def test_missing_timestamp_is_zero():
    assert _recency_score(None, now=_NOW) == 0.0
    assert _recency_score("", now=_NOW) == 0.0


def test_unparseable_timestamp_is_zero():
    assert _recency_score("not-a-date", now=_NOW) == 0.0


def test_naive_datetime_is_treated_as_utc():
    naive = datetime(2026, 7, 5, 12, 0)  # no tzinfo
    assert abs(_recency_score(naive, now=_NOW) - 1.0) < 1e-6


def test_rank_entities_prefers_recent_over_stale_at_equal_importance():
    recent = {"importance_score": 0.5, "interaction_count": 1, "last_seen_at": _NOW.isoformat()}
    stale = {
        "importance_score": 0.5,
        "interaction_count": 1,
        "last_seen_at": (_NOW - timedelta(days=120)).isoformat(),
    }
    ranked = _rank_entities([stale, recent])
    assert ranked[0] is recent
