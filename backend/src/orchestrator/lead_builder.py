"""Deep-chat single-lead builder (Step 10D A-5). Builds the synthetic per-turn ``lead``
SubAgent that handles a whole user goal on the deep runtime, and derives its
capability_scope from the plan so its authority is plan-bounded and fail-closed.

DORMANT: nothing here is wired into the live chat path in 5a. Wiring is 5b.
"""

from __future__ import annotations

from src.contracts import PlanStep
from src.orchestrator.agents import SubAgent, ThinkingConfig, apply_cheap_mode
from src.orchestrator.prompts import LEAD_PROMPT
from src.services.capability_resolver import CapabilityResolver

# Capabilities that contribute NO tool authority to the lead (handled without external tools).
_NON_TOOL_CAPABILITIES = {"reason", "respond", "none"}


async def derive_lead_scope(
    steps: list[PlanStep],
    resolver: CapabilityResolver,
    agents: dict[str, SubAgent],
) -> set[str]:
    """Derive the lead's capability_scope as the UNION of each plan step's authority.

    Per step (mirrors resolve_plan_routing's per-step authority):
    - actor == "user", ``system.*``, ``reason``/``respond``/``none`` → contribute nothing.
    - ``perceive`` → the Perceiver's full read scope (parity with the per-step Perceiver,
      which gets ALL its tools). Read-only, so blast radius is bounded to reads.
    - any real capability C → {C} plus its read-only family capabilities
      (``resolver.capabilities_for_step(C)``) — parity with ``resolve_for_step(C)``.

    The result is plan-bounded and fail-closed: a read-only plan yields a read-only scope
    (no write capability), and a write plan grants only the plan's specific write
    capabilities, never the executor's full write union.
    """
    scope: set[str] = set()
    perceiver = agents.get("perceiver")
    for step in steps:
        if getattr(step, "actor", None) == "user":
            continue
        cap = step.capability
        if cap.startswith("system.") or cap in _NON_TOOL_CAPABILITIES:
            continue
        if cap == "perceive":
            if perceiver is not None:
                scope |= set(perceiver.capability_scope)
            continue
        scope |= await resolver.capabilities_for_step(cap)
    return scope


def _make_lead(scope: set[str], cheap_mode: bool) -> SubAgent:
    lead = SubAgent(
        name="lead",
        prompt=LEAD_PROMPT,
        model_tier="sonnet",  # A-5 locked: sonnet lead tier (revisit opus-for-critical later)
        capability_scope=scope,
        max_tokens=4096,
        temperature=0.3,
        thinking=ThinkingConfig(enabled=True, budget_tokens=4096),  # tunable at R2 activation
    )
    return apply_cheap_mode(lead) if cheap_mode else lead


async def build_chat_lead(
    db_factory,
    workspace_id: str,
    steps: list[PlanStep],
    agents: dict[str, SubAgent],
    cheap_mode: bool,
) -> SubAgent:
    """Build the synthetic per-turn lead SubAgent for a plan, opening its own DB session to
    derive the plan-bounded capability_scope (mirrors resolve_plan_routing's session usage)."""
    async with db_factory() as db:
        resolver = CapabilityResolver(db, workspace_id)
        scope = await derive_lead_scope(steps, resolver, agents)
    return _make_lead(scope, cheap_mode)
