"""In-flight-on-resume resolution (Step-1 carry-forward, spec §6 Step 3).

Step 1's ledger fails CLOSED on an in_flight conflict at resume (a prior attempt
reserved the identity but we never saw record_success — the worker may have crashed
after the external API call but before the checkpoint). Step 3 adds a read-back to
DIAGNOSE it: if the post-condition confirms the effect already landed, resolve to
ALREADY_DONE (don't re-fire); otherwise stay fail-closed but ESCALATE (surface it)
rather than silently blocking.
"""

from __future__ import annotations

from enum import Enum

from src.services.verification.readback import VerifyVerdict


class InflightResolution(str, Enum):
    ALREADY_DONE = "already_done"  # prior write confirmed by read-back — do not re-fire
    ESCALATE = "escalate"  # unknown/contradicted — stay fail-closed, surface to the user


def resolve_inflight_on_resume(verdict: VerifyVerdict) -> InflightResolution:
    """Map a resume-time read-back verdict to a ledger resolution. Only a CONFIRMED
    read-back is safe to treat as already-done; everything else escalates (fail-closed
    is preserved — we never re-fire an ambiguous in-flight write)."""
    if verdict == VerifyVerdict.CONFIRMED:
        return InflightResolution.ALREADY_DONE
    return InflightResolution.ESCALATE
