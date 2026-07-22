"""Authorization-source provenance for the deep-runtime approval gate (Step 6B, phase-1).

Phase-1 is a coarse STRUCTURAL rule: a chat turn triggered by the user's literal message is
``direct_user_request`` and its writes are ungated (the user's message IS the authorization —
the two-execution-paths invariant). Any other origin (autonomous scheduler/perception, a
headless lead, a custom agent) is gated-by-construction. Phase-2 per-argument provenance taint
is a separate later security plan. The source is a literal captured at the seam — NEVER
LLM-supplied.
"""

from __future__ import annotations

from typing import Final


class AuthorizationSource:
    DIRECT_USER_REQUEST: Final = "direct_user_request"
    AUTONOMOUS: Final = "autonomous"
    HEADLESS: Final = "headless"
    CUSTOM: Final = "custom"


def is_gated_source(source: str) -> bool:
    """True iff a write from this source must pass the approval gate. Fail-closed: only the
    exact ``direct_user_request`` literal is ungated; everything else (incl. unknown) is gated."""
    return source != AuthorizationSource.DIRECT_USER_REQUEST
