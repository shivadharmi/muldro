"""Tests for ContextBuilder — assembles rich context for agent prompts."""

from unittest.mock import AsyncMock, MagicMock

import pytest

from src.services.context_builder import ContextBuilder, ContextPack


@pytest.fixture
def mock_world_model():
    wm = AsyncMock()
    wm.find_entity = AsyncMock(return_value=[])
    return wm


@pytest.fixture
def mock_memory_service():
    ms = AsyncMock()
    ms.retrieve = AsyncMock(return_value=[])
    return ms


@pytest.fixture
def mock_artifact_store():
    ast = AsyncMock()
    ast.search = AsyncMock(return_value=[])
    return ast


@pytest.fixture
def builder(
    mock_world_model,
    mock_memory_service,
    mock_artifact_store,
):
    return ContextBuilder(
        world_model=mock_world_model,
        memory_service=mock_memory_service,
        artifact_store=mock_artifact_store,
    )


@pytest.fixture
def empty_builder():
    return ContextBuilder()


def _make_artifact(
    artifact_id="art_001",
    artifact_type="document",
    title="Investor Deck",
):
    a = MagicMock()
    a.artifact_id = artifact_id
    a.artifact_type = artifact_type
    a.title = title
    return a


class TestBuildEmptyContext:
    @pytest.mark.asyncio
    async def test_build_empty_context(self, empty_builder):
        pack = await empty_builder.build("usr_1", "test query")
        assert pack.task_summary == "test query"
        assert pack.entities == []
        assert pack.goals == []
        assert pack.recent_events == []
        assert pack.preferences == []
        assert pack.artifacts == []

    @pytest.mark.asyncio
    async def test_build_empty_query(self, builder):
        pack = await builder.build("usr_1", "")
        assert pack.task_summary == ""
        assert pack.entities == []


class TestBuildWithEntities:
    @pytest.mark.asyncio
    async def test_build_with_entities(self, builder, mock_world_model):
        entities = [
            {"canonical_name": "Acme Corp", "entity_type": "org"},
            {"canonical_name": "John Doe", "entity_type": "person"},
        ]
        mock_world_model.find_entity.return_value = entities

        pack = await builder.build("usr_1", "Acme Corp deal")
        assert len(pack.entities) == 2
        assert pack.entities[0]["canonical_name"] == "Acme Corp"

    @pytest.mark.asyncio
    async def test_build_truncates_entities_to_10(self, builder, mock_world_model):
        entities = [{"canonical_name": f"Entity {i}", "entity_type": "org"} for i in range(15)]
        mock_world_model.find_entity.return_value = entities

        pack = await builder.build("usr_1", "many entities")
        assert len(pack.entities) == 10


class TestBuildWithGoals:
    @pytest.mark.asyncio
    async def test_build_with_goal_memories(self, builder, mock_memory_service):
        """Goals are now stored as memories with memory_type='goal'."""
        # First call returns regular memories (episodic/preference), second returns goals
        mock_memory_service.retrieve.side_effect = [
            [],  # regular memory retrieval
            [
                {
                    "memory_id": "mem_001",
                    "fact_text": "Goal: Ship MVP. Priority: high",
                    "confidence": 0.9,
                    "provenance": {"priority": "high"},
                },
            ],
        ]

        pack = await builder.build("usr_1", "progress update")
        assert len(pack.goals) == 1
        assert "Ship MVP" in pack.goals[0]["title"]

    @pytest.mark.asyncio
    async def test_build_with_memory(self, builder, mock_memory_service):
        memories = [
            {
                "memory_type": "episodic",
                "fact_text": "Meeting with investor",
            },
            {
                "memory_type": "preference",
                "fact_text": "Prefers concise emails",
            },
        ]
        mock_memory_service.retrieve.return_value = memories

        pack = await builder.build("usr_1", "investor prep")
        assert len(pack.recent_events) == 1
        assert len(pack.preferences) == 1
        assert pack.preferences[0]["fact_text"] == "Prefers concise emails"

    @pytest.mark.asyncio
    async def test_build_with_artifacts(self, builder, mock_artifact_store):
        artifacts = [_make_artifact()]
        mock_artifact_store.search.return_value = artifacts

        pack = await builder.build("usr_1", "investor deck")
        assert len(pack.artifacts) == 1
        assert pack.artifacts[0]["title"] == "Investor Deck"


class TestToPromptFormatting:
    def test_to_prompt_empty_pack(self):
        pack = ContextPack()
        prompt = ContextBuilder.to_prompt(pack)
        assert prompt == ""

    def test_to_prompt_with_task_summary(self):
        pack = ContextPack(task_summary="Draft an email")
        prompt = ContextBuilder.to_prompt(pack)
        assert "## Task" in prompt
        assert "Draft an email" in prompt

    def test_to_prompt_with_goals(self):
        pack = ContextPack(
            goals=[
                {
                    "title": "Ship MVP",
                    "progress": 0.5,
                    "priority": "high",
                },
            ]
        )
        prompt = ContextBuilder.to_prompt(pack)
        assert "## Active Goals" in prompt
        assert "Ship MVP" in prompt
        assert "50%" in prompt

    def test_to_prompt_with_entities(self):
        pack = ContextPack(
            entities=[
                {
                    "canonical_name": "Acme Corp",
                    "entity_type": "org",
                },
            ]
        )
        prompt = ContextBuilder.to_prompt(pack)
        assert "## Relevant Entities" in prompt
        assert "Acme Corp (org)" in prompt

    def test_to_prompt_with_all_sections(self):
        pack = ContextPack(
            task_summary="Review deal",
            goals=[{"title": "Close deal", "progress": 0.8}],
            entities=[
                {
                    "canonical_name": "BigCo",
                    "entity_type": "org",
                }
            ],
            preferences=[{"fact_text": "Likes bullet points"}],
            recent_events=[{"fact_text": "Met BigCo yesterday"}],
            artifacts=[{"artifact_type": "doc", "title": "Term Sheet"}],
            constraints=["Max 500 words"],
            risks=["Deal may fall through"],
        )
        prompt = ContextBuilder.to_prompt(pack)
        assert "## Task" in prompt
        assert "## Active Goals" in prompt
        assert "## Relevant Entities" in prompt
        assert "## User Preferences" in prompt
        assert "## Recent Context" in prompt
        assert "## Artifacts" in prompt
        assert "## Constraints" in prompt
        assert "## Risks" in prompt


class TestBuildHandlesServiceFailures:
    @pytest.mark.asyncio
    async def test_build_handles_entity_failure(self, builder, mock_world_model):
        mock_world_model.find_entity.side_effect = Exception("DB down")
        pack = await builder.build("usr_1", "test query")
        assert pack.entities == []

    @pytest.mark.asyncio
    async def test_build_handles_memory_failure(self, builder, mock_memory_service):
        mock_memory_service.retrieve.side_effect = Exception("Redis error")
        pack = await builder.build("usr_1", "test query")
        assert pack.recent_events == []
        assert pack.preferences == []

    @pytest.mark.asyncio
    async def test_build_handles_all_failures(
        self,
        builder,
        mock_world_model,
        mock_memory_service,
        mock_artifact_store,
    ):
        mock_world_model.find_entity.side_effect = Exception("fail")
        mock_memory_service.retrieve.side_effect = Exception("fail")
        mock_artifact_store.search.side_effect = Exception("fail")

        pack = await builder.build("usr_1", "query", task_type="send_email")
        assert pack.task_summary == "query"
        assert pack.entities == []
        assert pack.goals == []
        assert pack.recent_events == []
        assert pack.artifacts == []
