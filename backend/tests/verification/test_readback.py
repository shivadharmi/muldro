"""ReadBackVerifier: maps (capability, input, output, risk) -> VerifyVerdict.
The connector read is an injected seam (mocked). No DB, no network."""

from types import SimpleNamespace
from unittest.mock import AsyncMock

from src.services.verification.readback import ReadBackVerifier, VerifyVerdict


def _risk(reversible=False, blast_radius="external_single", risk_level="high"):
    return SimpleNamespace(reversible=reversible, blast_radius=blast_radius, risk_level=risk_level)


async def test_reversible_internal_write_is_not_verified_and_returns_confirmed():
    # Not irreversible -> no read-back required -> trivially confirmed (marks completed).
    v = ReadBackVerifier(read_fn=AsyncMock())
    verdict = await v.verify_step(
        capability="internal.store_memory",
        write_input={},
        write_output={},
        risk=_risk(reversible=True, blast_radius="internal"),
    )
    assert verdict == VerifyVerdict.CONFIRMED
    v._read_fn.assert_not_awaited()


async def test_unverifiable_capability_returns_unverified_without_read():
    v = ReadBackVerifier(read_fn=AsyncMock())
    verdict = await v.verify_step(
        capability="email.send",
        write_input={"to": "a@b.com"},
        write_output={"message_id": "m1"},
        risk=_risk(),
    )
    assert verdict == VerifyVerdict.UNVERIFIED
    v._read_fn.assert_not_awaited()  # no deterministic read exists


async def test_post_condition_confirmed_when_readback_matches():
    read_fn = AsyncMock(return_value={"id": "evt_1"})
    v = ReadBackVerifier(read_fn=read_fn)
    verdict = await v.verify_step(
        capability="calendar.create",
        write_input={"calendar_id": "cal_1"},
        write_output={"event_id": "evt_1"},
        risk=_risk(reversible=False, blast_radius="external_multiple"),
    )
    assert verdict == VerifyVerdict.CONFIRMED
    read_fn.assert_awaited_once()


async def test_post_condition_contradicted_when_readback_absent():
    read_fn = AsyncMock(return_value=[])  # event not found on read-back
    v = ReadBackVerifier(read_fn=read_fn)
    verdict = await v.verify_step(
        capability="calendar.create",
        write_input={"calendar_id": "cal_1"},
        write_output={"event_id": "evt_1"},
        risk=_risk(),
    )
    assert verdict == VerifyVerdict.CONTRADICTED


async def test_post_condition_read_error_is_unverified_not_contradicted():
    # A failed read-back != a contradicted effect. Fail SAFE to unverified.
    read_fn = AsyncMock(side_effect=RuntimeError("connector down"))
    v = ReadBackVerifier(read_fn=read_fn)
    verdict = await v.verify_step(
        capability="calendar.create",
        write_input={"calendar_id": "cal_1"},
        write_output={"event_id": "evt_1"},
        risk=_risk(),
    )
    assert verdict == VerifyVerdict.UNVERIFIED


async def test_no_seam_available_is_unverified():
    # read_fn=None (seam unavailable / verify budget exhausted) -> unverified.
    v = ReadBackVerifier(read_fn=None)
    verdict = await v.verify_step(
        capability="calendar.create",
        write_input={"calendar_id": "cal_1"},
        write_output={"event_id": "evt_1"},
        risk=_risk(),
    )
    assert verdict == VerifyVerdict.UNVERIFIED
