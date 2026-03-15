"""Tests for orchestrator context assembly and prompt building."""

from unittest.mock import AsyncMock, MagicMock, patch

import pytest

from tests.conftest import make_mock_settings


class TestContextAssembly:
    """Test context assembly for enriched agents."""

    @patch("src.orchestrator.jarvis.get_anthropic_client")
    @pytest.mark.asyncio
    async def test_assemble_context_returns_empty_for_non_enriched_agents(self, mock_get_client):
        """Test _assemble_context returns empty string for non-enriched agents."""
        from src.orchestrator.jarvis import JarvisOrchestrator

        mock_get_client.return_value = AsyncMock()
        settings = make_mock_settings(
            daily_token_budget_usd=5.0, use_bedrock=False, telegram_bot_token=""
        )
        db_factory = MagicMock()

        orchestrator = JarvisOrchestrator(settings=settings, db_factory=db_factory, services={})

        # Observer is not in CONTEXT_ENRICHED_AGENTS
        context = await orchestrator._assemble_context("observer", "test message")
        assert context == ""

        # Governor is not enriched
        context = await orchestrator._assemble_context("governor", "test message")
        assert context == ""

        # Operator is not enriched
        context = await orchestrator._assemble_context("operator", "test message")
        assert context == ""

    @patch("src.orchestrator.jarvis.get_anthropic_client")
    @pytest.mark.asyncio
    async def test_assemble_context_returns_context_for_enriched_agents(self, mock_get_client):
        """Test _assemble_context returns context for enriched agents with services."""
        from src.orchestrator.jarvis import JarvisOrchestrator

        mock_get_client.return_value = AsyncMock()
        settings = make_mock_settings(
            daily_token_budget_usd=5.0, use_bedrock=False, telegram_bot_token=""
        )
        db_factory = MagicMock()

        # Mock memory service
        mock_memory_svc = AsyncMock()
        mock_memory_svc.retrieve = AsyncMock(
            return_value=[
                {
                    "memory_type": "fact",
                    "fact_text": "User prefers concise communication",
                },
                {"memory_type": "preference", "fact_text": "Morning person"},
            ]
        )

        # Mock world_model service
        mock_world_model = AsyncMock()
        mock_world_model.find_entity = AsyncMock(
            return_value=[
                {"entity_type": "person", "name": "Alice"},
                {"entity_type": "project", "name": "Project X"},
            ]
        )

        orchestrator = JarvisOrchestrator(
            settings=settings,
            db_factory=db_factory,
            services={"memory": mock_memory_svc, "world_model": mock_world_model},
        )

        # Planner is enriched
        context = await orchestrator._assemble_context("planner", "What should I do?")
        assert context != ""
        assert "RELEVANT MEMORIES" in context
        assert "User prefers concise communication" in context
        assert "RELEVANT ENTITIES" in context
        assert "Alice" in context

    @patch("src.orchestrator.jarvis.get_anthropic_client")
    @pytest.mark.asyncio
    async def test_assemble_context_handles_missing_memory_service(self, mock_get_client):
        """Test _assemble_context gracefully handles missing memory service."""
        from src.orchestrator.jarvis import JarvisOrchestrator

        mock_get_client.return_value = AsyncMock()
        settings = make_mock_settings(
            daily_token_budget_usd=5.0, use_bedrock=False, telegram_bot_token=""
        )
        db_factory = MagicMock()

        # Mock only world_model, no memory service
        mock_world_model = AsyncMock()
        mock_world_model.find_entity = AsyncMock(
            return_value=[{"entity_type": "person", "name": "Bob"}]
        )

        orchestrator = JarvisOrchestrator(
            settings=settings,
            db_factory=db_factory,
            services={"world_model": mock_world_model},
        )

        context = await orchestrator._assemble_context("planner", "test")
        assert "RELEVANT ENTITIES" in context
        assert "Bob" in context
        # Memory section should not be present
        assert "RELEVANT MEMORIES" not in context

    @patch("src.orchestrator.jarvis.get_anthropic_client")
    @pytest.mark.asyncio
    async def test_assemble_context_handles_empty_results(self, mock_get_client):
        """Test _assemble_context returns empty when services return no results."""
        from src.orchestrator.jarvis import JarvisOrchestrator

        mock_get_client.return_value = AsyncMock()
        settings = make_mock_settings(
            daily_token_budget_usd=5.0, use_bedrock=False, telegram_bot_token=""
        )
        db_factory = MagicMock()

        # Mock services that return empty results
        mock_memory_svc = AsyncMock()
        mock_memory_svc.retrieve = AsyncMock(return_value=[])

        mock_world_model = AsyncMock()
        mock_world_model.find_entity = AsyncMock(return_value=[])

        orchestrator = JarvisOrchestrator(
            settings=settings,
            db_factory=db_factory,
            services={"memory": mock_memory_svc, "world_model": mock_world_model},
        )

        context = await orchestrator._assemble_context("researcher", "test")
        assert context == ""

    @patch("src.orchestrator.jarvis.get_anthropic_client")
    @pytest.mark.asyncio
    async def test_assemble_context_handles_service_exceptions(self, mock_get_client):
        """Test _assemble_context handles service exceptions gracefully."""
        from src.orchestrator.jarvis import JarvisOrchestrator

        mock_get_client.return_value = AsyncMock()
        settings = make_mock_settings(
            daily_token_budget_usd=5.0, use_bedrock=False, telegram_bot_token=""
        )
        db_factory = MagicMock()

        # Mock memory service that raises exception
        mock_memory_svc = AsyncMock()
        mock_memory_svc.retrieve = AsyncMock(side_effect=Exception("Database connection failed"))

        # Mock world_model that works
        mock_world_model = AsyncMock()
        mock_world_model.find_entity = AsyncMock(
            return_value=[{"entity_type": "person", "name": "Charlie"}]
        )

        orchestrator = JarvisOrchestrator(
            settings=settings,
            db_factory=db_factory,
            services={"memory": mock_memory_svc, "world_model": mock_world_model},
        )

        # Should not raise, should return partial context
        context = await orchestrator._assemble_context("librarian", "test")
        assert "RELEVANT ENTITIES" in context
        assert "Charlie" in context


class TestSystemPromptBuilding:
    """Test system prompt building with cache control."""

    @patch("src.orchestrator.jarvis.get_anthropic_client")
    def test_build_system_prompt_returns_list_with_cache_control(self, mock_get_client):
        """Test _build_system_prompt returns list with cache_control."""
        from src.orchestrator.agents import AGENTS
        from src.orchestrator.jarvis import JarvisOrchestrator

        mock_get_client.return_value = AsyncMock()
        settings = make_mock_settings(
            daily_token_budget_usd=5.0, use_bedrock=False, telegram_bot_token=""
        )

        orchestrator = JarvisOrchestrator(settings=settings, db_factory=MagicMock(), services={})

        agent = AGENTS["planner"]
        blocks = orchestrator._build_system_prompt(agent, context="")

        assert isinstance(blocks, list)
        assert len(blocks) == 1
        assert blocks[0]["type"] == "text"
        assert "cache_control" in blocks[0]
        assert blocks[0]["cache_control"]["type"] == "ephemeral"
        assert "Jarvis" in blocks[0]["text"]
        assert agent.prompt in blocks[0]["text"]

    @patch("src.orchestrator.jarvis.get_anthropic_client")
    def test_build_system_prompt_includes_context_block(self, mock_get_client):
        """Test _build_system_prompt adds context block when provided."""
        from src.orchestrator.agents import AGENTS
        from src.orchestrator.jarvis import JarvisOrchestrator

        mock_get_client.return_value = AsyncMock()
        settings = make_mock_settings(
            daily_token_budget_usd=5.0, use_bedrock=False, telegram_bot_token=""
        )

        orchestrator = JarvisOrchestrator(settings=settings, db_factory=MagicMock(), services={})

        agent = AGENTS["planner"]
        context = "--- CONTEXT ---\nRELEVANT MEMORIES:\n- User prefers email"
        blocks = orchestrator._build_system_prompt(agent, context=context)

        assert len(blocks) == 2
        # First block: soul + role with cache_control
        assert blocks[0]["cache_control"]["type"] == "ephemeral"
        # Second block: context (no cache_control on context)
        assert blocks[1]["type"] == "text"
        assert blocks[1]["text"] == context
        assert "cache_control" not in blocks[1]


class TestToolCacheControl:
    """Test tool cache control application."""

    @patch("src.orchestrator.jarvis.get_anthropic_client")
    def test_apply_cache_control_to_tools_marks_last_tool(self, mock_get_client):
        """Test _apply_cache_control_to_tools adds cache_control to last tool."""
        from src.orchestrator.jarvis import JarvisOrchestrator

        mock_get_client.return_value = AsyncMock()
        settings = make_mock_settings(
            daily_token_budget_usd=5.0, use_bedrock=False, telegram_bot_token=""
        )

        orchestrator = JarvisOrchestrator(settings=settings, db_factory=MagicMock(), services={})

        tools = [
            {"name": "tool_a", "description": "First tool"},
            {"name": "tool_b", "description": "Second tool"},
            {"name": "tool_c", "description": "Third tool"},
        ]

        result = orchestrator._apply_cache_control_to_tools(tools)

        # Should return new list
        assert result is not tools
        assert len(result) == 3

        # First two tools should not have cache_control
        assert "cache_control" not in result[0]
        assert "cache_control" not in result[1]

        # Last tool should have cache_control
        assert "cache_control" in result[2]
        assert result[2]["cache_control"]["type"] == "ephemeral"
        assert result[2]["name"] == "tool_c"

    @patch("src.orchestrator.jarvis.get_anthropic_client")
    def test_apply_cache_control_to_empty_tools(self, mock_get_client):
        """Test _apply_cache_control_to_tools handles empty tool list."""
        from src.orchestrator.jarvis import JarvisOrchestrator

        mock_get_client.return_value = AsyncMock()
        settings = make_mock_settings(
            daily_token_budget_usd=5.0, use_bedrock=False, telegram_bot_token=""
        )

        orchestrator = JarvisOrchestrator(settings=settings, db_factory=MagicMock(), services={})

        result = orchestrator._apply_cache_control_to_tools([])
        assert result == []

    @patch("src.orchestrator.jarvis.get_anthropic_client")
    def test_apply_cache_control_to_single_tool(self, mock_get_client):
        """Test _apply_cache_control_to_tools with single tool."""
        from src.orchestrator.jarvis import JarvisOrchestrator

        mock_get_client.return_value = AsyncMock()
        settings = make_mock_settings(
            daily_token_budget_usd=5.0, use_bedrock=False, telegram_bot_token=""
        )

        orchestrator = JarvisOrchestrator(settings=settings, db_factory=MagicMock(), services={})

        tools = [{"name": "only_tool", "description": "The only tool"}]
        result = orchestrator._apply_cache_control_to_tools(tools)

        assert len(result) == 1
        assert "cache_control" in result[0]
        assert result[0]["cache_control"]["type"] == "ephemeral"
