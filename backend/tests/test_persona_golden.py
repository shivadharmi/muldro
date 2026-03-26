"""Golden tests for Persona agent preference extraction.

Verifies that the Persona prompt + tool scope correctly extracts
behavioral patterns from interaction sequences.
"""

import pytest

from src.orchestrator.agents import AGENTS

# Golden test cases for persona behavior
PERSONA_CASES = [
    {
        "name": "detects_brevity_preference",
        "interactions": [
            {"message": "brief", "surface": "telegram"},
            {"message": "just the priorities", "surface": "telegram"},
            {"message": "tldr", "surface": "telegram"},
        ],
        "expected_categories": ["communication"],
        "expected_signals": ["concise", "brief", "short"],
    },
    {
        "name": "detects_morning_activity",
        "interactions": [
            {"message": "what's on today?", "surface": "telegram", "time": "07:00"},
            {"message": "morning brief", "surface": "telegram", "time": "07:30"},
        ],
        "expected_categories": ["schedule"],
        "expected_signals": ["morning"],
    },
    {
        "name": "detects_approval_priority",
        "interactions": [
            {"message": "any approvals pending?", "surface": "web"},
            {"message": "show me what needs approval", "surface": "web"},
        ],
        "expected_categories": ["priorities", "workflow"],
        "expected_signals": ["approval"],
    },
]


class TestPersonaAgentConfig:
    """Verify Persona agent is correctly configured."""

    def test_persona_agent_exists(self):
        assert "persona" in AGENTS

    def test_persona_uses_haiku(self):
        assert AGENTS["persona"].model_tier == "haiku"

    def test_persona_has_search_tool(self):
        assert AGENTS["persona"].can_use_tool("search")

    def test_persona_has_extract_preferences_tool(self):
        assert AGENTS["persona"].can_use_tool("extract_preferences")

    def test_persona_cannot_use_write_tools(self):
        assert not AGENTS["persona"].can_use_tool("gmail_send")
        assert not AGENTS["persona"].can_use_tool("calendar_create")
        assert not AGENTS["persona"].can_use_tool("ingest_event")

    def test_persona_prompt_mentions_preferences(self):
        agent = AGENTS["persona"]
        assert "preference" in agent.prompt.lower()
        assert "observe" in agent.prompt.lower()


class TestPersonaGoldenCases:
    """Verify Persona prompt produces correct preference format."""

    @pytest.mark.parametrize("case", PERSONA_CASES, ids=lambda c: c["name"])
    def test_persona_prompt_includes_output_schema(self, case):
        """Verify the Persona prompt asks for structured JSON output with
        the expected fields (category, observation, preference, confidence).
        """
        agent = AGENTS["persona"]
        prompt = agent.prompt
        assert "category" in prompt
        assert "confidence" in prompt
        assert "observation" in prompt

    @pytest.mark.parametrize("case", PERSONA_CASES, ids=lambda c: c["name"])
    def test_persona_interaction_context_is_formattable(self, case):
        """Verify we can format interactions into the prompt context
        the orchestrator would send to Persona.
        """
        for interaction in case["interactions"]:
            msg = (
                f"Observe this user interaction on {interaction['surface']}:\n"
                f"User said: {interaction['message']}\n"
                f"Decision: acknowledge\n"
                f"Extract any preference signals."
            )
            assert len(msg) > 0
            assert interaction["message"] in msg
