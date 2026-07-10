"""Step 10B Phase 3a: ``DivergenceComparator`` — the PURE (no I/O) diff engine
at the heart of the shadow-compare cutover control plane.

The shadow harness (built across Phase 3) runs a NON-authoritative agent
runtime alongside the authoritative one and diffs their read-only decision
outputs. This module owns exactly two things: the comparable snapshot shape
(``ShadowDecision``) and the comparison function (``DivergenceComparator``).

Both the authoritative and the shadow runtime are captured into the SAME
``ShadowDecision`` shape so they can be diffed WITHOUT comparing transport
details — deep native-stream vs legacy frames is the B12 boundary, not this
module's concern.

Deliberately PURE: no I/O, no async, no DB, no logging side effects, no
imports beyond stdlib. This keeps the module import-cheap for the runner
(Task 3b) and for tests.
"""

from __future__ import annotations

import re
from dataclasses import dataclass, field

_WHITESPACE_RUN = re.compile(r"\s+")


def _normalize_text(s: str) -> str:
    """Normalize text for final_text comparison: lowercase, collapse every run
    of whitespace to a single space, then strip leading/trailing whitespace.

    Rationale: minor whitespace/case differences between runtimes (e.g. one
    runtime emitting a double space or different casing) are not meaningful
    divergences; a real wording difference still is.
    """
    return _WHITESPACE_RUN.sub(" ", s.lower()).strip()


@dataclass(frozen=True)
class ShadowDecision:
    """A captured, comparable snapshot of one runtime's decision for a turn.

    Both the authoritative and the shadow (non-authoritative) runtime are
    captured into this shape so they can be diffed WITHOUT comparing transport
    details (deep native-stream vs legacy frames — that's the B12 boundary).
    """

    route: str  # the agent that handled the turn (agent_name)
    final_text: str  # final user-facing text
    write_intents: frozenset[str] = field(default_factory=frozenset)
    # "capability:tool" the run WANTED to write (captured, never executed)
    gate_verdict: str | None = None  # approval-gate verdict if any (dormant on direct chat)
    read_synthesis: str | None = None  # perceiver read-synthesis if any


@dataclass(frozen=True)
class Divergence:
    # kind: one of "route" | "write_intent_set" | "final_text" | "gate_verdict" | "read_synthesis"
    kind: str
    detail: str  # human-readable description of the difference


class DivergenceComparator:
    """Pure comparison of two ``ShadowDecision`` snapshots."""

    @staticmethod
    def compare(auth: ShadowDecision, shadow: ShadowDecision) -> list[Divergence]:
        divergences: list[Divergence] = []

        if auth.route != shadow.route:
            divergences.append(
                Divergence(
                    kind="route",
                    detail=f"auth route={auth.route!r} != shadow route={shadow.route!r}",
                )
            )

        if auth.write_intents != shadow.write_intents:
            auth_only = auth.write_intents - shadow.write_intents
            shadow_only = shadow.write_intents - auth.write_intents
            divergences.append(
                Divergence(
                    kind="write_intent_set",
                    detail=(
                        f"auth-only intents={sorted(auth_only)!r}, "
                        f"shadow-only intents={sorted(shadow_only)!r}"
                    ),
                )
            )

        if _normalize_text(auth.final_text) != _normalize_text(shadow.final_text):
            divergences.append(
                Divergence(
                    kind="final_text",
                    detail=(
                        f"auth final_text={auth.final_text!r} != "
                        f"shadow final_text={shadow.final_text!r}"
                    ),
                )
            )

        if (
            auth.gate_verdict is not None
            and shadow.gate_verdict is not None
            and auth.gate_verdict != shadow.gate_verdict
        ):
            divergences.append(
                Divergence(
                    kind="gate_verdict",
                    detail=(
                        f"auth gate_verdict={auth.gate_verdict!r} != "
                        f"shadow gate_verdict={shadow.gate_verdict!r}"
                    ),
                )
            )

        if (
            auth.read_synthesis is not None
            and shadow.read_synthesis is not None
            and auth.read_synthesis != shadow.read_synthesis
        ):
            divergences.append(
                Divergence(
                    kind="read_synthesis",
                    detail=(
                        f"auth read_synthesis={auth.read_synthesis!r} != "
                        f"shadow read_synthesis={shadow.read_synthesis!r}"
                    ),
                )
            )

        return divergences
