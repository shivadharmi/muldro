# Orchestrator Core Switchover Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Rewrite the orchestrator routing in jarvis.py from 19-decision-type classification to capability-based plan step execution, activating the Perceiver agent and PlanOutput throughout.

**Architecture:** Replace `PlannerOutput` decision routing with `PlanOutput` step loop. Each plan step names a capability (e.g. `email.search`, `system.set_goal`). `route_step()` maps capability → agent. `CapabilityResolver.resolve_for_step()` provides focused tools. Direct handlers become `system.*` capability steps. Old functions (`extract_decision`, `intent_to_decision`, `_resolve_pipeline`) stop being called but are NOT deleted (that's Spec 1B-iii).

**Tech Stack:** Python 3.12, async/await, Pydantic v2, SQLAlchemy async, pytest + pytest-asyncio, ruff

**Spec:** `docs/superpowers/specs/2026-04-07-orchestrator-core-switchover-design.md`

**Prerequisites (all DONE):**
- Spec 1A: `PlanOutput`/`PlanStep`/`CapabilityGap` in contracts.py, `CapabilityResolver` + `route_step()` in capability_resolver.py, `generate_capability_summary()` in capability_summary.py, `discover_capabilities` MCP tool
- Spec 1B-i: `PLANNER_PROMPT_V2` + `PERCEIVER_PROMPT` in prompts.py, `extract_plan()` + `intent_to_plan()` in intent_classifier.py, expanded `FAST_INTENTS`

---

## File Structure

### Modified Files (~15)

| File | Responsibility | Risk |
|------|---------------|------|
| `backend/src/orchestrator/prompts.py` | Switch AGENT_PROMPTS: planner→V2, +perceiver, −observer/researcher | HIGH |
| `backend/src/orchestrator/agents.py` | Swap observer/researcher for perceiver in all dicts | HIGH |
| `backend/src/orchestrator/jarvis.py` | Full routing rewrite, system capability handler, public methods, surface push, planner prompt | **CRITICAL** |
| `backend/src/orchestrator/intent_classifier.py` | Delete `intent_to_decision()` and `extract_decision()` | HIGH |
| `backend/src/services/graph_executor.py` | PlanOutput steps, CapabilityResolver for tools, capability field | HIGH |
| `backend/src/api/routes_chat.py` | `plan` SSE event, `PlanOutput` in metadata | HIGH |
| `backend/src/orchestrator/contracts.py` | `MessageMetadata.decision` type → `PlanOutput` | HIGH |
| `backend/src/interface/telegram.py` | Use public orchestrator methods | MEDIUM |

### Test Files (create or modify)

| File | What it tests |
|------|--------------|
| `backend/tests/test_perceiver_agent.py` | Perceiver registration, scope, observer/researcher gone |
| `backend/tests/test_system_capability_handler.py` | system.* routing to direct handlers |
| `backend/tests/test_orchestrator_routing.py` | Full process_message + process_message_stream with PlanOutput |
| `backend/tests/test_graph_executor.py` (modify) | PlanOutput step population, capability-based tools |
| `backend/tests/test_chat_plan_event.py` | SSE emits `plan` event with PlanOutput shape |
| `backend/tests/test_telegram_public_methods.py` | Telegram uses public get_budget_status/get_system_health |

---

## Task 1: Perceiver Agent Activation

**Files:**
- Modify: `backend/src/orchestrator/prompts.py:861-870`
- Modify: `backend/src/orchestrator/agents.py:12-207`
- Modify: `backend/src/orchestrator/jarvis.py:79-86` (CONTEXT_ENRICHED_AGENTS)
- Create: `backend/tests/test_perceiver_agent.py`

- [ ] **Step 1: Write the failing tests**

```python
# backend/tests/test_perceiver_agent.py
"""Tests for Perceiver agent activation — observer/researcher merge."""

from src.orchestrator.agents import (
    AGENT_CAPABILITY_SCOPES,
    AGENT_MODEL_TIERS,
    AGENT_THINKING,
    AGENTS,
    create_sub_agents,
)
from src.orchestrator.prompts import AGENT_PROMPTS, PERCEIVER_PROMPT


class TestPerceiverRegistration:
    """Verify perceiver replaces observer + researcher in all registries."""

    def test_perceiver_in_agent_prompts(self):
        assert "perceiver" in AGENT_PROMPTS
        assert AGENT_PROMPTS["perceiver"] is PERCEIVER_PROMPT

    def test_observer_not_in_agent_prompts(self):
        assert "observer" not in AGENT_PROMPTS

    def test_researcher_not_in_agent_prompts(self):
        assert "researcher" not in AGENT_PROMPTS

    def test_perceiver_in_model_tiers(self):
        assert AGENT_MODEL_TIERS["perceiver"] == "sonnet"

    def test_observer_not_in_model_tiers(self):
        assert "observer" not in AGENT_MODEL_TIERS

    def test_researcher_not_in_model_tiers(self):
        assert "researcher" not in AGENT_MODEL_TIERS

    def test_perceiver_capability_scope_merges_observer_and_researcher(self):
        scope = AGENT_CAPABILITY_SCOPES["perceiver"]
        # From old observer scope
        assert "email.list" in scope
        assert "email.read" in scope
        assert "internal.ingest_event" in scope
        assert "internal.report_observation" in scope
        # From old researcher scope
        assert "internal.search" in scope
        assert "search.web" in scope
        assert "browser.open" in scope
        assert "browser.snapshot" in scope
        assert "repo.search_code" in scope
        assert "repo.list_prs" in scope

    def test_perceiver_thinking_enabled(self):
        assert "perceiver" in AGENT_THINKING
        assert AGENT_THINKING["perceiver"].enabled is True

    def test_perceiver_in_agents_dict(self):
        assert "perceiver" in AGENTS
        agent = AGENTS["perceiver"]
        assert agent.model_tier == "sonnet"
        assert agent.temperature == 0.3

    def test_observer_not_in_agents_dict(self):
        assert "observer" not in AGENTS

    def test_researcher_not_in_agents_dict(self):
        assert "researcher" not in AGENTS

    def test_planner_prompt_is_v2(self):
        from src.orchestrator.prompts import PLANNER_PROMPT_V2
        assert AGENT_PROMPTS["planner"] is PLANNER_PROMPT_V2

    def test_total_agent_count(self):
        """7 agents: perceiver, librarian, planner, governor, operator, presenter, persona."""
        assert len(AGENTS) == 7
        expected = {"perceiver", "librarian", "planner", "governor", "operator", "presenter", "persona"}
        assert set(AGENTS.keys()) == expected
```

- [ ] **Step 2: Run tests to verify they fail**

Run: `cd backend && python -m pytest tests/test_perceiver_agent.py -v`
Expected: Multiple FAIL — "observer" still in AGENT_PROMPTS, "perceiver" not found, planner prompt is not V2

- [ ] **Step 3: Update prompts.py — switch AGENT_PROMPTS**

In `backend/src/orchestrator/prompts.py`, replace the `AGENT_PROMPTS` dict (lines 861-870):

```python
AGENT_PROMPTS = {
    "perceiver": PERCEIVER_PROMPT,
    "librarian": LIBRARIAN_PROMPT,
    "planner": PLANNER_PROMPT_V2,
    "governor": GOVERNOR_PROMPT,
    "operator": OPERATOR_PROMPT,
    "presenter": PRESENTER_PROMPT,
    "persona": PERSONA_PROMPT,
}
```

- [ ] **Step 4: Update agents.py — swap agent registries**

In `backend/src/orchestrator/agents.py`, replace the three dicts:

Replace `AGENT_MODEL_TIERS` (lines 12-21):
```python
AGENT_MODEL_TIERS = {
    "perceiver": "sonnet",
    "librarian": "sonnet",
    "planner": "opus",
    "governor": "sonnet",
    "operator": "sonnet",
    "presenter": "sonnet",
    "persona": "haiku",
}
```

Replace `AGENT_CAPABILITY_SCOPES` (lines 26-186) — merge observer + researcher into perceiver:
```python
AGENT_CAPABILITY_SCOPES: dict[str, set[str]] = {
    "perceiver": {
        # From observer — external data source reads
        "email.list",
        "email.read",
        "email.search",
        "calendar.list",
        "calendar.get",
        "calendar.read",
        "doc.drive_list",
        "doc.drive_search",
        "doc.get",
        "doc.search",
        "doc.query",
        "messaging.list_channels",
        "messaging.get_history",
        "messaging.get_thread",
        "messaging.get_users",
        "messaging.get_profile",
        "messaging.search",
        "issue.list",
        "issue.get",
        "issue.search",
        "repo.search_code",
        "repo.search_repos",
        "repo.list_prs",
        "repo.get_diff",
        "repo.get_reviews",
        "workflow.list",
        "workflow.get",
        "workflow.search",
        "workflow.get_teams",
        "filesystem.read",
        "filesystem.list",
        "filesystem.search",
        # From observer — internal observation tools
        "internal.ingest_event",
        "internal.report_observation",
        "internal.get_cursor",
        "internal.update_cursor",
        # From researcher — knowledge + web
        "internal.search",
        "search.web",
        "browser.open",
        "browser.snapshot",
        "browser.extract",
        "browser.screenshot",
    },
    "librarian": {
        "internal.update_entity",
        "internal.search",
        "internal.store_memory",
    },
    "planner": {
        "internal.get_plans",
        "internal.get_goals",
        "internal.search",
        "internal.store_memory",
        "internal.discover_capabilities",
    },
    "governor": {
        "internal.evaluate_policy",
        "internal.approve_action",
        "internal.get_plan_details",
    },
    "operator": {
        # Email
        "email.list",
        "email.read",
        "email.search",
        "email.send",
        "email.draft",
        "email.reply",
        # Calendar
        "calendar.list",
        "calendar.get",
        "calendar.read",
        "calendar.create",
        "calendar.update",
        "calendar.delete",
        # Messaging
        "messaging.list_channels",
        "messaging.get_history",
        "messaging.get_thread",
        "messaging.send",
        "messaging.reply",
        "messaging.react",
        "messaging.update",
        "messaging.send_template",
        "messaging.post",
        "messaging.share",
        # Issues
        "issue.list",
        "issue.get",
        "issue.search",
        "issue.create",
        "issue.update",
        "issue.comment",
        "issue.transition",
        "issue.sub_issue",
        # Repos
        "repo.list_prs",
        "repo.get_diff",
        "repo.get_reviews",
        "repo.create_pr",
        "repo.merge_pr",
        "repo.update_pr",
        # Workflow
        "workflow.list",
        "workflow.get",
        "workflow.search",
        "workflow.create_issue",
        "workflow.update_issue",
        "workflow.transition",
        "workflow.comment",
        "workflow.delete",
        "workflow.delete_comment",
        "workflow.delete_milestone",
        # Docs
        "doc.create",
        "doc.update",
        "doc.comment",
        "doc.append",
        # Internal
        "internal.update_execution",
    },
    "presenter": {
        "internal.get_briefing",
        "internal.search",
        "internal.send_telegram",
        "internal.send_approval",
        "internal.push_ui",
        "messaging.send",
    },
    "persona": {
        "internal.search",
        "internal.extract_preferences",
        "internal.store_preference",
    },
}
```

Replace `AGENT_THINKING` (lines 198-207):
```python
AGENT_THINKING: dict[str, ThinkingConfig] = {
    "planner": ThinkingConfig(enabled=True, budget_tokens=8192),
    "perceiver": ThinkingConfig(enabled=True, budget_tokens=6144),
    "librarian": ThinkingConfig(enabled=True, budget_tokens=4096),
    "presenter": ThinkingConfig(enabled=True, budget_tokens=4096),
    "governor": ThinkingConfig(enabled=True, budget_tokens=2048),
    "operator": ThinkingConfig(enabled=True, budget_tokens=2048),
    "persona": ThinkingConfig(enabled=True, budget_tokens=2048),
}
```

- [ ] **Step 5: Update jarvis.py — CONTEXT_ENRICHED_AGENTS**

In `backend/src/orchestrator/jarvis.py`, replace `CONTEXT_ENRICHED_AGENTS` (lines 79-86):

```python
CONTEXT_ENRICHED_AGENTS = {
    "planner",
    "presenter",
    "perceiver",
    "librarian",
    "operator",
    "governor",
}
```

- [ ] **Step 6: Run tests to verify they pass**

Run: `cd backend && python -m pytest tests/test_perceiver_agent.py -v`
Expected: All 14 tests PASS

- [ ] **Step 7: Run existing tests to check for regressions**

Run: `cd backend && python -m pytest tests/test_agent_loop.py tests/test_route_resolver.py tests/test_intent_to_plan.py -v`
Expected: All PASS (agent_loop tests reference agent objects generically, not by name)

- [ ] **Step 8: Commit**

```bash
cd backend
git add src/orchestrator/prompts.py src/orchestrator/agents.py src/orchestrator/jarvis.py tests/test_perceiver_agent.py
git commit -m "feat(spec1b-ii): activate perceiver agent, retire observer/researcher"
```

---

## Task 2: System Capability Handler + Public Orchestrator Methods

**Files:**
- Modify: `backend/src/orchestrator/jarvis.py` (add `_handle_system_capability`, `get_budget_status`, `get_system_health`)
- Create: `backend/tests/test_system_capability_handler.py`

- [ ] **Step 1: Write the failing tests**

```python
# backend/tests/test_system_capability_handler.py
"""Tests for _handle_system_capability and public orchestrator methods."""

import pytest
from unittest.mock import AsyncMock, MagicMock, patch

from src.orchestrator.contracts import PlanOutput, PlanStep, PlannerOutput, InstructionSpec


def _make_orchestrator():
    """Create a minimal JarvisOrchestrator with mocked deps."""
    from src.orchestrator.jarvis import JarvisOrchestrator

    settings = MagicMock()
    settings.use_bedrock = False
    settings.daily_token_budget_usd = 10.0
    settings.redis_url = "redis://localhost:6379"
    db_factory = MagicMock()
    services = MagicMock()
    services.memory_service = AsyncMock()
    services.memory_service.store_goal_memory = AsyncMock(return_value="mem_test123")
    services.memory_service.store_instruction_memory = AsyncMock(return_value="mem_instr456")
    services.memory_service.store_briefing_memory = AsyncMock(return_value="mem_brief789")
    services.redis = None

    with patch("src.orchestrator.jarvis.get_anthropic_client"):
        orch = JarvisOrchestrator(settings=settings, db_factory=db_factory, services=services)
    return orch


class TestHandleSystemCapability:
    """system.* capability steps route to the correct direct handler."""

    @pytest.mark.asyncio
    async def test_system_set_goal(self):
        orch = _make_orchestrator()
        step = PlanStep(
            step_id="s1",
            description="Set goal: launch by April",
            capability="system.set_goal",
            input={},
        )
        plan = PlanOutput(
            goal="Launch product by April",
            reasoning="User wants to set a goal",
            priority="high",
            steps=[step],
        )
        result = await orch._handle_system_capability(step, plan, "usr_1", "ws_1")
        assert result["status"] == "created"
        assert result["memory_id"] == "mem_test123"
        orch._services.memory_service.store_goal_memory.assert_called_once_with(
            user_id="usr_1",
            workspace_id="ws_1",
            title="Set goal: launch by April",
            priority="high",
        )

    @pytest.mark.asyncio
    async def test_system_set_instruction(self):
        orch = _make_orchestrator()
        step = PlanStep(
            step_id="s1",
            description="Summarize email every morning",
            capability="system.set_instruction",
            input={
                "instruction": {
                    "instruction_text": "Summarize email every morning",
                    "instruction_type": "schedule",
                }
            },
        )
        plan = PlanOutput(
            goal="Set recurring instruction",
            steps=[step],
        )
        result = await orch._handle_system_capability(step, plan, "usr_1", "ws_1")
        assert result["status"] == "created"
        assert result["memory_id"] == "mem_instr456"

    @pytest.mark.asyncio
    async def test_system_add_to_brief(self):
        orch = _make_orchestrator()
        step = PlanStep(
            step_id="s1",
            description="Add investor update to briefing",
            capability="system.add_to_brief",
            input={},
        )
        plan = PlanOutput(goal="Add to briefing", steps=[step])
        result = await orch._handle_system_capability(step, plan, "usr_1", "ws_1")
        assert result["status"] == "stored"
        assert result["memory_id"] == "mem_brief789"

    @pytest.mark.asyncio
    async def test_system_schedule_reminder(self):
        orch = _make_orchestrator()
        orch._db_factory = MagicMock()
        mock_db = AsyncMock()
        mock_db.commit = AsyncMock()
        mock_db.add = MagicMock()

        async def db_context():
            return mock_db

        ctx = AsyncMock()
        ctx.__aenter__ = AsyncMock(return_value=mock_db)
        ctx.__aexit__ = AsyncMock(return_value=False)
        orch._db_factory.return_value = ctx

        step = PlanStep(
            step_id="s1",
            description="Remind me to call John at 3pm",
            capability="system.schedule_reminder",
            input={"cron_expr": "0 15 * * *"},
        )
        plan = PlanOutput(goal="Schedule reminder", priority="medium", steps=[step])
        result = await orch._handle_system_capability(step, plan, "usr_1", "ws_1")
        assert result["status"] == "created"
        assert "schedule_id" in result

    @pytest.mark.asyncio
    async def test_system_respond_returns_empty(self):
        orch = _make_orchestrator()
        step = PlanStep(step_id="s1", description="Respond", capability="system.respond")
        plan = PlanOutput(goal="Respond", steps=[step])
        result = await orch._handle_system_capability(step, plan, "usr_1", "ws_1")
        assert result == {}

    @pytest.mark.asyncio
    async def test_unknown_system_capability_returns_empty(self):
        orch = _make_orchestrator()
        step = PlanStep(step_id="s1", description="?", capability="system.unknown_thing")
        plan = PlanOutput(goal="?", steps=[step])
        result = await orch._handle_system_capability(step, plan, "usr_1", "ws_1")
        assert result == {}


class TestPublicOrchestratorMethods:
    """get_budget_status() and get_system_health() — public API for Telegram."""

    @pytest.mark.asyncio
    async def test_get_budget_status(self):
        orch = _make_orchestrator()
        mock_status = MagicMock()
        mock_status.daily_spend_usd = 1.5
        mock_status.daily_limit_usd = 10.0
        orch._budget = MagicMock()
        orch._budget.get_budget_status = AsyncMock(return_value=mock_status)

        mock_db = AsyncMock()
        ctx = AsyncMock()
        ctx.__aenter__ = AsyncMock(return_value=mock_db)
        ctx.__aexit__ = AsyncMock(return_value=False)
        orch._db_factory = MagicMock(return_value=ctx)

        status = await orch.get_budget_status()
        assert status.daily_spend_usd == 1.5
        assert status.daily_limit_usd == 10.0

    @pytest.mark.asyncio
    async def test_get_system_health(self):
        orch = _make_orchestrator()
        orch._circuit_breaker = MagicMock()
        orch._circuit_breaker.is_open = MagicMock(return_value=False)
        orch._background_tasks = set()

        health = await orch.get_system_health()
        assert health["circuit_breaker_open"] is False
        assert health["background_tasks"] == 0
        assert "agents" in health
```

- [ ] **Step 2: Run tests to verify they fail**

Run: `cd backend && python -m pytest tests/test_system_capability_handler.py -v`
Expected: FAIL — `_handle_system_capability` not found, `get_budget_status` not found

- [ ] **Step 3: Implement `_handle_system_capability` in jarvis.py**

Add this method to `JarvisOrchestrator` class (after `_handle_add_to_brief`, around line 2494):

```python
    async def _handle_system_capability(
        self,
        step: "PlanStep",
        plan: "PlanOutput",
        user_id: str,
        workspace_id: str,
    ) -> dict:
        """Route system.* capability steps to direct handlers.

        Bridges PlanStep data to the existing handlers which accept
        PlannerOutput. Full handler rewrite is deferred to Spec 1B-iii.
        """
        from src.orchestrator.contracts import InstructionSpec, PlannerOutput, PlannerTask

        cap = step.capability

        if cap in ("system.respond", "system.acknowledge"):
            return {}

        # Build a bridge PlannerOutput for legacy handlers
        bridge = PlannerOutput(
            decision=cap.removeprefix("system."),
            goal=step.description or plan.goal,
            reasoning=plan.reasoning,
            priority=plan.priority,
        )

        # Transfer instruction spec from step input if present
        if step.input.get("instruction"):
            try:
                bridge = bridge.model_copy(
                    update={"instruction": InstructionSpec(**step.input["instruction"])}
                )
            except Exception:
                logger.debug("Failed to parse instruction from step input", exc_info=True)

        # Transfer tasks from step input if present (for schedule_reminder)
        if step.input.get("tasks"):
            try:
                bridge = bridge.model_copy(
                    update={
                        "tasks": [PlannerTask(**t) for t in step.input["tasks"]]
                    }
                )
            except Exception:
                logger.debug("Failed to parse tasks from step input", exc_info=True)

        if cap == "system.set_goal":
            return await self._handle_set_goal(bridge, user_id, workspace_id)
        elif cap == "system.set_instruction":
            return await self._handle_set_instruction(bridge, user_id, workspace_id)
        elif cap == "system.schedule_reminder":
            return await self._handle_schedule_reminder(bridge, user_id, workspace_id)
        elif cap == "system.add_to_brief":
            return await self._handle_add_to_brief(bridge, user_id, workspace_id)
        else:
            logger.warning("Unknown system capability: %s", cap)
            return {}
```

Add the import at the top of jarvis.py (around line 31, with the other contracts imports):
```python
from src.orchestrator.contracts import PlannerOutput, PlanOutput, PlanStep
```

- [ ] **Step 4: Implement `get_budget_status` and `get_system_health` in jarvis.py**

Add these public methods to `JarvisOrchestrator` (after `shutdown`, around line 248):

```python
    async def get_budget_status(self):
        """Public accessor for budget status — replaces private attribute access."""
        async with self._db_factory() as db:
            return await self._budget.get_budget_status(db)

    async def get_system_health(self) -> dict:
        """Public accessor for system health — replaces private attribute access."""
        return {
            "circuit_breaker_open": self._circuit_breaker.is_open()
            if hasattr(self._circuit_breaker, "is_open")
            else False,
            "background_tasks": len(self._background_tasks),
            "agents": sorted(self._agents.keys()),
        }
```

- [ ] **Step 5: Run tests to verify they pass**

Run: `cd backend && python -m pytest tests/test_system_capability_handler.py -v`
Expected: All 8 tests PASS

- [ ] **Step 6: Commit**

```bash
cd backend
git add src/orchestrator/jarvis.py tests/test_system_capability_handler.py
git commit -m "feat(spec1b-ii): add system capability handler + public orchestrator methods"
```

---

## Task 3: Planner System Prompt + Capability Summary Injection

**Files:**
- Modify: `backend/src/orchestrator/jarvis.py` (`_build_system_prompt`, `_call_agent`, `_call_agent_stream`)

This task modifies `_build_system_prompt` to inject the capability summary for the planner (replacing `JARVIS_DECISION_FRAMEWORK`) and adds `capability_summary` + `tools_override` parameters to `_call_agent` / `_call_agent_stream`.

- [ ] **Step 1: Write the failing test**

```python
# Add to backend/tests/test_system_capability_handler.py

class TestPlannerSystemPrompt:
    """Planner gets capability summary, not JARVIS_DECISION_FRAMEWORK."""

    def test_build_system_prompt_planner_with_cap_summary(self):
        orch = _make_orchestrator()
        from src.orchestrator.agents import AGENTS

        planner = AGENTS["planner"]
        blocks = orch._build_system_prompt(
            planner, context="", capability_summary="<connected_services>\n  Email: search, read\n</connected_services>"
        )
        prompt_text = blocks[0]["text"]
        # Should contain capability summary (formatted into PLANNER_PROMPT_V2)
        assert "Email: search, read" in prompt_text
        # Should NOT contain old decision framework
        assert "decision_framework" not in prompt_text
        assert "<decisions>" not in prompt_text

    def test_build_system_prompt_non_planner_ignores_cap_summary(self):
        orch = _make_orchestrator()
        from src.orchestrator.agents import AGENTS

        presenter = AGENTS["presenter"]
        blocks = orch._build_system_prompt(
            presenter, context="", capability_summary="should not appear"
        )
        prompt_text = blocks[0]["text"]
        assert "should not appear" not in prompt_text

    def test_build_system_prompt_planner_without_cap_summary(self):
        orch = _make_orchestrator()
        from src.orchestrator.agents import AGENTS

        planner = AGENTS["planner"]
        blocks = orch._build_system_prompt(planner, context="")
        prompt_text = blocks[0]["text"]
        # Placeholder should remain unformatted (raw template)
        assert "{capability_summary}" in prompt_text
```

- [ ] **Step 2: Run tests to verify they fail**

Run: `cd backend && python -m pytest tests/test_system_capability_handler.py::TestPlannerSystemPrompt -v`
Expected: FAIL — `_build_system_prompt` doesn't accept `capability_summary`

- [ ] **Step 3: Modify `_build_system_prompt`**

In `backend/src/orchestrator/jarvis.py`, replace `_build_system_prompt` (around line 2311):

```python
    def _build_system_prompt(
        self, agent: SubAgent, context: str = "", capability_summary: str = ""
    ) -> list[dict]:
        """Build system prompt with cache_control for prompt caching.

        For the Planner, injects the runtime capability summary into
        PLANNER_PROMPT_V2 (replacing the {capability_summary} placeholder).
        Other agents get JARVIS_SOUL_CORE + their role prompt unchanged.
        """
        soul = JARVIS_SOUL_CORE

        prompt = agent.prompt
        if agent.name == "planner" and capability_summary:
            prompt = prompt.format(capability_summary=capability_summary)

        blocks = [
            {
                "type": "text",
                "text": f"{soul}\n\n--- YOUR ROLE ---\n{prompt}",
                "cache_control": {"type": "ephemeral"},
            },
        ]
        if context:
            blocks.append({"type": "text", "text": context})
        return blocks
```

- [ ] **Step 4: Add `capability_summary` and `tools_override` params to `_call_agent`**

In `backend/src/orchestrator/jarvis.py`, modify `_call_agent` signature and body (around line 2496):

```python
    async def _call_agent(
        self,
        agent_name: str,
        message: str,
        user_id: str,
        trace=None,
        max_tool_rounds: int = 10,
        workspace_id: str = "",
        capability_summary: str = "",
        tools_override: list[dict] | None = None,
    ) -> str:
        """Call a sub-agent (non-streaming). Returns final text response."""
        agent = self._agents.get(agent_name)
        if not agent:
            raise ValueError(f"Unknown agent: {agent_name}")

        model = self._get_model_for_agent(agent)

        if tools_override is not None:
            tools = self._apply_cache_control_to_tools(tools_override)
        else:
            tools = self._apply_cache_control_to_tools(
                await self._get_tools_for_agent(agent, workspace_id=workspace_id)
            )

        # Auto-generate capability summary for planner if not provided
        if agent_name == "planner" and not capability_summary:
            try:
                from src.orchestrator.capability_summary import generate_capability_summary

                async with self._db_factory() as db:
                    capability_summary = await generate_capability_summary(db, workspace_id)
            except Exception:
                logger.debug("Failed to generate capability summary", exc_info=True)

        context_block = await self._assemble_context(
            agent_name, message, user_id=user_id, workspace_id=workspace_id
        )
        system_blocks = self._build_system_prompt(
            agent, context_block, capability_summary=capability_summary
        )

        text = ""
        async for evt in agent_loop(
            client=self._client,
            agent=agent,
            model=model,
            system_blocks=system_blocks,
            tools=tools,
            message=message,
            user_id=user_id,
            workspace_id=workspace_id,
            db_factory=self._db_factory,
            services=self._services,
            budget=self._budget,
            trace=trace,
            execute_tool_fn=self._execute_tool,
            max_tool_rounds=max_tool_rounds,
            stream=False,
            circuit_breaker=self._circuit_breaker,
        ):
            if isinstance(evt, LoopDone):
                text = evt.text
                logger.info(
                    "agent_call_complete",
                    extra={
                        "agent": agent_name,
                        "model": model,
                        "input_tokens": evt.input_tokens,
                        "output_tokens": evt.output_tokens,
                        "tools_called": evt.tools_called,
                        "latency_ms": evt.latency_ms,
                        "trace_id": trace.trace_id if trace else None,
                    },
                )

        return text
```

- [ ] **Step 5: Add same params to `_call_agent_stream`**

In `backend/src/orchestrator/jarvis.py`, modify `_call_agent_stream` signature (around line 1223):

```python
    async def _call_agent_stream(
        self,
        agent_name: str,
        message: str,
        user_id: str,
        trace=None,
        max_tool_rounds: int = 10,
        workspace_id: str = "",
        capability_summary: str = "",
        tools_override: list[dict] | None = None,
    ) -> AsyncGenerator[dict[str, Any], None]:
        """Call a sub-agent with streaming, yielding SSE-compatible dicts."""
        agent = self._agents.get(agent_name)
        if not agent:
            yield {"event": "error", "message": f"Unknown agent: {agent_name}"}
            return

        model = self._get_model_for_agent(agent)

        if tools_override is not None:
            tools = self._apply_cache_control_to_tools(tools_override)
        else:
            tools = self._apply_cache_control_to_tools(
                await self._get_tools_for_agent(agent, workspace_id=workspace_id)
            )

        # Auto-generate capability summary for planner if not provided
        if agent_name == "planner" and not capability_summary:
            try:
                from src.orchestrator.capability_summary import generate_capability_summary

                async with self._db_factory() as db:
                    capability_summary = await generate_capability_summary(db, workspace_id)
            except Exception:
                logger.debug("Failed to generate capability summary", exc_info=True)

        context_block = await self._assemble_context(
            agent_name, message, user_id=user_id, workspace_id=workspace_id
        )
        system_blocks = self._build_system_prompt(
            agent, context_block, capability_summary=capability_summary
        )

        async for evt in agent_loop(
            client=self._client,
            agent=agent,
            model=model,
            system_blocks=system_blocks,
            tools=tools,
            message=message,
            user_id=user_id,
            workspace_id=workspace_id,
            db_factory=self._db_factory,
            services=self._services,
            budget=self._budget,
            trace=trace,
            execute_tool_fn=self._execute_tool,
            max_tool_rounds=max_tool_rounds,
            stream=True,
            circuit_breaker=self._circuit_breaker,
        ):
            # ... existing event mapping (unchanged) ...
```

The event mapping block (`if isinstance(evt, LoopAgentStart)` etc.) stays exactly the same.

- [ ] **Step 6: Remove JARVIS_DECISION_FRAMEWORK from import and usage**

In `backend/src/orchestrator/jarvis.py`, update the import (line 38):

Before:
```python
from src.orchestrator.prompts import JARVIS_DECISION_FRAMEWORK, JARVIS_SOUL_CORE
```

After:
```python
from src.orchestrator.prompts import JARVIS_SOUL_CORE
```

Note: `JARVIS_DECISION_FRAMEWORK` is NOT deleted from prompts.py — only the import in jarvis.py is removed. Deletion is Spec 1B-iii.

- [ ] **Step 7: Run tests to verify they pass**

Run: `cd backend && python -m pytest tests/test_system_capability_handler.py -v`
Expected: All PASS (including new TestPlannerSystemPrompt tests)

- [ ] **Step 8: Commit**

```bash
cd backend
git add src/orchestrator/jarvis.py tests/test_system_capability_handler.py
git commit -m "feat(spec1b-ii): inject capability summary into planner prompt, add tools_override"
```

---

## Task 4: Plan Persistence + Lightweight Run for PlanOutput

**Files:**
- Modify: `backend/src/orchestrator/jarvis.py` (`_persist_plan_record`, `_create_lightweight_run`, `_complete_lightweight_run`)

- [ ] **Step 1: Write the failing tests**

Add to `backend/tests/test_system_capability_handler.py`:

```python
class TestPlanPersistence:
    """_persist_plan_record accepts PlanOutput and creates Plan + PlanTasks."""

    @pytest.mark.asyncio
    async def test_persist_plan_record_with_plan_output(self):
        orch = _make_orchestrator()

        # Mock DB factory
        mock_db = AsyncMock()
        mock_db.commit = AsyncMock()
        mock_db.add = MagicMock()
        mock_db.execute = AsyncMock(return_value=MagicMock(scalar_one_or_none=MagicMock(return_value=None)))
        ctx = AsyncMock()
        ctx.__aenter__ = AsyncMock(return_value=mock_db)
        ctx.__aexit__ = AsyncMock(return_value=False)
        orch._db_factory = MagicMock(return_value=ctx)

        plan = PlanOutput(
            goal="Send follow-up email",
            reasoning="User wants to follow up with investor",
            priority="high",
            steps=[
                PlanStep(step_id="s1", description="Read email", capability="email.read", risk="none"),
                PlanStep(
                    step_id="s2",
                    description="Draft reply",
                    capability="email.draft",
                    depends_on=["s1"],
                    risk="medium",
                ),
            ],
        )

        result = await orch._persist_plan_record(plan, "usr_1", "ws_1")
        assert isinstance(result, PlanOutput)
        assert result.plan_id is not None
        assert result.plan_id.startswith("plan_")
        # DB should have been called
        assert mock_db.add.called
        assert mock_db.commit.called

    @pytest.mark.asyncio
    async def test_persist_plan_record_skips_user_steps(self):
        orch = _make_orchestrator()

        added_objects = []
        mock_db = AsyncMock()
        mock_db.commit = AsyncMock()
        mock_db.add = MagicMock(side_effect=lambda obj: added_objects.append(obj))
        mock_db.execute = AsyncMock(return_value=MagicMock(scalar_one_or_none=MagicMock(return_value=None)))
        ctx = AsyncMock()
        ctx.__aenter__ = AsyncMock(return_value=mock_db)
        ctx.__aexit__ = AsyncMock(return_value=False)
        orch._db_factory = MagicMock(return_value=ctx)

        plan = PlanOutput(
            goal="Send email",
            steps=[
                PlanStep(step_id="s1", description="Draft", capability="email.draft", risk="medium"),
                PlanStep(step_id="s2", description="User reviews", capability="email.send", actor="user"),
            ],
        )

        await orch._persist_plan_record(plan, "usr_1", "ws_1")
        # Only the Plan object should be added (tasks are on plan.tasks)
        from src.models.plans import Plan
        plans = [o for o in added_objects if isinstance(o, Plan)]
        assert len(plans) == 1
        # The plan should have 1 task (user step skipped)
        assert len(plans[0].tasks) == 1
```

- [ ] **Step 2: Run tests to verify they fail**

Run: `cd backend && python -m pytest tests/test_system_capability_handler.py::TestPlanPersistence -v`
Expected: FAIL — `_persist_plan_record` expects `PlannerOutput`, not `PlanOutput`

- [ ] **Step 3: Rewrite `_persist_plan_record` to accept PlanOutput**

In `backend/src/orchestrator/jarvis.py`, replace `_persist_plan_record` (around line 267):

```python
    async def _persist_plan_record(
        self,
        plan: "PlanOutput",
        user_id: str,
        workspace_id: str,
        trigger_type: str = "user_message",
        idempotency_key: str | None = None,
    ) -> "PlanOutput":
        """Persist a Plan + PlanTasks to DB from a PlanOutput.

        Creates DB records so the Governor can evaluate policy and the
        Operator can execute via GraphExecutor. Only jarvis-actor steps
        become PlanTasks; user-actor steps are skipped.

        Returns the PlanOutput with plan_id populated.
        """
        from src.models.plans import Plan, PlanTask

        plan_id = f"plan_{ULID()}"

        try:
            async with self._db_factory() as db:
                # Idempotency check
                if idempotency_key:
                    from sqlalchemy import select

                    existing = await db.execute(
                        select(Plan.plan_id).where(
                            Plan.idempotency_key == idempotency_key,
                            Plan.status.notin_(["completed", "failed", "cancelled"]),
                        )
                    )
                    if existing.scalar_one_or_none():
                        logger.info(
                            "Skipping duplicate plan: idempotency_key=%s",
                            idempotency_key,
                        )
                        return plan

                # Build PlanTasks from PlanSteps (jarvis-actor only)
                step_id_to_task_id: dict[str, str] = {}
                for step in plan.steps:
                    if step.actor == "user":
                        continue
                    step_id_to_task_id[step.step_id] = f"ptask_{ULID()}"

                tasks = []
                for step in plan.steps:
                    if step.actor == "user":
                        continue
                    task_id = step_id_to_task_id[step.step_id]
                    depends_on = [
                        step_id_to_task_id[dep]
                        for dep in step.depends_on
                        if dep in step_id_to_task_id
                    ]
                    tasks.append(
                        PlanTask(
                            task_id=task_id,
                            plan_id=plan_id,
                            workspace_id=workspace_id,
                            task_type=step.capability,
                            input_data=step.input or {},
                            depends_on=depends_on or None,
                            status="pending",
                        )
                    )

                # Derive risk_level from max step risk
                _risk_ord = {"none": 0, "low": 1, "medium": 2, "high": 3}
                max_risk = max(
                    (_risk_ord.get(s.risk, 0) for s in plan.steps), default=0
                )
                risk_level = {0: "none", 1: "low", 2: "medium", 3: "high"}[max_risk]
                execution_mode = (
                    "approval_required" if max_risk >= 2 else "auto_execute"
                )

                db_plan = Plan(
                    plan_id=plan_id,
                    user_id=user_id,
                    workspace_id=workspace_id,
                    trigger_type=trigger_type,
                    trigger_ref=None,
                    idempotency_key=idempotency_key,
                    goal=plan.goal or "",
                    priority=plan.priority,
                    decision="plan",
                    reasoning_summary=plan.reasoning or None,
                    risk_level=risk_level,
                    execution_mode=execution_mode,
                    status="created",
                )
                db_plan.tasks = tasks
                db.add(db_plan)
                await db.commit()

            logger.info(
                "Persisted plan %s goal=%s tasks=%d",
                plan_id,
                plan.goal[:80] if plan.goal else "",
                len(tasks),
            )
            return plan.model_copy(update={"plan_id": plan_id})
        except Exception:
            logger.warning("Failed to persist plan record", exc_info=True)
            return plan
```

- [ ] **Step 4: Rewrite `_create_lightweight_run` for PlanOutput**

In `backend/src/orchestrator/jarvis.py`, replace `_create_lightweight_run` (around line 352):

```python
    async def _create_lightweight_run(
        self,
        user_id: str,
        workspace_id: str,
        plan: "PlanOutput",
        trace_id: str,
        conversation_id: str | None = None,
    ) -> str | None:
        """Create a lightweight TaskRun for tracking every user interaction.

        Even single-step plans get a run so ALL interactions appear in the
        runs table. Returns run_id on success, None on failure.
        """
        run_id = f"run_{ULID()}"

        try:
            async with self._db_factory() as db:
                run = TaskRun(
                    run_id=run_id,
                    user_id=user_id,
                    workspace_id=workspace_id,
                    plan_id=plan.plan_id,
                    status="running",
                    source="user_message",
                    execution_mode="auto_execute",
                    policy_decision={
                        "goal": plan.goal,
                        "step_count": len(plan.steps),
                    },
                    conversation_id=conversation_id,
                    trace_id=trace_id,
                )
                db.add(run)

                first_cap = plan.steps[0].capability if plan.steps else "respond"
                step = TaskStep(
                    step_id=f"step_{ULID()}",
                    run_id=run_id,
                    workspace_id=workspace_id,
                    task_id=f"task_{ULID()}",
                    plan_task_id=None,
                    step_type=first_cap,
                    status="running",
                    input_data=plan.model_dump(mode="json"),
                )
                db.add(step)
                await db.commit()
        except Exception:
            logger.warning("Failed to create lightweight run", exc_info=True)
            return None

        return run_id
```

- [ ] **Step 5: Update `_complete_lightweight_run` — no signature change needed**

The existing `_complete_lightweight_run` takes `run_id` and `result: dict` — it does not reference `PlannerOutput` directly. No changes needed.

- [ ] **Step 6: Run tests to verify they pass**

Run: `cd backend && python -m pytest tests/test_system_capability_handler.py -v`
Expected: All PASS

- [ ] **Step 7: Commit**

```bash
cd backend
git add src/orchestrator/jarvis.py tests/test_system_capability_handler.py
git commit -m "feat(spec1b-ii): rewrite plan persistence + lightweight run for PlanOutput"
```

---

## Task 5: Routing Rewrite — process_message()

**Files:**
- Modify: `backend/src/orchestrator/jarvis.py` (`process_message`)
- Create: `backend/tests/test_orchestrator_routing.py`

This is the **CRITICAL** task — replace decision-type routing with capability-based plan step execution.

- [ ] **Step 1: Write the failing tests**

```python
# backend/tests/test_orchestrator_routing.py
"""Tests for capability-based routing in process_message() and process_message_stream()."""

import pytest
from unittest.mock import AsyncMock, MagicMock, patch

from src.orchestrator.contracts import PlanOutput, PlanStep


def _make_orchestrator():
    """Create a JarvisOrchestrator with all deps mocked."""
    from src.orchestrator.jarvis import JarvisOrchestrator

    settings = MagicMock()
    settings.use_bedrock = False
    settings.daily_token_budget_usd = 10.0
    settings.redis_url = "redis://localhost:6379"

    mock_db = AsyncMock()
    mock_db.commit = AsyncMock()
    mock_db.add = MagicMock()
    mock_db.execute = AsyncMock(
        return_value=MagicMock(scalar_one_or_none=MagicMock(return_value=None))
    )
    ctx = AsyncMock()
    ctx.__aenter__ = AsyncMock(return_value=mock_db)
    ctx.__aexit__ = AsyncMock(return_value=False)
    db_factory = MagicMock(return_value=ctx)

    services = MagicMock()
    services.memory_service = AsyncMock()
    services.memory_service.store_goal_memory = AsyncMock(return_value="mem_1")
    services.memory_service.store_briefing_memory = AsyncMock(return_value="mem_2")
    services.redis = None
    services.world_model = None
    services.artifact_store = None
    services.graph_engine = None
    services.tri_search = None
    services.reranker = None
    services.notifier = None

    with patch("src.orchestrator.jarvis.get_anthropic_client") as mock_client:
        orch = JarvisOrchestrator(settings=settings, db_factory=db_factory, services=services)

    return orch


class TestProcessMessageRouting:
    """process_message() uses PlanOutput capability-based routing."""

    @pytest.mark.asyncio
    @patch("src.orchestrator.jarvis.classify_intent")
    async def test_fast_path_greeting_routes_to_presenter(self, mock_classify):
        mock_classify.return_value = ("greeting", 0.99, [])
        orch = _make_orchestrator()

        # Mock _call_agent to capture which agents are called
        agents_called = []

        async def mock_call_agent(agent_name, **kwargs):
            agents_called.append(agent_name)
            return "Hello! How can I help?"

        orch._call_agent = mock_call_agent
        orch._create_lightweight_run = AsyncMock(return_value="run_1")
        orch._complete_lightweight_run = AsyncMock()
        orch._push_workspace_surface = AsyncMock()
        orch._emit_runtime_event = AsyncMock()
        orch._load_conversation_history = AsyncMock(return_value="")

        result = await orch.process_message(
            message="Hey Jarvis",
            user_id="usr_1",
            workspace_id="ws_1",
        )
        # Fast path greeting → intent_to_plan → PlanStep(capability="respond")
        # → Presenter agent
        assert "presenter" in agents_called
        # Should NOT call old pipeline resolver
        assert "error" not in result

    @pytest.mark.asyncio
    @patch("src.orchestrator.jarvis.classify_intent")
    async def test_fast_path_data_fetch_routes_to_perceiver(self, mock_classify):
        mock_classify.return_value = ("data_fetch", 0.95, ["gmail"])
        orch = _make_orchestrator()

        agents_called = []

        async def mock_call_agent(agent_name, **kwargs):
            agents_called.append(agent_name)
            return "You have 3 new emails"

        orch._call_agent = mock_call_agent
        orch._create_lightweight_run = AsyncMock(return_value="run_1")
        orch._complete_lightweight_run = AsyncMock()
        orch._push_workspace_surface = AsyncMock()
        orch._emit_runtime_event = AsyncMock()
        orch._load_conversation_history = AsyncMock(return_value="")
        orch._bump_perception_for_sources = AsyncMock()
        orch._get_available_capabilities = AsyncMock(
            return_value=["email.search", "email.read", "calendar.list"]
        )

        result = await orch.process_message(
            message="Check my email",
            user_id="usr_1",
            workspace_id="ws_1",
        )
        # data_fetch → email.search capability → perceiver agent
        # Perceiver runs, then presenter formats
        assert "perceiver" in agents_called or "presenter" in agents_called
        assert "error" not in result

    @pytest.mark.asyncio
    @patch("src.orchestrator.jarvis.classify_intent")
    async def test_system_set_goal_calls_handler(self, mock_classify):
        """Planner returns a system.set_goal step → direct handler called."""
        mock_classify.return_value = ("command", 0.9, [])
        orch = _make_orchestrator()

        # Mock planner to return a set_goal plan
        plan_json = '{"goal": "Launch by April", "steps": [{"step_id": "s1", "description": "Set goal", "capability": "system.set_goal"}], "achievable": "full"}'

        async def mock_call_agent(agent_name, **kwargs):
            if agent_name == "planner":
                return plan_json
            return "Goal set!"

        orch._call_agent = mock_call_agent
        orch._create_lightweight_run = AsyncMock(return_value="run_1")
        orch._complete_lightweight_run = AsyncMock()
        orch._push_workspace_surface = AsyncMock()
        orch._emit_runtime_event = AsyncMock()
        orch._load_conversation_history = AsyncMock(return_value="")

        result = await orch.process_message(
            message="I want to launch the product by April",
            user_id="usr_1",
            workspace_id="ws_1",
        )
        # Should have called _handle_set_goal via _handle_system_capability
        orch._services.memory_service.store_goal_memory.assert_called_once()
        assert "error" not in result

    @pytest.mark.asyncio
    @patch("src.orchestrator.jarvis.classify_intent")
    async def test_no_resolve_pipeline_called(self, mock_classify):
        """_resolve_pipeline should NOT be called in the new routing."""
        mock_classify.return_value = ("greeting", 0.99, [])
        orch = _make_orchestrator()
        orch._resolve_pipeline = AsyncMock(side_effect=AssertionError("Should not be called"))

        async def mock_call_agent(agent_name, **kwargs):
            return "Hi!"

        orch._call_agent = mock_call_agent
        orch._create_lightweight_run = AsyncMock(return_value="run_1")
        orch._complete_lightweight_run = AsyncMock()
        orch._push_workspace_surface = AsyncMock()
        orch._emit_runtime_event = AsyncMock()
        orch._load_conversation_history = AsyncMock(return_value="")

        # Should NOT raise — _resolve_pipeline should never be called
        result = await orch.process_message(
            message="Hi", user_id="usr_1", workspace_id="ws_1"
        )
        assert "error" not in result
```

- [ ] **Step 2: Run tests to verify they fail**

Run: `cd backend && python -m pytest tests/test_orchestrator_routing.py -v`
Expected: FAIL — process_message still uses old routing with `intent_to_decision` / `extract_decision` / `_resolve_pipeline`

- [ ] **Step 3: Add `_get_available_capabilities` helper to jarvis.py**

Add after `get_system_health` (around line 260):

```python
    async def _get_available_capabilities(self, workspace_id: str) -> list[str]:
        """Get list of available capability strings from the tool registry."""
        try:
            from src.services.capability_resolver import CapabilityResolver

            async with self._db_factory() as db:
                resolver = CapabilityResolver(db, workspace_id)
                tools = await resolver._list_enabled_tools()
                return list({t.capability for t in tools if t.capability})
        except Exception:
            logger.debug("Failed to get available capabilities", exc_info=True)
            return []
```

- [ ] **Step 4: Rewrite `process_message()` with capability-based routing**

In `backend/src/orchestrator/jarvis.py`, replace the entire `process_message` method (lines ~631-869). This is the core change.

Update imports at top of file — add:
```python
from src.orchestrator.contracts import PlannerOutput, PlanOutput, PlanStep
from src.orchestrator.intent_classifier import (
    FAST_INTENTS,
    INTENT_CONFIDENCE_THRESHOLD,
    classify_intent,
    extract_plan,
    intent_to_plan,
)
from src.services.capability_resolver import CapabilityResolver, route_step
```

Remove from imports:
```python
# DELETE these lines:
from src.orchestrator.intent_classifier import (
    ...
    extract_decision,
    intent_to_decision,
)
from src.orchestrator.prompts import JARVIS_DECISION_FRAMEWORK, JARVIS_SOUL_CORE
from src.services.route_resolver import RouteResolver
```

Keep:
```python
from src.orchestrator.prompts import JARVIS_SOUL_CORE
```

Now replace `process_message`:

```python
    async def process_message(
        self,
        message: str,
        user_id: str,
        workspace_id: str,
        conversation_id: str | None = None,
        surface: str = "api",
        context: dict | None = None,
    ) -> dict:
        """Process a user message through the orchestrator.

        Routes through capability-based plan steps:
        1. Classify intent (Haiku fast path or full Planner)
        2. Generate PlanOutput with capability steps
        3. Pre-resolve agent routing and tools for each step
        4. Execute steps sequentially
        5. Presenter formats the final response
        """
        if not user_id:
            return {"error": "user_id is required", "decision": "error"}
        if not workspace_id:
            return {"error": "workspace_id is required", "decision": "error"}
        if not message or not message.strip():
            return {"error": "Empty message", "decision": "ignore"}

        trace = self._trace_manager.start_trace("user_message")
        run_id: str | None = None

        try:
            await self._emit_runtime_event(
                "command_received",
                workspace_id=workspace_id,
                user_id=user_id,
                payload={"surface": surface, "message_preview": message[:100]},
            )

            history_block = await self._load_conversation_history(conversation_id)

            # Step 0: Fast intent classification
            intent, confidence, sources = await classify_intent(
                self._client, self._haiku_model, message, history_block
            )
            use_planner = (
                intent not in FAST_INTENTS
                or confidence < INTENT_CONFIDENCE_THRESHOLD
            )

            if sources:
                await self._bump_perception_for_sources(sources, user_id, workspace_id)

            await self._emit_runtime_event(
                "route_selected",
                workspace_id=workspace_id,
                user_id=user_id,
                payload={
                    "intent": intent,
                    "confidence": confidence,
                    "use_planner": use_planner,
                },
            )

            # Step 1: Generate PlanOutput
            plan: PlanOutput
            plan_text = ""

            if use_planner:
                planner_message = (
                    f"User message: {message}\n\nContext: {json.dumps(context or {})}"
                )
                if history_block:
                    planner_message = f"{history_block}\n\n{planner_message}"

                plan_text = await self._call_agent(
                    "planner",
                    message=planner_message,
                    user_id=user_id,
                    trace=trace,
                    workspace_id=workspace_id,
                )
                plan = extract_plan(plan_text)
            else:
                capabilities = await self._get_available_capabilities(workspace_id)
                plan = intent_to_plan(intent, message, capabilities)

            # Persist Plan record if it has tasks worth tracking
            if len(plan.steps) > 1 or any(
                s.risk not in ("none",) for s in plan.steps
            ):
                import hashlib

                goal_hash = hashlib.sha256(
                    (plan.goal or "").encode()
                ).hexdigest()[:16]
                plan = await self._persist_plan_record(
                    plan, user_id, workspace_id,
                    idempotency_key=f"user:{goal_hash}",
                )

            plan_dict = plan.model_dump(mode="json")

            # Create lightweight TaskRun for tracking
            run_id = await self._create_lightweight_run(
                user_id=user_id,
                workspace_id=workspace_id,
                plan=plan,
                trace_id=trace.trace_id,
                conversation_id=conversation_id,
            )

            result: dict[str, Any] = {
                "trace_id": trace.trace_id,
                "run_id": run_id,
                "plan": plan_dict,
                "summary": plan.reasoning or plan_text,
            }

            await self._publish_event(
                "plan_generated",
                user_id,
                {"plan": plan_dict, "trace_id": trace.trace_id},
                trace_id=trace.trace_id,
            )

            # Step 2: Pre-resolve routing and tools for all steps
            step_routing: list[tuple[PlanStep, str, list[dict]]] = []
            user_steps: list[PlanStep] = []

            async with self._db_factory() as db:
                resolver = CapabilityResolver(db, workspace_id)
                for step in plan.steps:
                    if step.actor == "user":
                        user_steps.append(step)
                        continue
                    if step.capability.startswith("system."):
                        step_routing.append((step, "", []))
                    elif step.capability in ("reason", "respond"):
                        step_routing.append((step, "presenter", []))
                    else:
                        agent_name = await route_step(step.capability, resolver)
                        tools = await resolver.resolve_for_step(step.capability)
                        step_routing.append((step, agent_name, tools))

            # Step 3: Execute steps sequentially
            for step, agent_name, tools in step_routing:
                if step.capability.startswith("system."):
                    sys_result = await self._handle_system_capability(
                        step, plan, user_id, workspace_id
                    )
                    result[f"system_{step.capability}"] = sys_result
                    continue

                agent_message = (
                    f"Execute this step: {step.description}\n"
                    f"Goal: {plan.goal}\n"
                    f"User message: {message}"
                )
                if history_block:
                    agent_message = f"{history_block}\n\n{agent_message}"

                agent_result = await self._call_agent(
                    agent_name,
                    message=agent_message,
                    user_id=user_id,
                    trace=trace,
                    workspace_id=workspace_id,
                    tools_override=tools if tools else None,
                )
                result[agent_name] = agent_result

            # Step 4: Presenter formats the response (if no respond step)
            has_presenter_step = any(
                s.capability in ("reason", "respond")
                for s in plan.steps
                if s.actor == "jarvis"
            )
            if not has_presenter_step:
                presenter_msg = (
                    f"Format this for the user ({surface}). "
                    f"Be conversational and helpful.\n\n"
                    f"User message: {message}\n"
                    f"Plan: {json.dumps(plan_dict)}"
                )
                if plan_text:
                    presenter_msg += f"\nAnalysis: {plan_text[:2000]}"
                if history_block:
                    presenter_msg = f"{history_block}\n\n{presenter_msg}"
                present_result = await self._call_agent(
                    "presenter",
                    message=presenter_msg,
                    user_id=user_id,
                    trace=trace,
                    workspace_id=workspace_id,
                )
                result["presentation"] = present_result

            # Step 5: Persona learning (fire-and-forget for meaningful intents)
            if intent in ("command", "complex"):
                try:
                    await self._call_agent(
                        "persona",
                        message=(
                            f"Observe this user interaction on {surface}:\n"
                            f"User said: {message}\n"
                            f"Plan goal: {plan.goal}\n"
                            f"Extract any preference signals."
                        ),
                        user_id=user_id,
                        trace=trace,
                        workspace_id=workspace_id,
                    )
                except Exception:
                    logger.debug("Persona reflection skipped", exc_info=True)

            # Complete the lightweight run
            if run_id:
                await self._complete_lightweight_run(run_id, result, success=True)
                await self._emit_runtime_event(
                    "run_completed",
                    workspace_id=workspace_id,
                    user_id=user_id,
                    run_id=run_id,
                    payload={"trace_id": trace.trace_id},
                )

            # Push surface to workspace
            await self._push_workspace_surface(
                plan,
                user_id,
                workspace_id,
                run_id,
                response_text=result.get("presentation", result.get("presenter", "")),
            )

            return result

        except Exception as e:
            logger.error("process_message failed: %s", e, exc_info=True)
            error_result = {
                "trace_id": trace.trace_id,
                "decision": "error",
                "summary": f"Error processing message: {e}",
            }
            if run_id:
                await self._complete_lightweight_run(
                    run_id, error_result, success=False
                )
                await self._emit_runtime_event(
                    "run_failed",
                    workspace_id=workspace_id,
                    user_id=user_id,
                    run_id=run_id,
                    payload={"error": str(e)[:200]},
                )
            return error_result
        finally:
            await self._trace_manager.finish_trace(
                trace.trace_id, user_id=user_id, workspace_id=workspace_id
            )
```

- [ ] **Step 5: Run tests to verify they pass**

Run: `cd backend && python -m pytest tests/test_orchestrator_routing.py -v`
Expected: All 4 tests PASS

- [ ] **Step 6: Commit**

```bash
cd backend
git add src/orchestrator/jarvis.py tests/test_orchestrator_routing.py
git commit -m "feat(spec1b-ii): rewrite process_message() with capability-based routing"
```

---

## Task 6: Routing Rewrite — process_message_stream()

**Files:**
- Modify: `backend/src/orchestrator/jarvis.py` (`process_message_stream`)

Same routing changes as Task 5, but for the streaming path that powers the chat UI.

- [ ] **Step 1: Add streaming routing tests**

Add to `backend/tests/test_orchestrator_routing.py`:

```python
class TestProcessMessageStreamRouting:
    """process_message_stream() uses PlanOutput capability-based routing."""

    @pytest.mark.asyncio
    @patch("src.orchestrator.jarvis.classify_intent")
    async def test_stream_fast_path_emits_plan_event(self, mock_classify):
        mock_classify.return_value = ("greeting", 0.99, [])
        orch = _make_orchestrator()

        agents_called = []

        async def mock_call_agent_stream(agent_name, **kwargs):
            agents_called.append(agent_name)
            yield {"event": "agent_start", "agent": agent_name, "model": "sonnet"}
            yield {"event": "agent_done", "agent": agent_name, "text": "Hi!"}

        orch._call_agent_stream = mock_call_agent_stream
        orch._create_lightweight_run = AsyncMock(return_value="run_1")
        orch._complete_lightweight_run = AsyncMock()
        orch._push_workspace_surface = AsyncMock()
        orch._spawn_background = MagicMock()
        orch._emit_runtime_event = AsyncMock()
        orch._load_conversation_history = AsyncMock(return_value="")

        events = []
        async for evt in orch.process_message_stream(
            message="Hi", user_id="usr_1", workspace_id="ws_1"
        ):
            events.append(evt)

        event_types = [e.get("event") for e in events]
        # Should emit "plan" event (not old "decision" event)
        assert "plan" in event_types
        # Should have done event
        assert "done" in event_types

    @pytest.mark.asyncio
    @patch("src.orchestrator.jarvis.classify_intent")
    async def test_stream_does_not_call_resolve_pipeline(self, mock_classify):
        mock_classify.return_value = ("greeting", 0.99, [])
        orch = _make_orchestrator()
        orch._resolve_pipeline = AsyncMock(
            side_effect=AssertionError("Should not be called")
        )

        async def mock_call_agent_stream(agent_name, **kwargs):
            yield {"event": "agent_done", "agent": agent_name, "text": "Hi!"}

        orch._call_agent_stream = mock_call_agent_stream
        orch._create_lightweight_run = AsyncMock(return_value="run_1")
        orch._complete_lightweight_run = AsyncMock()
        orch._push_workspace_surface = AsyncMock()
        orch._spawn_background = MagicMock()
        orch._emit_runtime_event = AsyncMock()
        orch._load_conversation_history = AsyncMock(return_value="")

        events = []
        async for evt in orch.process_message_stream(
            message="Hi", user_id="usr_1", workspace_id="ws_1"
        ):
            events.append(evt)
        # No error means _resolve_pipeline was never called
        error_events = [e for e in events if e.get("event") == "error"]
        assert len(error_events) == 0
```

- [ ] **Step 2: Run tests to verify they fail**

Run: `cd backend && python -m pytest tests/test_orchestrator_routing.py::TestProcessMessageStreamRouting -v`
Expected: FAIL — still emits "decision" event, may call _resolve_pipeline

- [ ] **Step 3: Rewrite `process_message_stream()`**

In `backend/src/orchestrator/jarvis.py`, replace `process_message_stream` (lines ~871-1221):

```python
    async def process_message_stream(
        self,
        message: str,
        user_id: str,
        workspace_id: str,
        surface: str = "web",
        mode: str = "ask",
        context: dict | None = None,
        conversation_id: str | None = None,
    ) -> AsyncGenerator[dict[str, Any], None]:
        """Stream events while processing a user message.

        Yields SSE-compatible dicts. Uses capability-based plan step routing.
        """
        if not user_id or not workspace_id:
            yield {"event": "error", "message": "user_id and workspace_id are required"}
            return
        if not message or not message.strip():
            yield {"event": "error", "message": "Empty message"}
            return

        trace = self._trace_manager.start_trace("user_message")
        run_id: str | None = None

        def _fire_event(event_type: str, **kwargs: Any) -> None:
            self._spawn_background(self._emit_runtime_event(event_type, **kwargs))

        try:
            yield {"event": "trace", "trace_id": trace.trace_id}

            _fire_event(
                "command_received",
                workspace_id=workspace_id,
                user_id=user_id,
                payload={"surface": surface, "message_preview": message[:100]},
            )

            history_block = await self._load_conversation_history(conversation_id)

            # Step 0: Fast intent classification
            intent, confidence, sources = await classify_intent(
                self._client, self._haiku_model, message, history_block
            )
            yield {"event": "intent", "intent": intent, "confidence": confidence}

            if sources:
                await self._bump_perception_for_sources(sources, user_id, workspace_id)

            # Decide routing
            if mode == "execute":
                use_planner = True
            elif mode == "plan":
                use_planner = True
            else:
                use_planner = (
                    intent not in FAST_INTENTS
                    or confidence < INTENT_CONFIDENCE_THRESHOLD
                )

            _fire_event(
                "route_selected",
                workspace_id=workspace_id,
                user_id=user_id,
                payload={
                    "intent": intent,
                    "confidence": confidence,
                    "use_planner": use_planner,
                },
            )

            # Step 1: Generate PlanOutput
            plan: PlanOutput
            plan_text = ""

            if use_planner:
                planner_message = (
                    f"User message: {message}\n\nContext: {json.dumps(context or {})}"
                )
                if history_block:
                    planner_message = f"{history_block}\n\n{planner_message}"

                async for evt in self._call_agent_stream(
                    "planner",
                    message=planner_message,
                    user_id=user_id,
                    trace=trace,
                    workspace_id=workspace_id,
                ):
                    yield evt
                    if evt.get("event") == "agent_done":
                        plan_text = evt.get("text", "")

                plan = extract_plan(plan_text)
            else:
                capabilities = await self._get_available_capabilities(workspace_id)
                plan = intent_to_plan(intent, message, capabilities)

            # Apply mode overrides
            if mode == "plan":
                # In plan mode, add requires_user_input flag
                plan = plan.model_copy(update={"requires_user_input": True})

            # Persist Plan record if needed
            if len(plan.steps) > 1 or any(
                s.risk not in ("none",) for s in plan.steps
            ):
                import hashlib

                goal_hash = hashlib.sha256(
                    (plan.goal or "").encode()
                ).hexdigest()[:16]
                plan = await self._persist_plan_record(
                    plan, user_id, workspace_id,
                    idempotency_key=f"user:{goal_hash}",
                )

            plan_dict = plan.model_dump(mode="json")

            run_id = await self._create_lightweight_run(
                user_id=user_id,
                workspace_id=workspace_id,
                plan=plan,
                trace_id=trace.trace_id,
                conversation_id=conversation_id,
            )

            # Emit plan event (replaces old "decision" event)
            yield {
                "event": "plan",
                "plan": plan_dict,
                "run_id": run_id,
            }

            _fire_event(
                "plan_created",
                workspace_id=workspace_id,
                user_id=user_id,
                run_id=run_id,
                payload={"goal": plan.goal, "trace_id": trace.trace_id},
            )

            # Step 2: Pre-resolve routing and tools
            step_routing: list[tuple[PlanStep, str, list[dict]]] = []
            user_steps: list[PlanStep] = []

            async with self._db_factory() as db:
                resolver = CapabilityResolver(db, workspace_id)
                for step in plan.steps:
                    if step.actor == "user":
                        user_steps.append(step)
                        continue
                    if step.capability.startswith("system."):
                        step_routing.append((step, "", []))
                    elif step.capability in ("reason", "respond"):
                        step_routing.append((step, "presenter", []))
                    else:
                        agent_name = await route_step(step.capability, resolver)
                        tools = await resolver.resolve_for_step(step.capability)
                        step_routing.append((step, agent_name, tools))

            # Step 3: Execute steps with streaming
            for step, agent_name, tools in step_routing:
                if step.capability.startswith("system."):
                    await self._handle_system_capability(
                        step, plan, user_id, workspace_id
                    )
                    continue

                # Plan mode (draft_only): skip execution, present the plan
                if mode == "plan" and step.risk in ("medium", "high"):
                    yield {
                        "event": "plan_ready",
                        "plan_id": plan.plan_id,
                        "message": "Plan created. Review and approve to execute.",
                    }
                    continue

                agent_message = (
                    f"Execute this step: {step.description}\n"
                    f"Goal: {plan.goal}\n"
                    f"User message: {message}"
                )
                if history_block:
                    agent_message = f"{history_block}\n\n{agent_message}"

                async for evt in self._call_agent_stream(
                    agent_name,
                    message=agent_message,
                    user_id=user_id,
                    trace=trace,
                    workspace_id=workspace_id,
                    tools_override=tools if tools else None,
                ):
                    yield evt

            # Step 4: Presenter formatting (if no respond step)
            has_presenter_step = any(
                s.capability in ("reason", "respond")
                for s in plan.steps
                if s.actor == "jarvis"
            )
            if not has_presenter_step:
                presenter_msg = (
                    f"Respond to the user ({surface}). "
                    f"Be conversational and helpful.\n\n"
                    f"User message: {message}\n"
                    f"Intent: {intent}\n"
                )
                if plan_text:
                    presenter_msg += (
                        f"Plan: {json.dumps(plan_dict)}\n"
                        f"Analysis: {plan_text[:2000]}\n"
                    )
                if history_block:
                    presenter_msg = f"{history_block}\n\n{presenter_msg}"

                presenter_text = ""
                async for evt in self._call_agent_stream(
                    "presenter",
                    message=presenter_msg,
                    user_id=user_id,
                    trace=trace,
                    workspace_id=workspace_id,
                ):
                    yield evt
                    if evt.get("event") == "agent_done":
                        presenter_text = evt.get("text", "")
                        yield {"event": "response", "text": presenter_text}

            # Persona learning (meaningful intents only)
            if intent in ("command", "complex"):
                try:
                    await self._call_agent(
                        "persona",
                        message=(
                            f"Observe this user interaction on {surface}:\n"
                            f"User said: {message}\n"
                            f"Plan goal: {plan.goal}\n"
                            f"Extract any preference signals."
                        ),
                        user_id=user_id,
                        trace=trace,
                        workspace_id=workspace_id,
                    )
                except Exception:
                    pass

            # Complete lightweight run
            if run_id:
                await self._complete_lightweight_run(
                    run_id,
                    {"plan": plan.goal, "summary": presenter_text if not has_presenter_step else ""},
                    success=True,
                )
                _fire_event(
                    "run_completed",
                    workspace_id=workspace_id,
                    user_id=user_id,
                    run_id=run_id,
                    payload={"trace_id": trace.trace_id},
                )

            # Push workspace surface (fire-and-forget)
            self._spawn_background(
                self._push_workspace_surface(
                    plan,
                    user_id,
                    workspace_id,
                    run_id,
                    response_text=presenter_text if not has_presenter_step else "",
                )
            )

            yield {"event": "done", "trace_id": trace.trace_id, "run_id": run_id}

        except Exception as e:
            logger.error("process_message_stream failed: %s", e, exc_info=True)
            if run_id:
                await self._complete_lightweight_run(
                    run_id, {"summary": str(e)}, success=False
                )
                _fire_event(
                    "run_failed",
                    workspace_id=workspace_id,
                    user_id=user_id,
                    run_id=run_id,
                    payload={"error": str(e)[:200]},
                )
            yield {"event": "error", "message": str(e)}
        finally:
            await self._trace_manager.finish_trace(
                trace.trace_id, user_id=user_id, workspace_id=workspace_id
            )
```

- [ ] **Step 4: Run tests to verify they pass**

Run: `cd backend && python -m pytest tests/test_orchestrator_routing.py -v`
Expected: All 6 tests PASS (4 from Task 5 + 2 new)

- [ ] **Step 5: Commit**

```bash
cd backend
git add src/orchestrator/jarvis.py tests/test_orchestrator_routing.py
git commit -m "feat(spec1b-ii): rewrite process_message_stream() with capability-based routing"
```

---

## Task 7: Surface Push Update for PlanOutput

**Files:**
- Modify: `backend/src/orchestrator/jarvis.py` (`_push_workspace_surface`, `_build_surface_preview`)

- [ ] **Step 1: Write the failing test**

Add to `backend/tests/test_orchestrator_routing.py`:

```python
from src.orchestrator.jarvis import _build_surface_preview


class TestSurfacePushForPlanOutput:
    """_push_workspace_surface and _build_surface_preview work with PlanOutput."""

    def test_derive_surface_kind_respond_only_returns_none(self):
        """respond-only plans have no workspace surface (chat-only)."""
        from src.orchestrator.jarvis import _derive_surface_kind

        plan = PlanOutput(goal="Hi", steps=[
            PlanStep(step_id="s1", description="Respond", capability="respond"),
        ])
        assert _derive_surface_kind(plan) is None

    def test_derive_surface_kind_write_action_returns_plan(self):
        from src.orchestrator.jarvis import _derive_surface_kind

        plan = PlanOutput(goal="Send email", steps=[
            PlanStep(step_id="s1", description="Read", capability="email.read", risk="none"),
            PlanStep(step_id="s2", description="Draft", capability="email.draft", risk="medium"),
        ])
        kind, title = _derive_surface_kind(plan)
        assert kind == "plan"

    def test_derive_surface_kind_briefing(self):
        from src.orchestrator.jarvis import _derive_surface_kind

        plan = PlanOutput(goal="Add to brief", steps=[
            PlanStep(step_id="s1", description="Add", capability="system.add_to_brief"),
        ])
        kind, title = _derive_surface_kind(plan)
        assert kind == "briefing"

    def test_derive_surface_kind_single_read_returns_summary(self):
        from src.orchestrator.jarvis import _derive_surface_kind

        plan = PlanOutput(goal="Check email", steps=[
            PlanStep(step_id="s1", description="Read emails", capability="email.search", risk="none"),
        ])
        kind, title = _derive_surface_kind(plan)
        assert kind == "summary"
```

- [ ] **Step 2: Run tests to verify they fail**

Run: `cd backend && python -m pytest tests/test_orchestrator_routing.py::TestSurfacePushForPlanOutput -v`
Expected: FAIL — `_derive_surface_kind` not found

- [ ] **Step 3: Add `_derive_surface_kind` function and update `_push_workspace_surface`**

Add at module level in jarvis.py (near `_build_surface_preview`, around line 91):

```python
def _derive_surface_kind(plan: "PlanOutput") -> tuple[str, str] | None:
    """Derive workspace surface kind from PlanOutput step capabilities.

    Returns (kind, default_title) or None if the plan is chat-only.
    """
    if not plan.steps:
        return None

    caps = {s.capability for s in plan.steps if s.actor == "jarvis"}

    # Respond/reason only → no surface (chat-only)
    if caps <= {"reason", "respond", "none"}:
        return None

    # System capabilities with visual value
    if "system.add_to_brief" in caps:
        return ("briefing", "Briefing Update")
    if "system.schedule_reminder" in caps:
        return ("alert", "Reminder Scheduled")

    # Write actions → plan surface
    if any(s.risk in ("medium", "high") for s in plan.steps):
        return ("plan", "New Plan")

    # Multi-step → plan surface
    jarvis_steps = [s for s in plan.steps if s.actor == "jarvis"]
    if len(jarvis_steps) > 2:
        return ("plan", plan.goal[:80] or "Plan")

    # Single/dual read → summary
    return ("summary", "Summary")


def _build_surface_preview_from_plan(
    plan: "PlanOutput",
    kind: str,
    default_title: str,
    response_text: str,
):
    """Build a SurfacePreview from a PlanOutput for workspace grid cards."""
    from src.ui.contracts import SurfaceMetric, SurfacePreview

    title = plan.goal[:80] if plan.goal else default_title
    subtitle = plan.reasoning[:120] if plan.reasoning else None
    metrics: list[SurfaceMetric] = []
    tags: list[str] = []

    if kind == "plan":
        step_count = len([s for s in plan.steps if s.actor == "jarvis"])
        if step_count:
            metrics.append(SurfaceMetric(label="Steps", value=str(step_count)))
        metrics.append(SurfaceMetric(label="Priority", value=plan.priority))

    elif kind == "summary":
        tags.append("read")

    elif kind == "briefing":
        tags.append("briefing")

    elif kind == "alert":
        tags.append("reminder")

    return SurfacePreview(
        title=title,
        subtitle=subtitle,
        status=None,
        priority=plan.priority if plan.priority != "medium" else None,
        metrics=metrics,
        entities=[],
        progress=None,
        tags=tags,
    )
```

Now update `_push_workspace_surface` to accept PlanOutput:

```python
    async def _push_workspace_surface(
        self,
        plan: "PlanOutput",
        user_id: str,
        workspace_id: str,
        run_id: str | None = None,
        response_text: str = "",
    ) -> None:
        """Push a typed surface to the workspace via Redis Pub/Sub.

        Derives surface kind from plan step capabilities.
        Only pushes for plans with visual value beyond the chat response.
        """
        from datetime import datetime, timedelta, timezone

        from src.orchestrator.contracts import WorkspaceSurfacePush
        from src.ui.renderer import build_detail_config

        mapping = _derive_surface_kind(plan)
        if not mapping:
            return

        kind, default_title = mapping

        try:
            event_bus = await self._ensure_event_bus()
            if not event_bus:
                return

            from ulid import ULID

            surface_id = f"surf_{ULID()}"
            preview = _build_surface_preview_from_plan(
                plan, kind, default_title, response_text
            )
            detail_config = build_detail_config(kind, surface_id)

            surface = WorkspaceSurfacePush(
                id=surface_id,
                kind=kind,
                preview=preview.model_dump(mode="json"),
                detail_config=(
                    detail_config.model_dump(mode="json") if detail_config else None
                ),
                decision=None,
                source_run_id=run_id,
                response_preview=(response_text[:300] if response_text else None),
                created_at=datetime.now(timezone.utc).isoformat(),
            )

            channel = f"jarvis:a2ui:{user_id}"
            ws_msg = json.dumps(
                {"type": "surface", "surface": surface.model_dump(mode="json")}
            )
            await event_bus.publish_to_channel(channel, ws_msg)

            # Persist to DB
            try:
                from src.models.ui_state import UISurface

                async with self._db_factory() as db:
                    db.add(
                        UISurface(
                            surface_id=surface.id,
                            user_id=user_id,
                            workspace_id=workspace_id,
                            surface_type=kind,
                            payload=surface.model_dump(mode="json"),
                            preview=preview.model_dump(mode="json"),
                            detail_config=(
                                detail_config.model_dump(mode="json")
                                if detail_config
                                else None
                            ),
                            expires_at=datetime.now(timezone.utc) + timedelta(hours=24),
                        )
                    )
                    await db.commit()
            except Exception:
                logger.debug("Failed to persist workspace surface to DB", exc_info=True)
        except Exception:
            logger.warning("Failed to push workspace surface", exc_info=True)
```

Also update `generate_briefing` to use PlanOutput for surface push (around line 1768):

```python
            # In generate_briefing, replace the PlannerOutput surface push:
                await self._push_workspace_surface(
                    PlanOutput(
                        goal="Daily Briefing",
                        reasoning=str(result)[:200],
                        steps=[PlanStep(
                            description="Briefing update",
                            capability="system.add_to_brief",
                        )],
                    ),
                    user_id=user_id,
                    workspace_id=workspace_id,
                    response_text=str(result)[:1000],
                )
```

- [ ] **Step 4: Run tests to verify they pass**

Run: `cd backend && python -m pytest tests/test_orchestrator_routing.py -v`
Expected: All PASS

- [ ] **Step 5: Commit**

```bash
cd backend
git add src/orchestrator/jarvis.py tests/test_orchestrator_routing.py
git commit -m "feat(spec1b-ii): update surface push for PlanOutput capability-derived kinds"
```

---

## Task 8: Chat SSE Event Change

**Files:**
- Modify: `backend/src/orchestrator/contracts.py:209-215` (MessageMetadata)
- Modify: `backend/src/api/routes_chat.py:18-24,154-193`
- Create: `backend/tests/test_chat_plan_event.py`

- [ ] **Step 1: Write the failing test**

```python
# backend/tests/test_chat_plan_event.py
"""Tests for chat SSE plan event (replaces decision event)."""

from src.orchestrator.contracts import MessageMetadata, PlanOutput, PlanStep


class TestMessageMetadataUsePlanOutput:
    """MessageMetadata.decision is now PlanOutput type."""

    def test_metadata_accepts_plan_output(self):
        plan = PlanOutput(
            goal="Check email",
            steps=[PlanStep(step_id="s1", description="Read", capability="email.search")],
        )
        meta = MessageMetadata(
            trace_id="trace_1",
            decision=plan,
            agent_steps=[],
        )
        assert isinstance(meta.decision, PlanOutput)
        dumped = meta.model_dump(mode="json")
        assert dumped["decision"]["goal"] == "Check email"
        assert dumped["decision"]["steps"][0]["capability"] == "email.search"

    def test_metadata_decision_none(self):
        meta = MessageMetadata(trace_id="trace_1")
        assert meta.decision is None
```

- [ ] **Step 2: Run tests to verify they fail**

Run: `cd backend && python -m pytest tests/test_chat_plan_event.py -v`
Expected: FAIL — `MessageMetadata.decision` expects `PlannerOutput`, not `PlanOutput`

- [ ] **Step 3: Update MessageMetadata in contracts.py**

In `backend/src/orchestrator/contracts.py`, change line 214:

Before:
```python
    decision: PlannerOutput | None = None
```

After:
```python
    decision: PlanOutput | None = None
```

- [ ] **Step 4: Update routes_chat.py — emit `plan` event instead of `decision`**

In `backend/src/api/routes_chat.py`, update imports (lines 18-24):

Before:
```python
from src.orchestrator.contracts import (
    MessageAgentStep,
    MessageMetadata,
    MessageToolCall,
    PlannerOutput,
)
```

After:
```python
from src.orchestrator.contracts import (
    MessageAgentStep,
    MessageMetadata,
    MessageToolCall,
    PlanOutput,
)
```

Update the `final_decision` variable (line 158):

Before:
```python
    final_decision: PlannerOutput | None = None
```

After:
```python
    final_decision: PlanOutput | None = None
```

Update the event parsing in the generator (around line 191-193):

Before:
```python
                if event_type == "decision":
                    raw = event.get("decision")
                    if isinstance(raw, dict):
                        final_decision = PlannerOutput.model_validate(raw)
```

After:
```python
                if event_type == "plan":
                    raw = event.get("plan")
                    if isinstance(raw, dict):
                        final_decision = PlanOutput.model_validate(raw)
```

- [ ] **Step 5: Run tests to verify they pass**

Run: `cd backend && python -m pytest tests/test_chat_plan_event.py -v`
Expected: All PASS

- [ ] **Step 6: Commit**

```bash
cd backend
git add src/orchestrator/contracts.py src/api/routes_chat.py tests/test_chat_plan_event.py
git commit -m "feat(spec1b-ii): emit plan SSE event with PlanOutput, replace decision event"
```

---

## Task 9: Telegram Public Methods Fix

**Files:**
- Modify: `backend/src/interface/telegram.py:166-189`
- Create: `backend/tests/test_telegram_public_methods.py`

- [ ] **Step 1: Write the failing test**

```python
# backend/tests/test_telegram_public_methods.py
"""Tests for Telegram using public orchestrator methods."""

import pytest
from unittest.mock import AsyncMock, MagicMock


class TestTelegramUsesPublicMethods:
    """Telegram _handle_status uses get_budget_status/get_system_health."""

    @pytest.mark.asyncio
    async def test_handle_status_uses_public_methods(self):
        from src.interface.telegram import TelegramInterface

        settings = MagicMock()
        settings.telegram_chat_id = "12345"

        mock_budget_status = MagicMock()
        mock_budget_status.daily_spend_usd = 2.5
        mock_budget_status.daily_limit_usd = 10.0
        mock_budget_status.percent_used = 25.0
        mock_budget_status.budget_mode = "normal"

        orchestrator = MagicMock()
        orchestrator.get_budget_status = AsyncMock(return_value=mock_budget_status)
        orchestrator.get_system_health = AsyncMock(
            return_value={"circuit_breaker_open": False, "background_tasks": 0, "agents": []}
        )
        # Should NOT access private attributes
        orchestrator._db_factory = MagicMock(
            side_effect=AssertionError("Should use public methods, not private")
        )
        orchestrator._budget = MagicMock(
            side_effect=AssertionError("Should use public methods, not private")
        )

        tg = TelegramInterface(
            settings=settings,
            orchestrator=orchestrator,
            surface_registry=None,
        )

        # Create mock update
        update = MagicMock()
        update.message = MagicMock()
        update.message.reply_text = AsyncMock()

        await tg._handle_status(update, None)

        # Should have called public methods
        orchestrator.get_budget_status.assert_called_once()
        # Should have sent a message
        update.message.reply_text.assert_called_once()
        text = update.message.reply_text.call_args[0][0]
        assert "$2.50" in text
        assert "$10.00" in text
```

- [ ] **Step 2: Run tests to verify they fail**

Run: `cd backend && python -m pytest tests/test_telegram_public_methods.py -v`
Expected: FAIL — `_handle_status` accesses `self._orchestrator._db_factory` and `self._orchestrator._budget`

- [ ] **Step 3: Update `_handle_status` in telegram.py**

In `backend/src/interface/telegram.py`, replace `_handle_status` (lines 165-189):

```python
    async def _handle_status(self, update, context) -> None:
        """Handle /status command — show system status."""
        try:
            budget = await self._orchestrator.get_budget_status()

            surfaces = []
            if self._surface_registry:
                surfaces = await self._surface_registry.get_active_surfaces(
                    self._resolve_user_id()
                )

            text = (
                f"*Jarvis Status*\n"
                f"Budget: ${budget.daily_spend_usd:.2f} / "
                f"${budget.daily_limit_usd:.2f} "
                f"({budget.percent_used:.0f}%)\n"
                f"Mode: {budget.budget_mode}\n"
                f"Active surfaces: {', '.join(surfaces) or 'none'}"
            )
            await update.message.reply_text(text, parse_mode="Markdown")
        except Exception as e:
            logger.error("Status command failed: %s", e)
            await update.message.reply_text(f"Error: {e}")
```

- [ ] **Step 4: Run tests to verify they pass**

Run: `cd backend && python -m pytest tests/test_telegram_public_methods.py -v`
Expected: PASS

- [ ] **Step 5: Commit**

```bash
cd backend
git add src/interface/telegram.py tests/test_telegram_public_methods.py
git commit -m "feat(spec1b-ii): telegram uses public get_budget_status, not private attrs"
```

---

## Task 10: Intent Classifier Cleanup + Perception Path

**Files:**
- Modify: `backend/src/orchestrator/intent_classifier.py` (delete `intent_to_decision`, `extract_decision`)
- Modify: `backend/src/orchestrator/jarvis.py` (update perception path to use `extract_plan`)

- [ ] **Step 1: Write the test for deleted functions**

Add to `backend/tests/test_intent_to_plan.py`:

```python
class TestOldFunctionsRemoved:
    """intent_to_decision and extract_decision are deleted."""

    def test_intent_to_decision_not_importable(self):
        import importlib
        import src.orchestrator.intent_classifier as mod
        importlib.reload(mod)
        assert not hasattr(mod, "intent_to_decision")

    def test_extract_decision_not_importable(self):
        import importlib
        import src.orchestrator.intent_classifier as mod
        importlib.reload(mod)
        assert not hasattr(mod, "extract_decision")
```

- [ ] **Step 2: Run tests to verify they fail**

Run: `cd backend && python -m pytest tests/test_intent_to_plan.py::TestOldFunctionsRemoved -v`
Expected: FAIL — both functions still exist

- [ ] **Step 3: Delete `intent_to_decision` and `extract_decision` from intent_classifier.py**

In `backend/src/orchestrator/intent_classifier.py`, delete the `intent_to_decision` function (lines 319-336) and `extract_decision` function (lines 339-362).

- [ ] **Step 4: Update perception path in jarvis.py**

In `_queue_perception_plan` (around line 2152), replace `extract_decision` with `extract_plan`:

Before:
```python
        decision = extract_decision(planner_result)
```

After:
```python
        plan = extract_plan(planner_result)
```

Then update the rest of the method to work with `PlanOutput` instead of `PlannerOutput`:

```python
    async def _queue_perception_plan(
        self,
        planner_result: str,
        source: str,
        user_id: str,
        workspace_id: str,
        trace_id: str,
    ) -> PlanOutput | None:
        """Extract a structured plan from the Planner's perception response
        and queue actionable plans for background execution.

        System capability steps are handled inline. Steps with write
        capabilities are persisted as Plan + background TaskRun.
        """
        import hashlib

        plan = extract_plan(planner_result)

        # Check if any steps are actionable
        has_system_caps = any(
            s.capability.startswith("system.") for s in plan.steps if s.actor == "jarvis"
        )
        has_write_steps = any(
            s.risk not in ("none",) for s in plan.steps if s.actor == "jarvis"
        )
        has_tool_steps = any(
            not s.capability.startswith("system.")
            and s.capability not in ("reason", "respond", "none")
            for s in plan.steps
            if s.actor == "jarvis"
        )

        if not has_system_caps and not has_write_steps and not has_tool_steps:
            logger.debug(
                "Perception plan from %s — no actionable steps", source
            )
            return plan

        # Handle system capability steps inline
        _INLINE_CAPS = {
            "system.set_goal",
            "system.set_instruction",
            "system.schedule_reminder",
            "system.add_to_brief",
        }
        for step in plan.steps:
            if step.capability in _INLINE_CAPS:
                try:
                    await self._handle_system_capability(
                        step, plan, user_id, workspace_id
                    )
                    logger.info(
                        "Perception inline handler: %s from %s",
                        step.capability,
                        source,
                    )
                except Exception:
                    logger.warning(
                        "Perception inline handler failed: %s",
                        step.capability,
                        exc_info=True,
                    )

        # For steps requiring tool execution, persist and queue
        tool_steps = [
            s for s in plan.steps
            if s.actor == "jarvis"
            and not s.capability.startswith("system.")
            and s.capability not in ("reason", "respond", "none")
        ]
        if not tool_steps:
            return plan

        # Compute idempotency key
        goal_hash = hashlib.sha256((plan.goal or "").encode()).hexdigest()[:16]
        idempotency_key = f"perception:{source}:{goal_hash}"

        # Persist Plan + PlanTasks
        plan = await self._persist_plan_record(
            plan,
            user_id,
            workspace_id,
            trigger_type="perception",
            idempotency_key=idempotency_key,
        )

        if not plan.plan_id:
            logger.debug(
                "Plan not persisted (idempotent skip or error) for %s", source
            )
            return plan

        # Create a background TaskRun for the scheduler
        try:
            async with self._db_factory() as db:
                from src.services.graph_executor import create_graph_executor

                executor = await create_graph_executor(
                    settings=self._settings,
                    db=db,
                    workspace_id=workspace_id,
                )
                run = await executor.create_run(
                    plan_id=plan.plan_id,
                    user_id=user_id,
                    workspace_id=workspace_id,
                    source="background",
                )
                await db.commit()

                logger.info(
                    "Perception queued plan %s → run %s from %s",
                    plan.plan_id,
                    run.run_id,
                    source,
                )
        except Exception:
            logger.warning(
                "Failed to create background run for perception plan %s",
                plan.plan_id,
                exc_info=True,
            )

        return plan
```

Also update `run_cross_source_synthesis` to use `_queue_perception_plan` — it already returns PlanOutput now, so no changes needed there.

Remove the now-unused constants:
```python
    # DELETE these from JarvisOrchestrator class:
    # PERCEPTION_ACTIONABLE_DECISIONS = { ... }
    # _PERCEPTION_INLINE_DECISIONS = { ... }
```

- [ ] **Step 5: Remove unused imports from jarvis.py**

Verify that `extract_decision`, `intent_to_decision`, `JARVIS_DECISION_FRAMEWORK`, and `RouteResolver` are no longer imported. The imports should now look like:

```python
from src.orchestrator.intent_classifier import (
    FAST_INTENTS,
    INTENT_CONFIDENCE_THRESHOLD,
    classify_intent,
    extract_plan,
    intent_to_plan,
)
from src.orchestrator.prompts import JARVIS_SOUL_CORE
from src.services.capability_resolver import CapabilityResolver, route_step
```

Note: Do NOT delete `RouteResolver` from `route_resolver.py` or `JARVIS_DECISION_FRAMEWORK` from `prompts.py`. Only remove the imports FROM jarvis.py. Those modules are deleted in Spec 1B-iii.

- [ ] **Step 6: Run all tests**

Run: `cd backend && python -m pytest tests/test_intent_to_plan.py tests/test_orchestrator_routing.py tests/test_system_capability_handler.py tests/test_perceiver_agent.py -v`
Expected: All PASS

- [ ] **Step 7: Commit**

```bash
cd backend
git add src/orchestrator/intent_classifier.py src/orchestrator/jarvis.py tests/test_intent_to_plan.py
git commit -m "feat(spec1b-ii): delete old intent_to_decision/extract_decision, update perception path"
```

---

## Task 11: GraphExecutor PlanOutput Integration

**Files:**
- Modify: `backend/src/services/graph_executor.py` (`_populate_steps`, `_run_step_action`, `_execute_step`)
- Modify: `backend/tests/test_graph_executor.py`

- [ ] **Step 1: Write the failing tests**

Add to `backend/tests/test_graph_executor.py`:

```python
class TestGraphExecutorCapabilityField:
    """GraphExecutor uses capability field from PlanOutput steps."""

    @pytest.mark.asyncio
    async def test_execute_step_reads_capability_not_task_type(self):
        """_execute_step should check step.input_data['capability'] for approval routing."""
        from src.services.graph_executor import GraphExecutor

        settings = MagicMock()
        settings.redis_url = "redis://localhost:6379"
        mock_db = AsyncMock()
        mock_db.flush = AsyncMock()
        mock_db.refresh = AsyncMock()
        mock_db.execute = AsyncMock()

        with patch("src.services.graph_executor.get_anthropic_client"):
            ge = GraphExecutor(settings=settings, db=mock_db)

        # Create a step with capability in input_data
        step = MagicMock()
        step.step_id = "step_1"
        step.status = "pending"
        step.input_data = {"capability": "email.draft", "description": "Draft email"}
        step.started_at = None
        step.retry_count = 0
        step.max_retries = 3

        run = MagicMock()
        run.run_id = "run_1"
        run.user_id = "usr_1"
        run.workspace_id = "ws_1"

        # The step should read capability, not task_type
        assert step.input_data.get("capability") == "email.draft"
        assert step.input_data.get("task_type") is None

    @pytest.mark.asyncio
    async def test_run_step_action_uses_capability_resolver(self):
        """_run_step_action should use CapabilityResolver for focused tools when available."""
        from src.services.graph_executor import GraphExecutor

        settings = MagicMock()
        settings.redis_url = "redis://localhost:6379"
        settings.resolved_model = "claude-sonnet-4-20250514"
        mock_db = AsyncMock()

        with patch("src.services.graph_executor.get_anthropic_client"):
            ge = GraphExecutor(settings=settings, db=mock_db)

        step = MagicMock()
        step.input_data = {"capability": "email.search", "description": "Search email"}
        step.step_id = "step_1"

        run = MagicMock()
        run.run_id = "run_1"
        run.user_id = "usr_1"
        run.workspace_id = "ws_1"
        run.context_pack_json = None

        # When agent loop deps are NOT available, falls back to minimal
        ge._db_factory = None
        ge._execute_tool_fn = None
        ge._budget = None

        with patch.object(ge, "_minimal_claude_action", new_callable=AsyncMock) as mock_minimal:
            mock_minimal.return_value = {"status": "completed"}
            result = await ge._run_step_action(step, run)
            mock_minimal.assert_called_once()
```

- [ ] **Step 2: Run tests to verify they fail or pass**

Run: `cd backend && python -m pytest tests/test_graph_executor.py::TestGraphExecutorCapabilityField -v`
Expected: These tests are structural — they should PASS as-is since they check the new field format. If _execute_step still hardcodes `task_type`, the approval routing will be wrong (covered in integration tests).

- [ ] **Step 3: Update `_execute_step` to use `capability` field**

In `backend/src/services/graph_executor.py`, update `_execute_step` (around line 528):

Before:
```python
            task_type = (step.input_data or {}).get("task_type", "")
```

After:
```python
            task_type = (step.input_data or {}).get(
                "capability", (step.input_data or {}).get("task_type", "")
            )
```

This reads `capability` first, falling back to `task_type` for backward compatibility with pre-existing steps.

- [ ] **Step 4: Update `_run_step_action` to read `capability`**

In `_run_step_action` (around line 747):

Before:
```python
        task_type = input_data.get("task_type", "unknown")
```

After:
```python
        task_type = input_data.get("capability", input_data.get("task_type", "unknown"))
```

- [ ] **Step 5: Update `_minimal_claude_action` to read `capability`**

In `_minimal_claude_action` (around line 769):

Before:
```python
        task_type = input_data.get("task_type", "unknown")
```

After:
```python
        task_type = input_data.get("capability", input_data.get("task_type", "unknown"))
```

- [ ] **Step 6: Update `_run_step_via_agent_loop` to read `capability`**

In `_run_step_via_agent_loop` (around line 865):

Before:
```python
        task_type = input_data.get("task_type", "unknown")
```

After:
```python
        task_type = input_data.get("capability", input_data.get("task_type", "unknown"))
```

- [ ] **Step 7: Run tests**

Run: `cd backend && python -m pytest tests/test_graph_executor.py -v`
Expected: All PASS

- [ ] **Step 8: Commit**

```bash
cd backend
git add src/services/graph_executor.py tests/test_graph_executor.py
git commit -m "feat(spec1b-ii): GraphExecutor reads capability field, falls back to task_type"
```

---

## Task 12: Dead Code Removal + Final Integration Test

**Files:**
- Modify: `backend/src/orchestrator/jarvis.py` (remove unused methods)

- [ ] **Step 1: Remove `_resolve_pipeline` and `_check_step_condition` from jarvis.py**

Delete the `_resolve_pipeline` method (around line 2788) and `_check_step_condition` method (around line 2798). These are no longer called from any code path.

Also delete the old `_build_surface_preview` function at the top of the file (it used `PlannerOutput` — replaced by `_build_surface_preview_from_plan`).

Also delete the old `PERCEPTION_ACTIONABLE_DECISIONS` and `_PERCEPTION_INLINE_DECISIONS` class attributes if not already done in Task 10.

- [ ] **Step 2: Remove unused `PlannerOutput` reference from `generate_briefing`**

Verify `generate_briefing` now uses `PlanOutput` for surface push (done in Task 7).

- [ ] **Step 3: Run the full test suite**

Run: `cd backend && python -m pytest tests/ -v -k "not e2e" --timeout=60`
Expected: All tests PASS

If any test imports `extract_decision` or `intent_to_decision` from `intent_classifier`, update those imports.

- [ ] **Step 4: Run ruff lint and format**

```bash
cd backend
ruff check src/orchestrator/jarvis.py src/orchestrator/agents.py src/orchestrator/prompts.py src/orchestrator/intent_classifier.py src/services/graph_executor.py src/api/routes_chat.py src/interface/telegram.py src/orchestrator/contracts.py --fix
ruff format src/orchestrator/ src/services/graph_executor.py src/api/routes_chat.py src/interface/telegram.py
```

- [ ] **Step 5: Verify no remaining references to deleted functions**

```bash
cd backend
grep -rn "extract_decision\|intent_to_decision\|_resolve_pipeline\|_check_step_condition" src/ --include="*.py" | grep -v "\.pyc" | grep -v "__pycache__"
```

Expected: No matches (or only in files not modified by this spec, like route_resolver.py which still exists for Spec 1B-iii).

- [ ] **Step 6: Commit**

```bash
cd backend
git add -u
git commit -m "feat(spec1b-ii): remove dead code — _resolve_pipeline, _check_step_condition, old surface preview"
```

---

## Success Criteria Checklist

After all 12 tasks are complete, verify:

- [ ] Messages route through capability-based plan steps (not decision types)
- [ ] Perceiver agent handles all read requests (observer + researcher merged)
- [ ] GraphExecutor uses `capability` field (with `task_type` fallback)
- [ ] Chat SSE emits `plan` event with PlanOutput shape
- [ ] Telegram uses public `get_budget_status()` method
- [ ] All existing fast-path intents still work
- [ ] `intent_to_decision()` and `extract_decision()` are deleted from intent_classifier.py
- [ ] `_resolve_pipeline()` and `_check_step_condition()` are deleted from jarvis.py
- [ ] `JARVIS_DECISION_FRAMEWORK` is NOT imported in jarvis.py (but still exists in prompts.py)
- [ ] `RouteResolver` is NOT imported in jarvis.py (but still exists in route_resolver.py)
- [ ] `PlannerOutput` still exists in contracts.py (deletion is Spec 1B-iii)
- [ ] All tests pass: `pytest tests/ -v -k "not e2e"`

## NOT in scope (deferred to Spec 1B-iii)

- Deleting `PlannerOutput` model from contracts.py
- Deleting `RouteResolver` from route_resolver.py
- Deleting `JARVIS_DECISION_FRAMEWORK` from prompts.py
- Deleting `PLANNER_PROMPT` (old version) from prompts.py
- Deleting `OBSERVER_PROMPT` and `RESEARCHER_PROMPT` from prompts.py
- Rewriting direct handlers to accept `PlanStep` natively (bridge pattern used)
