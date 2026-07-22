"""Step-1 carry-forward: on the ledger's in_flight conflict at resume, a read-back
resolves whether the prior attempt's write actually fired. Confirmed -> already_done
(don't re-fire); contradicted/unverified -> escalate (stay fail-closed, now diagnosed)."""

from src.services.verification.inflight import InflightResolution, resolve_inflight_on_resume
from src.services.verification.readback import VerifyVerdict


def test_confirmed_prior_effect_is_already_done():
    assert resolve_inflight_on_resume(VerifyVerdict.CONFIRMED) == InflightResolution.ALREADY_DONE


def test_contradicted_prior_effect_escalates():
    assert resolve_inflight_on_resume(VerifyVerdict.CONTRADICTED) == InflightResolution.ESCALATE


def test_unverified_prior_effect_escalates_fail_closed():
    # Unknown whether it fired -> stay fail-closed, but surface it (don't silently drop).
    assert resolve_inflight_on_resume(VerifyVerdict.UNVERIFIED) == InflightResolution.ESCALATE
