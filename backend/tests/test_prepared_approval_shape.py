"""The record shape a PREPARED write persists (single-lead cutover, Task 4a).

A prepared action is found by the review queue on `approval_type`, never expires, and is
distinguishable from a live approval by a key that is ABSENT (not False) on every
pre-existing row.
"""

from src.deep_runtime.middleware.approval_persistence import (
    build_legibility_refs,
    prepared_approval_overrides,
)
from src.models.approvals import PREPARED_APPROVAL_TYPE


def test_a_live_approval_keeps_todays_defaults():
    """The interrupt path must be untouched: None/None means _get_or_create_approval keeps
    `tool:<name>` and create_approval's 24h expiry."""
    assert prepared_approval_overrides(False) == (None, None)


def test_a_prepared_approval_is_typed_and_never_expires():
    """See tests/test_prepared_actions_do_not_expire.py for why None is safe here: the
    factory exempts this type, so the None below is not read as "use the 24h default"."""
    assert prepared_approval_overrides(True) == (PREPARED_APPROVAL_TYPE, None)


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
