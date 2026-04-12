"""Tests for Governor agent demotion to edge-case-only (Spec 2B-i)."""


class TestGovernorEdgeCaseOnly:
    def test_governor_marked_edge_case(self):
        from src.orchestrator.agents import AGENTS

        governor = AGENTS["governor"]
        assert governor.edge_case_only is True

    def test_other_agents_not_edge_case(self):
        from src.orchestrator.agents import AGENTS

        for name, agent in AGENTS.items():
            if name != "governor":
                assert agent.edge_case_only is False, f"{name} should not be edge_case_only"

    def test_governor_prompt_simplified(self):
        from src.orchestrator.prompts import GOVERNOR_PROMPT

        # Should mention edge-case / fallback role
        assert "edge" in GOVERNOR_PROMPT.lower() or "fallback" in GOVERNOR_PROMPT.lower()
        # Should NOT contain the old rule
        assert "NEVER auto-approve external writes in v1" not in GOVERNOR_PROMPT
