"""End-to-end (§4.5): an irreversible write whose read-back CONTRADICTS the expected
effect lands the step 'partially_completed', builds a divergence escalation carrying
the exact artifact_ref, and offers the registered compensator. Exercises the real
ReadBackVerifier + compensation registry against a mocked read seam (no connectors)."""

from types import SimpleNamespace
from unittest.mock import AsyncMock

from src.services.verification.compensation import build_divergence_escalation
from src.services.verification.readback import (
    ReadBackVerifier,
    VerifyVerdict,
    verdict_to_step_status,
)


async def test_verify_contradicted_escalate_compensate_flow():
    risk = SimpleNamespace(reversible=False, blast_radius="external_multiple", risk_level="medium")

    # 1. Read-back CONTRADICTS: calendar.get returns no matching event.
    read_fn = AsyncMock(return_value=[])
    verifier = ReadBackVerifier(read_fn=read_fn)
    verdict = await verifier.verify_step(
        capability="calendar.create",
        write_input={"calendar_id": "cal_1"},
        write_output={"event_id": "evt_missing"},
        risk=risk,
    )
    assert verdict == VerifyVerdict.CONTRADICTED

    # 2. Verdict maps to the step-level partially_completed terminal status.
    assert verdict_to_step_status(verdict) == "partially_completed"

    # 3. Escalation carries the exact artifact_ref + the registered compensator.
    escalation = build_divergence_escalation(
        capability="calendar.create",
        artifact_ref={"event_id": "evt_missing"},
        observed="event not found on read-back",
    )
    assert escalation["artifact_ref"] == {"event_id": "evt_missing"}
    assert escalation["compensator"]["capability"] == "calendar.delete"
    # 4. The compensator input is derived from the artifact_ref (gated + idempotent on run).
    assert escalation["compensator"]["input"] == {"event_id": "evt_missing"}


async def test_confirmed_flow_marks_completed_no_escalation():
    risk = SimpleNamespace(reversible=False, blast_radius="external_multiple", risk_level="medium")
    read_fn = AsyncMock(return_value={"id": "evt_1"})
    verifier = ReadBackVerifier(read_fn=read_fn)
    verdict = await verifier.verify_step(
        capability="calendar.create",
        write_input={"calendar_id": "cal_1"},
        write_output={"event_id": "evt_1"},
        risk=risk,
    )
    assert verdict == VerifyVerdict.CONFIRMED
    assert verdict_to_step_status(verdict) == "completed"
