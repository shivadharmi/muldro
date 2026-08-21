"""Plan-step partitioning for the chat path.

What is left of the ORCH-P1-1 chat-pipeline helpers after the single-lead collapse. That
module existed because two entry points ran the same per-step routing pre-resolution and
the same Presenter prompt builders, and the two copies kept drifting. Both reasons are
gone: there is ONE lead per turn, it discovers its own tools, and there is no Presenter
step to build a prompt for — so the step→agent/tool pre-resolution and the four prompt
builders were deleted with the legacy arm.

The one thing the chat path still has to know before handing the plan to the lead is which
steps the *user* has to act on, because those are reported to the user rather than executed.
That is a pure filter over ``plan.steps``, so this stays a stateless function (per
engineering-standards §2: a function, not a class with one method).
"""

from __future__ import annotations

from src.contracts import PlanStep


def resolve_plan_routing(steps: list[PlanStep]) -> list[PlanStep]:
    """Return the steps whose actor is the USER, in plan order.

    The lead handles everything else: it is built with the plan's capability union and
    discovers its own tools, so nothing here resolves an agent or a tool set. Do not
    reintroduce per-step agent resolution — routing on agent identity is what the
    single-lead cutover removed.
    """
    return [step for step in steps if step.actor == "user"]
