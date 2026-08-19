"""Every chat lead can render a surface, on any plan shape.

Surfacing is a presentation decision, not a plan capability — the lead is always the reply
producer (`is_reply_lead=True`, unconditional in the deep chat path), so it always carries the
Presenter voice and must always be able to act on it. A `respond`-only plan that can describe a
surface but not create one is a prompt arguing with its own scope.
"""

from __future__ import annotations

from unittest.mock import AsyncMock, create_autospec

import pytest

from src.contracts import PlanStep
from src.orchestrator.agents import AGENTS
from src.orchestrator.lead_builder import PRESENTATION_FLOOR, derive_lead_scope
from src.services.capability_resolver import CapabilityResolver


def _resolver():
    """A double standing in for the real resolver's signature.

    `create_autospec` rather than a bare `MagicMock`: on this branch a hand-written fake that
    asserted a capability resolved to itself is exactly what let a phantom capability survive
    review. The double must be wrong in the same ways the real object would be.
    """
    resolver = create_autospec(CapabilityResolver, instance=True)
    resolver.capabilities_for_step = AsyncMock(side_effect=lambda cap: {cap})
    return resolver


def _step(capability: str) -> PlanStep:
    return PlanStep(description=capability, capability=capability, actor="muldro")


@pytest.mark.parametrize("capability", ["respond", "reason", "perceive", "knowledge.search"])
async def test_every_plan_shape_grants_the_render_capability(capability):
    scope = await derive_lead_scope([_step(capability)], _resolver(), AGENTS)
    assert "internal.render_surface" in scope


async def test_an_empty_plan_still_grants_it():
    scope = await derive_lead_scope([], _resolver(), AGENTS)
    assert "internal.render_surface" in scope


async def test_it_does_not_smuggle_in_any_other_write():
    """Teeth: the presentation floor must not become a general write grant."""
    scope = await derive_lead_scope([_step("respond")], _resolver(), AGENTS)
    assert scope == {"internal.render_surface"}


async def test_a_write_plan_still_gets_only_its_own_writes():
    """The floor is additive, not a widening — an email plan gains rendering, nothing else."""
    scope = await derive_lead_scope([_step("email.send")], _resolver(), AGENTS)
    assert scope == {"email.send"} | PRESENTATION_FLOOR
    assert "calendar.create" not in scope


def test_the_floor_is_exactly_one_internal_capability():
    """A floor that grows silently is a scope grant nobody reviewed."""
    assert PRESENTATION_FLOOR == frozenset({"internal.render_surface"})
