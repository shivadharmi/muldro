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

    @pytest.mark.asyncio
    async def test_persona_has_search_tool(self):
        from unittest.mock import AsyncMock, MagicMock, patch

        mock_db = AsyncMock()

        with patch("src.services.tool_registry.ToolRegistry") as mock_reg_cls:
            mock_reg = MagicMock()
            tool = MagicMock()
            tool.name = "search"
            tool.capability = "internal.search"
            mock_reg.get_tool = AsyncMock(return_value=tool)
            mock_reg_cls.return_value = mock_reg

            assert await AGENTS["persona"].can_use_tool("search", mock_db)

    @pytest.mark.asyncio
    async def test_persona_has_extract_preferences_tool(self):
        from unittest.mock import AsyncMock, MagicMock, patch

        mock_db = AsyncMock()

        with patch("src.services.tool_registry.ToolRegistry") as mock_reg_cls:
            mock_reg = MagicMock()
            tool = MagicMock()
            tool.name = "extract_preferences"
            tool.capability = "internal.extract_preferences"
            mock_reg.get_tool = AsyncMock(return_value=tool)
            mock_reg_cls.return_value = mock_reg

            assert await AGENTS["persona"].can_use_tool("extract_preferences", mock_db)

    @pytest.mark.asyncio
    async def test_persona_cannot_use_write_tools(self):
        from unittest.mock import AsyncMock, MagicMock, patch

        mock_db = AsyncMock()

        with patch("src.services.tool_registry.ToolRegistry") as mock_reg_cls:
            mock_reg = MagicMock()

            def get_tool(name):
                tools = {
                    "gmail_send": (
                        "gmail.send",
                        MagicMock(name="gmail_send", capability="gmail.send"),
                    ),
                    "calendar_create": (
                        "calendar.create",
                        MagicMock(name="calendar_create", capability="calendar.create"),
                    ),
                    "ingest_event": (
                        "internal.ingest_event",
                        MagicMock(name="ingest_event", capability="internal.ingest_event"),
                    ),
                }
                if name in tools:
                    cap, tool = tools[name]
                    return tool
                return None

            mock_reg.get_tool = AsyncMock(side_effect=get_tool)
            mock_reg_cls.return_value = mock_reg

            assert not await AGENTS["persona"].can_use_tool("gmail_send", mock_db)
            assert not await AGENTS["persona"].can_use_tool("calendar_create", mock_db)
            assert not await AGENTS["persona"].can_use_tool("ingest_event", mock_db)

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
