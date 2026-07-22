"""Compensation registry (escalate-first). A partially_completed irreversible write
builds a divergence escalation carrying the artifact_ref + observed divergence + the
compensator (if any). No compensator -> still escalates."""

from src.services.verification.compensation import (
    COMPENSATIONS,
    build_divergence_escalation,
    get_compensation,
)
from src.services.verification.predicate import is_irreversible_capability


def test_every_registered_compensation_is_a_write():
    for cap in COMPENSATIONS:
        assert is_irreversible_capability(cap), f"{cap} is not irreversible"


def test_compensation_lookup_returns_none_for_unregistered():
    assert get_compensation("email.reply") is None  # not registered -> escalate anyway


def test_escalation_includes_artifact_ref_and_divergence_without_compensator():
    payload = build_divergence_escalation(
        capability="email.reply",
        artifact_ref={"message_id": "m1"},
        observed="read-back could not confirm the reply was sent",
    )
    assert payload["capability"] == "email.reply"
    assert payload["artifact_ref"] == {"message_id": "m1"}
    assert payload["observed"]
    assert payload["compensator"] is None  # no compensator registered -> escalate regardless


def test_escalation_includes_compensator_when_registered():
    payload = build_divergence_escalation(
        capability="calendar.create",
        artifact_ref={"event_id": "e1"},
        observed="event not found on read-back",
    )
    assert payload["compensator"] is not None
    assert payload["compensator"]["capability"] == "calendar.delete"
