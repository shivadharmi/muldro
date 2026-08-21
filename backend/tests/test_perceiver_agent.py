"""Tests for Perceiver agent activation — observer/researcher merge."""

from src.orchestrator.agents import (
    AGENT_CAPABILITY_SCOPES,
    AGENT_MODEL_TIERS,
    AGENT_THINKING,
    AGENTS,
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
        assert AGENT_MODEL_TIERS["perceiver"] == "balanced"

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
        assert "repo.search_code" in scope
        assert "repo.list_prs" in scope

    def test_perceiver_thinking_enabled(self):
        assert "perceiver" in AGENT_THINKING
        assert AGENT_THINKING["perceiver"].enabled is True
        assert AGENT_THINKING["perceiver"].budget_tokens == 6144

    def test_perceiver_in_agents_dict(self):
        assert "perceiver" in AGENTS
        agent = AGENTS["perceiver"]
        assert agent.model_tier == "balanced"
        assert agent.temperature == 0.3

    def test_observer_not_in_agents_dict(self):
        assert "observer" not in AGENTS

    def test_researcher_not_in_agents_dict(self):
        assert "researcher" not in AGENTS

    def test_planner_prompt_is_v2(self):
        from src.orchestrator.prompts import PLANNER_PROMPT_V2

        assert AGENT_PROMPTS["planner"] is PLANNER_PROMPT_V2

    def test_total_agent_count(self):
        """6 agents: perceiver, librarian, planner, executor, presenter, persona."""
        assert len(AGENTS) == 6
        expected = {
            "perceiver",
            "librarian",
            "planner",
            "executor",
            "presenter",
            "persona",
        }
        assert set(AGENTS.keys()) == expected
