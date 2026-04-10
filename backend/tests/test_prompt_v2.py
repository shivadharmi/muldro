"""Tests for PLANNER_PROMPT_V2 and PERCEIVER_PROMPT — structural validation."""

from __future__ import annotations

from src.orchestrator.prompts import PLANNER_PROMPT_V2


class TestPlannerPromptV2:
    """Structural validation of the new Planner prompt."""

    def test_prompt_exists_and_nonempty(self):
        assert isinstance(PLANNER_PROMPT_V2, str)
        assert len(PLANNER_PROMPT_V2) > 100

    def test_has_role_section(self):
        assert "<role>" in PLANNER_PROMPT_V2
        assert "</role>" in PLANNER_PROMPT_V2

    def test_has_capability_placeholder(self):
        """The prompt must contain {capability_summary} for dynamic injection."""
        assert "{capability_summary}" in PLANNER_PROMPT_V2

    def test_has_instructions_section(self):
        assert "<instructions>" in PLANNER_PROMPT_V2
        assert "</instructions>" in PLANNER_PROMPT_V2

    def test_has_output_format_section(self):
        assert "<output_format>" in PLANNER_PROMPT_V2
        assert "</output_format>" in PLANNER_PROMPT_V2

    def test_has_examples_section(self):
        assert "<examples>" in PLANNER_PROMPT_V2
        assert "</examples>" in PLANNER_PROMPT_V2

    def test_has_rules_section(self):
        assert "<rules>" in PLANNER_PROMPT_V2
        assert "</rules>" in PLANNER_PROMPT_V2

    def test_references_plan_output_schema(self):
        """Output format must reference PlanOutput fields."""
        assert '"goal"' in PLANNER_PROMPT_V2
        assert '"steps"' in PLANNER_PROMPT_V2
        assert '"capability"' in PLANNER_PROMPT_V2
        assert '"achievable"' in PLANNER_PROMPT_V2
        assert '"capability_gaps"' in PLANNER_PROMPT_V2

    def test_mentions_goal_decomposition(self):
        assert "goal" in PLANNER_PROMPT_V2.lower()
        assert "decompos" in PLANNER_PROMPT_V2.lower()

    def test_does_not_mention_19_decision_types(self):
        """V2 prompt should NOT reference the old decision classification."""
        assert "create_task" not in PLANNER_PROMPT_V2
        assert "draft_reply" not in PLANNER_PROMPT_V2
        assert "read_source" not in PLANNER_PROMPT_V2

    def test_has_at_least_3_examples(self):
        """Spec requires 3 examples: multi-step, write action, partial achievability."""
        example_count = PLANNER_PROMPT_V2.count("Example")
        assert example_count >= 3

    def test_placeholder_is_formattable(self):
        """The {capability_summary} placeholder can be .format()-ed."""
        formatted = PLANNER_PROMPT_V2.format(capability_summary="<test>email: search, read</test>")
        assert "<test>email: search, read</test>" in formatted

    def test_is_active_in_agent_prompts(self):
        """V2 prompt is now wired into AGENT_PROMPTS (1B-ii switchover complete)."""
        from src.orchestrator.prompts import AGENT_PROMPTS

        assert AGENT_PROMPTS["planner"] == PLANNER_PROMPT_V2

    def test_old_planner_prompt_still_exists(self):
        """The existing PLANNER_PROMPT is kept for reference."""
        from src.orchestrator.prompts import PLANNER_PROMPT

        assert "decision" in PLANNER_PROMPT
        assert "create_task" in PLANNER_PROMPT


class TestPerceiverPrompt:
    """Structural validation of the new Perceiver prompt."""

    def test_prompt_exists_and_nonempty(self):
        from src.orchestrator.prompts import PERCEIVER_PROMPT

        assert isinstance(PERCEIVER_PROMPT, str)
        assert len(PERCEIVER_PROMPT) > 100

    def test_has_role_section(self):
        from src.orchestrator.prompts import PERCEIVER_PROMPT

        assert "<role>" in PERCEIVER_PROMPT
        assert "</role>" in PERCEIVER_PROMPT

    def test_role_mentions_read_only(self):
        from src.orchestrator.prompts import PERCEIVER_PROMPT

        role_start = PERCEIVER_PROMPT.index("<role>")
        role_end = PERCEIVER_PROMPT.index("</role>")
        role_text = PERCEIVER_PROMPT[role_start:role_end].lower()
        assert "read" in role_text

    def test_has_rules_section(self):
        from src.orchestrator.prompts import PERCEIVER_PROMPT

        assert "<rules>" in PERCEIVER_PROMPT
        assert "</rules>" in PERCEIVER_PROMPT

    def test_mentions_never_write(self):
        """Perceiver must be strictly read-only."""
        from src.orchestrator.prompts import PERCEIVER_PROMPT

        prompt_lower = PERCEIVER_PROMPT.lower()
        assert "never" in prompt_lower and "write" in prompt_lower

    def test_has_methodology_or_workflow(self):
        """Should include a methodology/workflow section."""
        from src.orchestrator.prompts import PERCEIVER_PROMPT

        assert "<methodology>" in PERCEIVER_PROMPT or "<workflow>" in PERCEIVER_PROMPT

    def test_has_examples(self):
        from src.orchestrator.prompts import PERCEIVER_PROMPT

        assert "<examples>" in PERCEIVER_PROMPT
        assert "</examples>" in PERCEIVER_PROMPT

    def test_covers_external_sources(self):
        """Should mention external data sources."""
        from src.orchestrator.prompts import PERCEIVER_PROMPT

        prompt_lower = PERCEIVER_PROMPT.lower()
        assert "email" in prompt_lower or "calendar" in prompt_lower

    def test_covers_internal_knowledge(self):
        """Should mention internal knowledge search."""
        from src.orchestrator.prompts import PERCEIVER_PROMPT

        prompt_lower = PERCEIVER_PROMPT.lower()
        assert "knowledge" in prompt_lower or "memor" in prompt_lower

    def test_old_observer_prompt_still_exists(self):
        """The existing OBSERVER_PROMPT is kept for reference."""
        from src.orchestrator.prompts import OBSERVER_PROMPT

        assert "Observer" in OBSERVER_PROMPT

    def test_old_researcher_prompt_still_exists(self):
        """The existing RESEARCHER_PROMPT is kept for reference."""
        from src.orchestrator.prompts import RESEARCHER_PROMPT

        assert "Researcher" in RESEARCHER_PROMPT

    def test_is_active_in_agent_prompts(self):
        """PERCEIVER_PROMPT is now wired into AGENT_PROMPTS (1B-ii switchover complete)."""
        from src.orchestrator.prompts import AGENT_PROMPTS, PERCEIVER_PROMPT

        assert "perceiver" in AGENT_PROMPTS
        assert AGENT_PROMPTS["perceiver"] == PERCEIVER_PROMPT
