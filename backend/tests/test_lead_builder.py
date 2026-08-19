"""Step 10D A-5 (Task D): deep-chat single-lead builder.

``derive_lead_scope`` derives the synthetic lead's capability_scope as the plan-bounded
UNION of each step's authority (mirroring ``resolve_plan_routing``'s per-step routing);
``build_chat_lead`` opens a DB session, derives the scope, and returns the ``lead``
SubAgent. Negative-controls have TEETH: a read-only plan must yield NO write capability,
and a write plan must grant only the plan's specific write, never the executor's union.

DORMANT: nothing here is wired into the live chat path in 5a.
"""

from __future__ import annotations

from types import SimpleNamespace
from unittest.mock import AsyncMock, patch

from src.contracts import PlanStep
from src.orchestrator.agents import SubAgent, ThinkingConfig
from src.orchestrator.lead_builder import (
    build_chat_lead,
    derive_lead_scope,
)
from src.orchestrator.prompts import LEAD_PROMPT

# A perceiver whose read scope is small + entirely read-only.
_PERCEIVER_SCOPE = {"email.read", "email.search", "internal.search"}


def _perceiver() -> SubAgent:
    return SubAgent(
        name="perceiver",
        prompt="p",
        model_tier="balanced",
        capability_scope=set(_PERCEIVER_SCOPE),
    )


def _agents() -> dict[str, SubAgent]:
    return {"perceiver": _perceiver()}


def _step(capability: str, *, actor: str = "muldro") -> PlanStep:
    return PlanStep(description=capability, capability=capability, actor=actor)


def _resolver(cap_map: dict[str, set[str]]):
    """Fake CapabilityResolver: ``capabilities_for_step`` returns a controlled set."""

    async def _caps(cap: str) -> set[str]:
        return set(cap_map[cap])

    return SimpleNamespace(capabilities_for_step=AsyncMock(side_effect=_caps))


def _no_resolver():
    """A resolver that must never be consulted.

    `perceive` and `knowledge.*` are VIRTUAL capabilities — routing vocabulary with no
    backing tool. `derive_lead_scope` must translate them from its own curated tables, so
    reaching the registry for one is itself the bug.
    """

    async def _caps(cap: str) -> set[str]:
        raise AssertionError(
            f"derive_lead_scope consulted the registry for virtual capability {cap!r}"
        )

    return SimpleNamespace(capabilities_for_step=AsyncMock(side_effect=_caps))


class _FakeCM:
    def __init__(self, db):
        self._db = db

    async def __aenter__(self):
        return self._db

    async def __aexit__(self, *exc):
        return False


# --- derive_lead_scope: read-only plan -> NO write capability --------------------------
async def test_read_only_plan_scope_has_no_write_capability():
    steps = [_step("perceive"), _step("knowledge.search")]

    scope = await derive_lead_scope(steps, _no_resolver(), _agents())

    # Both steps are virtual: `perceive` expands to the Perceiver's read scope and
    # `knowledge.search` to the curated recall capabilities.
    from src.orchestrator.lead_builder import KNOWLEDGE_RECALL_CAPABILITIES

    assert scope == _PERCEIVER_SCOPE | set(KNOWLEDGE_RECALL_CAPABILITIES)
    # Teeth: a read-only plan must never grant a write capability.
    assert "email.send" not in scope
    assert "calendar.create" not in scope


# --- derive_lead_scope: write plan -> only the plan's write, not the union -------------
async def test_write_plan_scope_grants_only_that_write():
    steps = [_step("email.send")]
    resolver = _resolver({"email.send": {"email.send", "email.search", "email.list"}})

    scope = await derive_lead_scope(steps, resolver, _agents())

    assert scope == {"email.send", "email.search", "email.list"}
    # Teeth: the lead gets only THIS plan's write, never an unrelated executor write.
    assert "calendar.create" not in scope


# --- derive_lead_scope: respond/reason-only plan -> EMPTY scope ------------------------
async def test_respond_reason_only_plan_scope_is_empty():
    steps = [_step("system.respond"), _step("reason"), _step("respond"), _step("none")]
    resolver = _resolver({})  # must never be consulted for these

    scope = await derive_lead_scope(steps, resolver, _agents())

    assert scope == set()
    resolver.capabilities_for_step.assert_not_called()


# --- derive_lead_scope: user-actor step contributes nothing ---------------------------
async def test_user_actor_step_contributes_no_authority():
    # A user-actor step is something the USER does — the lead must gain no authority from it.
    steps = [_step("email.send", actor="user")]
    resolver = _resolver({"email.send": {"email.send"}})

    scope = await derive_lead_scope(steps, resolver, _agents())

    assert scope == set()
    resolver.capabilities_for_step.assert_not_called()


# --- derive_lead_scope: perceive step -> perceiver's full read scope -------------------
async def test_perceive_step_scope_equals_perceiver_scope():
    steps = [_step("perceive")]
    resolver = _resolver({})

    scope = await derive_lead_scope(steps, resolver, _agents())

    assert scope == _PERCEIVER_SCOPE
    resolver.capabilities_for_step.assert_not_called()


async def test_perceive_step_fail_closed_when_no_perceiver():
    # Missing perceiver -> no read authority granted (fail-closed), not a crash.
    steps = [_step("perceive")]
    resolver = _resolver({})

    scope = await derive_lead_scope(steps, resolver, agents={})

    assert scope == set()


# --- build_chat_lead: SubAgent shape (default + cheap_mode) ----------------------------
async def test_build_chat_lead_produces_lead_agent():
    resolver_instance = _resolver({"email.send": {"email.send", "email.search"}})
    entered = {"count": 0}

    def _db_factory():
        entered["count"] += 1
        return _FakeCM(db=object())

    with patch(
        "src.orchestrator.lead_builder.CapabilityResolver",
        return_value=resolver_instance,
    ) as mock_resolver_cls:
        lead = await build_chat_lead(
            _db_factory,
            workspace_id="ws",
            steps=[_step("email.send")],
            agents=_agents(),
            cheap_mode=False,
        )

    assert entered["count"] == 1  # opened its own DB session
    mock_resolver_cls.assert_called_once()  # constructed a CapabilityResolver
    assert lead.name == "lead"
    assert lead.prompt is LEAD_PROMPT
    assert lead.model_tier == "balanced"
    assert lead.max_tokens == 4096
    assert lead.temperature == 0.3
    assert lead.thinking == ThinkingConfig(enabled=True, budget_tokens=4096)
    assert lead.capability_scope == {"email.send", "email.search"}


async def test_build_chat_lead_cheap_mode_keeps_balanced_and_thinking():
    resolver_instance = _resolver({"email.send": {"email.send"}})

    def _db_factory():
        return _FakeCM(db=object())

    with patch(
        "src.orchestrator.lead_builder.CapabilityResolver",
        return_value=resolver_instance,
    ):
        lead = await build_chat_lead(
            _db_factory,
            workspace_id="ws",
            steps=[_step("email.send")],
            agents=_agents(),
            cheap_mode=True,
        )

    assert lead.name == "lead"
    assert lead.prompt is LEAD_PROMPT
    assert lead.model_tier == "balanced"  # already balanced — cheap mode leaves it
    assert lead.thinking.budget_tokens == 4096  # thinking passed through (halving dropped)
    assert lead.capability_scope == {"email.send"}


# --- A3: knowledge.* must grant REAL authority, not zero -------------------------------
# `knowledge.*` is a routing-only virtual capability with NO backing tool (the same fact
# `connector_scope.INTERNAL_READ_FLOOR` records). Left unresolved it reaches the lead as a
# literal scope entry, `get_tools_for_agent` matches it against no tool, and the lead gets
# ZERO Muldro tools — so a "remember this" turn cannot store and a "what do you know" turn
# cannot search. These tests pin the translation to real internal capabilities.
async def test_knowledge_remember_scope_can_actually_store():
    """A memory-write step must put a real store tool's capability in the lead's scope."""
    steps = [_step("knowledge.remember")]

    scope = await derive_lead_scope(steps, _no_resolver(), _agents())

    assert "internal.store_memory" in scope
    assert "internal.store_preference" in scope
    # It must still be able to recall, or "update what you know about X" cannot read first.
    assert "internal.search" in scope
    # Teeth: the virtual capability must NOT survive into the scope — it matches no tool.
    assert "knowledge.remember" not in scope


async def test_knowledge_search_scope_can_actually_recall():
    """A recall step must put the memory/world-model read capabilities in scope."""
    steps = [_step("knowledge.search")]

    scope = await derive_lead_scope(steps, _no_resolver(), _agents())

    assert "internal.search" in scope
    assert "internal.query_facts" in scope
    assert "knowledge.search" not in scope


async def test_knowledge_scopes_stay_least_authority():
    """Teeth: the curated scopes must not become the blunt `internal.*` family sweep.

    `capabilities_for_step("internal.search")` returns all 23 non-approval `internal.*`
    capabilities — including `internal.push_ui`, `internal.update_execution` and
    `internal.report_verdict`. A chat lead answering "what do you know about X" has no
    business with any of them.
    """
    over_broad = {
        "internal.push_ui",
        "internal.update_execution",
        "internal.update_entity",
        "internal.ingest_event",
        "internal.report_verdict",
        "internal.evaluate_policy",
        "internal.approve_action",
    }
    for cap in ("knowledge.search", "knowledge.remember"):
        scope = await derive_lead_scope([_step(cap)], _no_resolver(), _agents())
        assert not (scope & over_broad), f"{cap} granted over-broad internal caps: {scope}"


async def test_knowledge_search_grants_no_write():
    """Recall is read-only: it must not smuggle in the store capabilities."""
    scope = await derive_lead_scope([_step("knowledge.search")], _no_resolver(), _agents())

    assert "internal.store_memory" not in scope
    assert "internal.store_preference" not in scope
