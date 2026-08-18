"""Tests for orchestrator context assembly and prompt building."""

from unittest.mock import AsyncMock, MagicMock, patch

import pytest

from src.orchestrator.services import ServiceContainer
from tests.conftest import TEST_USER_ID, TEST_WORKSPACE_ID, make_mock_settings


class TestContextAssembly:
    """Test context assembly for enriched agents."""

    @pytest.mark.asyncio
    async def test_assemble_context_returns_empty_for_non_enriched_agents(self):
        """Test _assemble_context returns empty string for non-enriched agents."""
        from src.orchestrator.muldro import MuldroOrchestrator

        settings = make_mock_settings(daily_token_budget_usd=5.0)
        db_factory = MagicMock()

        orchestrator = MuldroOrchestrator(
            settings=settings, db_factory=db_factory, services=ServiceContainer()
        )

        # Persona is not in CONTEXT_ENRICHED_AGENTS
        context = await orchestrator._assemble_context(
            "persona", "test message", user_id=TEST_USER_ID
        )
        assert context == ""

    @pytest.mark.asyncio
    async def test_assemble_context_returns_context_for_enriched_agents(self):
        """Test _assemble_context returns context for enriched agents with services."""
        from src.orchestrator.muldro import MuldroOrchestrator

        settings = make_mock_settings(daily_token_budget_usd=5.0)
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
        mock_world_model.resolve_entities = AsyncMock(
            return_value=[
                {"entity_type": "person", "name": "Alice"},
                {"entity_type": "project", "name": "Project X"},
            ]
        )

        orchestrator = MuldroOrchestrator(
            settings=settings,
            db_factory=db_factory,
            services=ServiceContainer(memory_service=mock_memory_svc, world_model=mock_world_model),
        )

        # Planner is enriched
        context = await orchestrator._assemble_context(
            "planner", "What should I do?", user_id=TEST_USER_ID
        )
        assert context != ""
        assert "--- CONTEXT ---" in context
        # ContextBuilder renders entities as "## Relevant Entities"
        # and memories split into preferences/recent context
        assert "Morning person" in context

    @pytest.mark.asyncio
    async def test_assemble_context_handles_missing_memory_service(self):
        """Test _assemble_context gracefully handles missing memory service."""
        from src.orchestrator.muldro import MuldroOrchestrator

        settings = make_mock_settings(daily_token_budget_usd=5.0)
        db_factory = MagicMock()

        # Mock only world_model, no memory service
        mock_world_model = AsyncMock()
        mock_world_model.resolve_entities = AsyncMock(
            return_value=[{"entity_type": "person", "name": "Bob"}]
        )

        orchestrator = MuldroOrchestrator(
            settings=settings,
            db_factory=db_factory,
            services=ServiceContainer(world_model=mock_world_model),
        )

        context = await orchestrator._assemble_context("planner", "test", user_id=TEST_USER_ID)
        # ContextBuilder renders entities as "## Relevant Entities"
        assert "Relevant Entities" in context
        assert "Bob" in context

    @pytest.mark.asyncio
    async def test_assemble_context_handles_empty_results(self):
        """Test _assemble_context returns empty when services return no results."""
        from src.orchestrator.muldro import MuldroOrchestrator

        settings = make_mock_settings(daily_token_budget_usd=5.0)
        db_factory = MagicMock()

        # Mock services that return empty results
        mock_memory_svc = AsyncMock()
        mock_memory_svc.retrieve = AsyncMock(return_value=[])

        mock_world_model = AsyncMock()
        mock_world_model.resolve_entities = AsyncMock(return_value=[])

        orchestrator = MuldroOrchestrator(
            settings=settings,
            db_factory=db_factory,
            services=ServiceContainer(memory_service=mock_memory_svc, world_model=mock_world_model),
        )

        # ContextBuilder always includes task_summary, so context may not be empty
        # even when services return no results — but it won't have entity/memory sections
        context = await orchestrator._assemble_context("perceiver", "test", user_id=TEST_USER_ID)
        if context:
            assert "Relevant Entities" not in context
            assert "User Preferences" not in context

    @pytest.mark.asyncio
    async def test_assemble_context_handles_service_exceptions(self):
        """Test _assemble_context handles service exceptions gracefully."""
        from src.orchestrator.muldro import MuldroOrchestrator

        settings = make_mock_settings(daily_token_budget_usd=5.0)
        db_factory = MagicMock()

        # Mock memory service that raises exception
        mock_memory_svc = AsyncMock()
        mock_memory_svc.retrieve = AsyncMock(side_effect=Exception("Database connection failed"))

        # Mock world_model that works
        mock_world_model = AsyncMock()
        mock_world_model.resolve_entities = AsyncMock(
            return_value=[{"entity_type": "person", "name": "Charlie"}]
        )

        orchestrator = MuldroOrchestrator(
            settings=settings,
            db_factory=db_factory,
            services=ServiceContainer(memory_service=mock_memory_svc, world_model=mock_world_model),
        )

        # ContextBuilder catches internal errors per-service, so partial context may work.
        # But since memory_service raises, only world_model contributes.
        context = await orchestrator._assemble_context("librarian", "test", user_id=TEST_USER_ID)
        # ContextBuilder catches each service error independently
        assert "Charlie" in context or context == ""


class TestSystemPromptBuilding:
    """Test system prompt building with cache control."""

    def test_build_system_prompt_returns_list_with_cache_control(self):
        """Test _build_system_prompt returns list with cache_control."""
        from src.orchestrator.agents import AGENTS
        from src.orchestrator.muldro import MuldroOrchestrator

        settings = make_mock_settings(daily_token_budget_usd=5.0)

        orchestrator = MuldroOrchestrator(
            settings=settings, db_factory=MagicMock(), services=ServiceContainer()
        )

        agent = AGENTS["planner"]
        blocks = orchestrator._build_system_prompt(agent, context="")

        assert isinstance(blocks, list)
        assert len(blocks) == 1
        assert blocks[0]["type"] == "text"
        assert "cache_control" in blocks[0]
        assert blocks[0]["cache_control"]["type"] == "ephemeral"
        assert "Muldro" in blocks[0]["text"]
        # Planner prompt has {capability_summary} replaced at build time
        expected_prompt = agent.prompt.format(capability_summary="No capabilities connected yet.")
        assert expected_prompt in blocks[0]["text"]

    def test_build_system_prompt_includes_context_block(self):
        """Test _build_system_prompt adds context block when provided."""
        from src.orchestrator.agents import AGENTS
        from src.orchestrator.muldro import MuldroOrchestrator

        settings = make_mock_settings(daily_token_budget_usd=5.0)

        orchestrator = MuldroOrchestrator(
            settings=settings, db_factory=MagicMock(), services=ServiceContainer()
        )

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


# --- Step 10D P1 A4: the synthetic "lead" is a context-enriched agent ------------------
def test_lead_is_context_enriched():
    """``assemble_context("lead", ...)`` must enrich the synthetic lead's context (5b assembles
    context for the lead), so "lead" belongs to CONTEXT_ENRICHED_AGENTS."""
    from src.orchestrator.context_assembler import CONTEXT_ENRICHED_AGENTS

    assert "lead" in CONTEXT_ENRICHED_AGENTS


def test_lead_is_not_jit_enabled():
    """JIT is a separate dormant concern — the lead is NOT added to JIT_ENABLED_AGENTS."""
    from src.orchestrator.context_assembler import JIT_ENABLED_AGENTS

    assert "lead" not in JIT_ENABLED_AGENTS


async def test_assemble_context_does_not_early_return_for_lead():
    """Behavioral: ``assemble_context("lead", ...)`` no longer early-returns "" for the
    unknown-agent reason — it reaches ContextBuilder.build (a non-enriched agent returns ""
    before building). Proves the CONTEXT_ENRICHED_AGENTS membership is load-bearing."""
    from src.orchestrator.context_assembler import ContextAssembler
    from src.services.context_builder import ContextPack

    db_session = MagicMock()
    db_session.__aenter__ = AsyncMock(return_value=db_session)
    db_session.__aexit__ = AsyncMock(return_value=False)
    result = MagicMock()
    result.scalars.return_value.all.return_value = []
    db_session.execute = AsyncMock(return_value=result)

    assembler = ContextAssembler(
        settings=make_mock_settings(),
        services=ServiceContainer(world_model=MagicMock(), memory_service=MagicMock()),
        db_factory_provider=lambda: lambda: db_session,
        client=MagicMock(),
    )

    with patch("src.orchestrator.context_assembler.ContextBuilder") as mock_builder_cls:
        mock_instance = MagicMock()
        mock_instance.build = AsyncMock(return_value=ContextPack())
        mock_builder_cls.return_value = mock_instance
        mock_builder_cls.to_prompt = MagicMock(return_value="LEAD CONTEXT")

        ctx = await assembler.assemble_context(
            "lead", "msg", user_id=TEST_USER_ID, workspace_id=TEST_WORKSPACE_ID
        )

    mock_instance.build.assert_awaited_once()
    assert "LEAD CONTEXT" in ctx
