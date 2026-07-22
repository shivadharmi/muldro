"""The inline read-back verifier (spec §4.5).

Given a write step's (capability, input, output, risk), decide whether its expected
post-condition holds by reading the effect back BEFORE the step is marked terminal:
  CONFIRMED     — read-back observed the effect (or the write is not irreversible).
  CONTRADICTED  — read-back ran and the effect is ABSENT (surface + escalate-first).
  UNVERIFIED    — no deterministic read exists / seam unavailable / read errored /
                  budget exhausted (honest completed_unverified, upgradeable later).

The connector read is an injected async seam `read_fn(read_capability, read_args)`;
production wires it to the tool-execution path (reads bypass the Step-1 ledger, so no
double-fire), tests mock it. A failed read is UNVERIFIED, never CONTRADICTED — a
verification outage must not false-fail a correct action (spec §7 false-negative risk).
"""

from __future__ import annotations

import logging
from enum import Enum
from typing import Awaitable, Callable

from src.services.verification.post_conditions import (
    POST_CONDITIONS,
    UNVERIFIABLE_CAPABILITIES,
)
from src.services.verification.predicate import is_write_verification_required

logger = logging.getLogger(__name__)

ReadFn = Callable[[str, dict], Awaitable[object]]


class VerifyVerdict(str, Enum):
    CONFIRMED = "confirmed"
    CONTRADICTED = "contradicted"
    UNVERIFIED = "unverified"


class ReadBackVerifier:
    def __init__(self, read_fn: ReadFn | None):
        self._read_fn = read_fn

    async def verify_step(
        self, *, capability: str, write_input: dict, write_output: dict, risk
    ) -> VerifyVerdict:
        # Not an irreversible write -> no read-back required -> trivially confirmed.
        if not is_write_verification_required(capability, risk):
            return VerifyVerdict.CONFIRMED

        pc = POST_CONDITIONS.get(capability)
        if pc is None:
            # Registered as UNVERIFIABLE, or (fail-closed) not registered at all — the
            # startup coverage gate guarantees irreversible caps ARE registered, so an
            # unregistered one here is an anomaly worth logging, resolved to unverified.
            if capability not in UNVERIFIABLE_CAPABILITIES:
                logger.warning(
                    "Irreversible capability %s has no post-condition at verify time "
                    "(coverage gate should prevent this) — resolving to unverified",
                    capability,
                )
            return VerifyVerdict.UNVERIFIED

        if self._read_fn is None:
            return VerifyVerdict.UNVERIFIED  # seam unavailable / budget exhausted

        try:
            read_args = pc.read_args(write_input or {}, write_output or {})
            read_result = await self._read_fn(pc.read_capability, read_args)
        except Exception:
            # A failed read-back is NOT a contradicted effect — fail safe.
            logger.warning(
                "Read-back for %s errored — resolving to unverified", capability, exc_info=True
            )
            return VerifyVerdict.UNVERIFIED

        try:
            ok = pc.assertion(read_result, write_input or {}, write_output or {})
        except Exception:
            logger.warning(
                "Post-condition assertion for %s errored — unverified", capability, exc_info=True
            )
            return VerifyVerdict.UNVERIFIED

        return VerifyVerdict.CONFIRMED if ok else VerifyVerdict.CONTRADICTED


def verdict_to_step_status(verdict: VerifyVerdict) -> str:
    """Map a verdict to the step's terminal status (spec §4.5 three-state model)."""
    return {
        VerifyVerdict.CONFIRMED: "completed",
        VerifyVerdict.CONTRADICTED: "partially_completed",
        VerifyVerdict.UNVERIFIED: "completed_unverified",
    }[verdict]
