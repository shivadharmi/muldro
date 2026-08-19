"""The record shape a PREPARED write persists (single-lead cutover, Task 4a).

A prepared action is found by the review queue on `approval_type`, expires on the founder's
schedule rather than the turn's, and is distinguishable from a live approval by a key that is
ABSENT (not False) on every pre-existing row.
"""

from datetime import datetime, timedelta, timezone

from src.deep_runtime.middleware.approval_persistence import (
    PREPARED_APPROVAL_TYPE,
    build_legibility_refs,
    prepared_approval_overrides,
)


def test_a_live_approval_keeps_todays_defaults():
    """The interrupt path must be untouched: None/None means _get_or_create_approval keeps
    `tool:<name>` and create_approval's 24h expiry."""
    assert prepared_approval_overrides(False, ttl_days=7) == (None, None)


def test_a_prepared_approval_is_typed_and_given_the_longer_ttl():
    approval_type, expires_at = prepared_approval_overrides(True, ttl_days=7)
    assert approval_type == PREPARED_APPROVAL_TYPE
    expected = datetime.now(timezone.utc) + timedelta(days=7)
    assert abs((expires_at - expected).total_seconds()) < 60


def test_the_ttl_is_configurable_not_hardcoded():
    # Each call anchors on its own `datetime.now()`, microseconds apart, so comparing `.days`
    # directly is off-by-one by construction (6 days minus a few microseconds floors to 5).
    # Compare elapsed seconds against the expected 6-day gap with a tolerance instead.
    _, seven = prepared_approval_overrides(True, ttl_days=7)
    _, one = prepared_approval_overrides(True, ttl_days=1)
    six_days_seconds = timedelta(days=6).total_seconds()
    assert abs((seven - one).total_seconds() - six_days_seconds) < 60


def test_a_live_approval_carries_no_prepared_key_at_all():
    """ABSENT, not False: the review queue keys on presence-of-key, so every approval row
    written before this feature existed stays correctly excluded."""
    refs = build_legibility_refs({"to": "a@b.com"}, frozenset({"email.send"}), "present")
    assert "prepared" not in refs


def test_a_prepared_approval_is_marked():
    refs = build_legibility_refs(
        {"to": "a@b.com"}, frozenset({"email.send"}), "absent", prepared=True
    )
    assert refs["prepared"] is True
    # The four legibility keys are unchanged by the flag.
    assert refs["capability_scope"] == ["email.send"]
    assert refs["effective_presence"] == "absent"
    assert refs["tool_input_truncated"] is False
    assert '"to": "a@b.com"' in refs["tool_input"]
