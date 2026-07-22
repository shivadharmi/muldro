"""Enforced read-back verification + compensation (spec §4.5).

Correctness becomes ENFORCED, not advisory: a world-touching write is verified by
read-back BEFORE its step is marked terminal (mandatory only when IRREVERSIBLE),
and a failed read-back on an irreversible write escalates to the user.
"""

from src.services.verification.compensation import (
    COMPENSATIONS,
    build_divergence_escalation,
    get_compensation,
)
from src.services.verification.inflight import (
    InflightResolution,
    resolve_inflight_on_resume,
)
from src.services.verification.predicate import (
    IRREVERSIBLE,
    is_irreversible_capability,
    is_write_verification_required,
    write_capabilities,
)
from src.services.verification.readback import (
    ReadBackVerifier,
    VerifyVerdict,
    verdict_to_step_status,
)

__all__ = [
    "COMPENSATIONS",
    "build_divergence_escalation",
    "get_compensation",
    "InflightResolution",
    "resolve_inflight_on_resume",
    "IRREVERSIBLE",
    "is_irreversible_capability",
    "is_write_verification_required",
    "write_capabilities",
    "ReadBackVerifier",
    "VerifyVerdict",
    "verdict_to_step_status",
]
