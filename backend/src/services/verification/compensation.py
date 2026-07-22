"""Per-capability compensation registry (escalate-first, spec §4.5).

Each write capability MAY declare a compensating action (delete the draft, cancel the
invite). On a partially_completed irreversible write the engine escalates to the
present user with the exact artifact_ref + observed divergence; the user decides
whether to run the compensator (itself gated + idempotent). No compensator ->
escalate regardless (informational). There is deliberately NO startup coverage gate
for compensation — a missing compensator is allowed (D9).
"""

from __future__ import annotations

from dataclasses import dataclass
from typing import Callable


@dataclass(frozen=True, slots=True)
class Compensation:
    """A compensating action for a write capability.

    capability: the compensator capability to run (gated + idempotent when executed).
    build_input: derive the compensator's input from the failed write's artifact_ref.
    description: user-facing explanation of what the compensator does.
    """

    capability: str
    build_input: Callable[[dict], dict]
    description: str = ""


COMPENSATIONS: dict[str, Compensation] = {
    "calendar.create": Compensation(
        capability="calendar.delete",
        build_input=lambda artifact_ref: {
            "event_id": artifact_ref.get("event_id") or artifact_ref.get("id")
        },
        description="Delete the calendar event that the read-back could not confirm.",
    ),
}


def get_compensation(capability: str) -> Compensation | None:
    return COMPENSATIONS.get(capability)


def build_divergence_escalation(*, capability: str, artifact_ref: dict, observed: str) -> dict:
    """Build the escalate-first payload for a contradicted irreversible write. Carries
    the exact artifact_ref + observed divergence, and the compensator if registered
    (None otherwise — escalate regardless)."""
    comp = get_compensation(capability)
    compensator = None
    if comp is not None:
        compensator = {
            "capability": comp.capability,
            "input": comp.build_input(artifact_ref or {}),
            "description": comp.description,
        }
    return {
        "capability": capability,
        "artifact_ref": artifact_ref or {},
        "observed": observed,
        "compensator": compensator,
    }
