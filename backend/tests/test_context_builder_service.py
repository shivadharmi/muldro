"""Tests for ContextBuilder — assembles rich context for agent prompts."""

from unittest.mock import AsyncMock

import pytest

from src.services.context_builder import ContextBuilder, ContextPack


@pytest.fixture
def mock_world_model():
    wm = AsyncMock()
    wm.resolve_entities = AsyncMock(return_value=[])
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
        mock_world_model.resolve_entities.return_value = entities

        pack = await builder.build("usr_1", "Acme Corp deal")
        assert len(pack.entities) == 2
        assert pack.entities[0]["canonical_name"] == "Acme Corp"

    @pytest.mark.asyncio
    async def test_build_truncates_entities_to_10(self, builder, mock_world_model):
        entities = [{"canonical_name": f"Entity {i}", "entity_type": "org"} for i in range(15)]
        mock_world_model.resolve_entities.return_value = entities

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
        mock_world_model.resolve_entities.side_effect = Exception("DB down")
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
        mock_world_model.resolve_entities.side_effect = Exception("fail")
        mock_memory_service.retrieve.side_effect = Exception("fail")

        pack = await builder.build("usr_1", "query", task_type="send_email")
        assert pack.task_summary == "query"
        assert pack.entities == []
        assert pack.goals == []
        assert pack.recent_events == []
        assert pack.artifacts == []


class TestEntityDoubleWrite:
    """Test that world-model fallback only runs when TriSearch returns no entities."""

    @pytest.mark.asyncio
    async def test_world_model_skipped_when_trisearch_returns_entities(self):
        """When TriSearch populates entities, world-model fallback should be skipped."""
        mock_world = AsyncMock()
        mock_world.resolve_entities = AsyncMock(
            return_value=[
                {"entity_id": "ent_wm", "entity_type": "org", "canonical_name": "WorldModel Co"},
            ]
        )

        mock_tri = AsyncMock()
        mock_tri.search_for_context = AsyncMock(
            return_value={
                "entity": [
                    {"id": "ent_ts", "result_type": "entity", "title": "TriSearch Co"},
                ],
                "memory": [],
            }
        )

        mock_db = AsyncMock()
        builder = ContextBuilder(
            world_model=mock_world,
            tri_search=mock_tri,
            db=mock_db,
        )

        pack = await builder.build("usr_1", "test", workspace_id="ws_1")

        # TriSearch entities should be used
        assert len(pack.entities) == 1
        assert pack.entities[0]["canonical_name"] == "TriSearch Co"
        # World model should NOT have been called
        mock_world.resolve_entities.assert_not_called()

    @pytest.mark.asyncio
    async def test_world_model_runs_when_trisearch_returns_no_entities(self):
        """When TriSearch returns no entities, world-model fallback should run."""
        mock_world = AsyncMock()
        mock_world.resolve_entities = AsyncMock(
            return_value=[
                {"entity_id": "ent_wm", "entity_type": "org", "canonical_name": "WorldModel Co"},
            ]
        )

        mock_tri = AsyncMock()
        mock_tri.search_for_context = AsyncMock(
            return_value={
                "entity": [],
                "memory": [],
            }
        )

        mock_db = AsyncMock()
        builder = ContextBuilder(
            world_model=mock_world,
            tri_search=mock_tri,
            db=mock_db,
        )

        pack = await builder.build("usr_1", "test", workspace_id="ws_1")

        mock_world.resolve_entities.assert_called_once()
        assert len(pack.entities) == 1
        assert pack.entities[0]["canonical_name"] == "WorldModel Co"


class TestBriefingEvidenceSemantic:
    @pytest.mark.asyncio
    async def test_related_items_uses_tri_search(self):
        """_get_related_items should use TriSearch vector similarity."""
        from unittest.mock import AsyncMock, MagicMock

        from src.services.briefing_read_model import BriefingReadModel

        mock_db = AsyncMock()
        mock_tri_search = AsyncMock()
        mock_tri_search.search = AsyncMock(
            return_value=[
                {
                    "id": "mem_1",
                    "title": "Related memory",
                    "result_type": "memory",
                    "final_score": 0.85,
                    "text": "Some evidence",
                },
                {
                    "id": "evt_2",
                    "title": "Related event",
                    "result_type": "event",
                    "final_score": 0.72,
                    "text": "An event",
                },
            ]
        )

        brm = BriefingReadModel(mock_db, "ws_1", tri_search=mock_tri_search, user_id="usr_1")

        mock_briefing = MagicMock()
        mock_briefing.headline = "Q1 Revenue Update"
        mock_briefing.briefing_id = "brn_001"
        mock_briefing.created_at = None

        items = await brm._get_related_items(mock_briefing)

        mock_tri_search.search.assert_called_once()
        assert len(items) >= 2
        assert any(i["item_id"] == "mem_1" for i in items)
        assert any(i["item_id"] == "evt_2" for i in items)

    @pytest.mark.asyncio
    async def test_related_items_falls_back_without_tri_search(self):
        """Without TriSearch, falls back to timestamp proximity."""
        from unittest.mock import AsyncMock, MagicMock

        from src.services.briefing_read_model import BriefingReadModel

        mock_db = AsyncMock()
        # No TriSearch provided
        brm = BriefingReadModel(mock_db, "ws_1")

        mock_briefing = MagicMock()
        mock_briefing.headline = "Q1 Revenue Update"
        mock_briefing.created_at = None

        # Should not raise even without TriSearch
        items = await brm._get_related_items(mock_briefing)
        assert isinstance(items, list)


class TestEnrichedGraphContext:
    @pytest.mark.asyncio
    async def test_build_uses_traverse_weighted(self):
        """ContextBuilder.build() should call traverse_weighted, not get_related_people."""
        from src.services.context_builder import ContextBuilder

        mock_graph = AsyncMock()
        mock_graph.traverse_weighted = AsyncMock(
            return_value=[
                {
                    "entity_id": "ent_b",
                    "name": "Alice Chen",
                    "entity_type": "person",
                    "avg_strength": 0.85,
                    "distance": 1,
                    "attributes": "{}",
                },
                {
                    "entity_id": "ent_c",
                    "name": "Acme Corp",
                    "entity_type": "organization",
                    "avg_strength": 0.6,
                    "distance": 2,
                    "attributes": "{}",
                },
            ]
        )

        mock_world = AsyncMock()
        mock_world.resolve_entities = AsyncMock(
            return_value=[
                {"entity_id": "ent_a", "entity_type": "person", "canonical_name": "Bob"},
            ]
        )

        builder = ContextBuilder(
            world_model=mock_world,
            graph_engine=mock_graph,
        )

        pack = await builder.build(user_id="usr_1", query="test query")

        mock_graph.traverse_weighted.assert_called()
        mock_graph.get_related_people.assert_not_called()
        assert len(pack.graph_relationships) == 2
        assert pack.graph_relationships[0]["name"] == "Alice Chen"
        assert pack.graph_relationships[0]["entity_type"] == "person"
        assert pack.graph_relationships[0]["strength"] == 0.85
        assert pack.graph_relationships[0]["distance"] == 1

    def test_to_prompt_renders_enriched_graph(self):
        """to_prompt() should render enriched graph relationships."""
        from src.services.context_builder import ContextBuilder, ContextPack

        pack = ContextPack(
            task_summary="test",
            graph_relationships=[
                {
                    "name": "Sarah Chen",
                    "entity_type": "person",
                    "relation_type": "invested_in",
                    "strength": 0.8,
                    "distance": 1,
                },
                {
                    "name": "Acme Corp",
                    "entity_type": "organization",
                    "strength": 0.6,
                    "distance": 2,
                },
            ],
        )

        prompt = ContextBuilder.to_prompt(pack)
        assert "Sarah Chen" in prompt
        assert "person" in prompt
        assert "strength=0.8" in prompt
        assert "distance=1" in prompt
