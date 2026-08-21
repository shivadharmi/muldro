"""Deep-chat single-lead builder (Step 10D A-5). Builds the synthetic per-turn ``lead``
SubAgent that handles a whole user goal on the deep runtime, and derives its
capability_scope from the plan so its authority is plan-bounded and fail-closed.

LIVE: this is THE chat path. Every chat turn builds its lead here — the alternative
per-step arm and the ``deep_single_lead`` flag that gated it are both deleted.
"""

from __future__ import annotations

from src.contracts import PlanStep
from src.orchestrator.agents import SubAgent, ThinkingConfig, apply_cheap_mode
from src.orchestrator.prompts import LEAD_PROMPT, LEAD_PROMPT_PLANLESS
from src.services.capability_resolver import CapabilityResolver

# Capabilities that contribute NO tool authority to the lead (handled without external tools).
_NON_TOOL_CAPABILITIES = {"reason", "respond", "none"}

# ``knowledge.*`` is VIRTUAL — routing vocabulary the Planner prompt and ``intent_to_plan``
# both emit, with no backing tool of its own (the same fact ``connector_scope`` records for
# its ``INTERNAL_READ_FLOOR``). Passed through unresolved it reaches the lead as a literal
# scope entry, ``get_tools_for_agent`` matches it against no tool, and the lead gets ZERO
# Muldro tools: a "remember this" turn silently loses the memory and a "what do you know
# about X" turn cannot search. So it is translated here, exactly as ``perceive`` is.
#
# Curated to LEAST AUTHORITY rather than delegated to ``capabilities_for_step``, whose
# same-family sweep returns ALL 23 non-approval ``internal.*`` capabilities for any one of
# them — handing a recall turn ``internal.push_ui``, ``internal.update_execution`` and
# ``internal.report_verdict``. Same reasoning as ``INTERNAL_READ_FLOOR``.
KNOWLEDGE_RECALL_CAPABILITIES: frozenset[str] = frozenset(
    {
        "internal.search",  # memory / semantic search
        "internal.query_facts",  # world-model fact query
        "internal.get_entity",  # world-model entity read
        "internal.traverse",  # world-model graph traversal
        "internal.get_provenance",  # where a fact came from
    }
)

# Recall PLUS persistence. The two store tools are internal, self-scoped and reversible —
# the user's own instruction to Muldro, not an outbound write — so they are the memory
# analogue of ``SYSTEM_ACTION_CAPABILITIES``. Every call still passes the full middleware
# chain (capability_scope -> ... -> write_lock) like any other tool call.
KNOWLEDGE_REMEMBER_CAPABILITIES: frozenset[str] = KNOWLEDGE_RECALL_CAPABILITIES | {
    "internal.store_memory",
    "internal.store_preference",
}

_VIRTUAL_KNOWLEDGE_SCOPES: dict[str, frozenset[str]] = {
    "knowledge.search": KNOWLEDGE_RECALL_CAPABILITIES,
    "knowledge.remember": KNOWLEDGE_REMEMBER_CAPABILITIES,
}


# The lead is ALWAYS the turn's reply producer (`is_reply_lead=True`, unconditional), so it
# always receives PRESENTER_VOICE and must always be able to act on it. Surfacing is a
# presentation decision, not a plan capability — a `respond`-only plan that can describe a
# surface but not create one is a prompt that argues with its own scope. Deliberately a floor
# of exactly one internal, workspace-scoped capability, not a general write grant.
PRESENTATION_FLOOR: frozenset[str] = frozenset({"internal.render_surface"})


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
    - ``knowledge.search`` / ``knowledge.remember`` → the curated recall / recall+persist
      capabilities. These are VIRTUAL: they back no tool, so passing them through would
      grant the lead nothing at all (see ``_VIRTUAL_KNOWLEDGE_SCOPES``).
    - any real capability C → {C} plus its read-only family capabilities
      (``resolver.capabilities_for_step(C)``) — parity with ``resolve_for_step(C)``.

    The scope starts at ``PRESENTATION_FLOOR`` — the lead is always the turn's reply
    producer, so it always carries the Presenter voice and must always be able to render a
    surface, whatever the plan's shape. The floor is additive, never a widening.

    The result is plan-bounded and fail-closed: a read-only plan yields a read-only scope
    (no write capability), and a write plan grants only the plan's specific write
    capabilities, never the executor's full write union.
    """
    scope: set[str] = set(PRESENTATION_FLOOR)
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
        virtual = _VIRTUAL_KNOWLEDGE_SCOPES.get(cap)
        if virtual is not None:
            scope |= set(virtual)
            continue
        scope |= await resolver.capabilities_for_step(cap)
    return scope


def _make_lead(scope: set[str], cheap_mode: bool, prompt: str = LEAD_PROMPT) -> SubAgent:
    lead = SubAgent(
        name="lead",
        prompt=prompt,
        model_tier="balanced",  # A-5 locked: balanced lead tier (revisit opus-for-critical later)
        capability_scope=scope,
        max_tokens=4096,
        temperature=0.3,
        thinking=ThinkingConfig(enabled=True, budget_tokens=4096),  # tunable at R2 activation
    )
    return apply_cheap_mode(lead) if cheap_mode else lead


def build_chat_lead_planless(scope, cheap_mode: bool) -> SubAgent:
    """Build the planless single-lead SubAgent (P2.5c) from a precomputed connector-derived
    ``scope`` (``resolve_connector_scope``) — NOT a plan. Reuses ``_make_lead`` with the planless
    prompt (the lead self-plans + calls its system.* tools; no Planner). ``scope`` is a frozenset;
    coerce to ``set`` for SubAgent's set-typed ``capability_scope`` field. Fail-closed by
    construction: the lead can only call in-scope tools (capability_scope middleware)."""
    return _make_lead(set(scope), cheap_mode, prompt=LEAD_PROMPT_PLANLESS)


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
