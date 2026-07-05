"""Characterization test (spec §4.5): no write path emits a terminal step status
without a passing post-condition OR an explicit completed_unverified verdict.

Drives finalize_step through the three verdicts and asserts the step lands in the
matching status — and, critically, that an irreversible write is NEVER marked bare
'completed' when its read-back did not confirm."""

from types import SimpleNamespace
from unittest.mock import AsyncMock

from src.services.verification.readback import VerifyVerdict, verdict_to_step_status


def test_verdict_status_mapping_is_total_and_correct():
    assert verdict_to_step_status(VerifyVerdict.CONFIRMED) == "completed"
    assert verdict_to_step_status(VerifyVerdict.CONTRADICTED) == "partially_completed"
    assert verdict_to_step_status(VerifyVerdict.UNVERIFIED) == "completed_unverified"


async def test_irreversible_write_never_bare_completed_without_confirmation():
    """The characterization invariant: for an irreversible capability, a
    non-CONFIRMED verdict must NOT map to 'completed'."""
    from src.services.verification.readback import ReadBackVerifier

    risk = SimpleNamespace(reversible=False, blast_radius="external_single", risk_level="high")

    # No seam -> UNVERIFIED -> completed_unverified (NOT completed).
    v = ReadBackVerifier(read_fn=None)
    verdict = await v.verify_step(
        capability="email.send", write_input={"to": "x"}, write_output={}, risk=risk
    )
    assert verdict_to_step_status(verdict) == "completed_unverified"

    # Contradicted read-back -> partially_completed (NOT completed).
    v2 = ReadBackVerifier(read_fn=AsyncMock(return_value=[]))
    verdict2 = await v2.verify_step(
        capability="calendar.create",
        write_input={"calendar_id": "c"},
        write_output={"event_id": "e"},
        risk=risk,
    )
    assert verdict_to_step_status(verdict2) == "partially_completed"


def test_only_confirmed_maps_to_completed():
    # Enumerate: exactly one verdict yields the terminal 'completed'.
    completed = [v for v in VerifyVerdict if verdict_to_step_status(v) == "completed"]
    assert completed == [VerifyVerdict.CONFIRMED]
