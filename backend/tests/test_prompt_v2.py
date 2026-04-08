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

    def test_not_in_agent_prompts(self):
        """V2 prompt should NOT be wired into AGENT_PROMPTS yet (that's 1B-ii)."""
        from src.orchestrator.prompts import AGENT_PROMPTS

        for name, prompt in AGENT_PROMPTS.items():
            assert prompt != PLANNER_PROMPT_V2, (
                f"PLANNER_PROMPT_V2 should not be in AGENT_PROMPTS['{name}'] yet"
            )

    def test_old_planner_prompt_still_exists(self):
        """The existing PLANNER_PROMPT must be untouched."""
        from src.orchestrator.prompts import PLANNER_PROMPT

        assert "decision" in PLANNER_PROMPT
        assert "create_task" in PLANNER_PROMPT
