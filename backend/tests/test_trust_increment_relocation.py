"""Spec §4.5: approved_count must count only VERIFIED writes. A completed_unverified
write must NOT increment trust at finalize; the metadata needed by the deferred tick
must be persisted on the step.

The trust-increment relocation itself (record_auto_execution_outcome gated on
verdict == CONFIRMED) landed in Task 6 and is characterized end-to-end in
tests/test_finalize_verification.py via the DagRunner-driven paths. This file covers
the build_verification_meta helper that persists the deferred-recheck inputs.
"""

from types import SimpleNamespace

from src.services.verification.readback import VerifyVerdict


def _verification_meta(capability, risk, verdict, output):
    from src.services.dag_runner import build_verification_meta

    return build_verification_meta(capability, risk, verdict, output)


def test_verification_meta_captures_deferred_recheck_inputs():
    risk = SimpleNamespace(reversible=False, blast_radius="external_single", risk_level="high")
    meta = _verification_meta("calendar.create", risk, VerifyVerdict.UNVERIFIED, {"event_id": "e1"})
    assert meta["capability"] == "calendar.create"
    assert meta["risk_level"] == "high"
    assert meta["verdict"] == "unverified"
    assert meta["reversible"] is False
    assert meta["blast_radius"] == "external_single"
    assert meta["artifact_ref"]["event_id"] == "e1"


def test_confirmed_write_needs_no_deferred_recheck():
    risk = SimpleNamespace(reversible=False, blast_radius="external_single", risk_level="high")
    meta = _verification_meta("calendar.create", risk, VerifyVerdict.CONFIRMED, {})
    assert meta["verdict"] == "confirmed"
