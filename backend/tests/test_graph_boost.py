"""Tests for graph-boosted search in TriSearchService."""

from unittest.mock import AsyncMock

import pytest

from src.services.tri_search import TriSearchService
from tests.conftest import make_mock_settings


@pytest.fixture
def settings():
    return make_mock_settings()


@pytest.fixture
def mock_graph():
    graph = AsyncMock()
    graph.traverse_weighted = AsyncMock(
        return_value=[
            {"entity_id": "ent_a"},
            {"entity_id": "ent_b"},
        ]
    )
    return graph


class TestSearchWithGraphBoost:
    @pytest.mark.asyncio
    async def test_boosts_graph_connected_results(self, settings, mock_graph):
        """Results sharing entities with graph neighborhood get score boost."""
        svc = TriSearchService(settings, graph_engine=mock_graph)
        svc.search = AsyncMock(
            return_value=[
                {"id": "mem_1", "score": 0.8, "final_score": 0.8, "entity_ids": ["ent_a"]},
                {"id": "mem_2", "score": 0.9, "final_score": 0.9, "entity_ids": ["ent_z"]},
            ]
        )

        results = await svc.search_with_graph_boost(
            query="test",
            user_id="usr_1",
            workspace_id="ws_1",
            db=AsyncMock(),
            context_entity_ids=["ent_x"],
            limit=10,
        )

        boosted = next(r for r in results if r["id"] == "mem_1")
        unboosted = next(r for r in results if r["id"] == "mem_2")
        assert boosted["final_score"] > 0.8
        assert unboosted["final_score"] == 0.9

    @pytest.mark.asyncio
    async def test_no_context_entities_returns_base(self, settings, mock_graph):
        """Without context entities, returns base search results unchanged."""
        svc = TriSearchService(settings, graph_engine=mock_graph)
        svc.search = AsyncMock(
            return_value=[
                {"id": "mem_1", "score": 0.8, "final_score": 0.8},
            ]
        )

        results = await svc.search_with_graph_boost(
            query="test",
            user_id="usr_1",
            workspace_id="ws_1",
            db=AsyncMock(),
            context_entity_ids=None,
            limit=10,
        )
        assert len(results) == 1
        assert results[0]["final_score"] == 0.8

    @pytest.mark.asyncio
    async def test_no_graph_engine_returns_base(self, settings):
        """Without graph engine, returns base search results."""
        svc = TriSearchService(settings, graph_engine=None)
        svc.search = AsyncMock(
            return_value=[
                {"id": "mem_1", "score": 0.8, "final_score": 0.8},
            ]
        )

        results = await svc.search_with_graph_boost(
            query="test",
            user_id="usr_1",
            workspace_id="ws_1",
            db=AsyncMock(),
            context_entity_ids=["ent_a"],
            limit=10,
        )
        assert len(results) == 1

    @pytest.mark.asyncio
    async def test_boost_is_10_percent_per_overlap(self, settings, mock_graph):
        """Each overlapping entity adds 10% boost to final_score."""
        svc = TriSearchService(settings, graph_engine=mock_graph)
        svc.search = AsyncMock(
            return_value=[
                {
                    "id": "mem_1",
                    "title": "Double overlap",
                    "score": 1.0,
                    "final_score": 1.0,
                    "entity_ids": ["ent_a", "ent_b"],
                },
            ]
        )

        results = await svc.search_with_graph_boost(
            query="test",
            user_id="usr_1",
            workspace_id="ws_1",
            db=AsyncMock(),
            context_entity_ids=["ent_x"],
            limit=10,
        )
        # 1.0 * (1.0 + 0.1 * 2) = 1.2
        assert abs(results[0]["final_score"] - 1.2) < 0.001

    @pytest.mark.asyncio
    async def test_results_re_sorted_after_boost(self, settings, mock_graph):
        """Results are re-sorted by boosted score."""
        svc = TriSearchService(settings, graph_engine=mock_graph)
        svc.search = AsyncMock(
            return_value=[
                {"id": "mem_1", "score": 0.7, "final_score": 0.7, "entity_ids": ["ent_a", "ent_b"]},
                {"id": "mem_2", "score": 0.8, "final_score": 0.8, "entity_ids": []},
            ]
        )

        results = await svc.search_with_graph_boost(
            query="test",
            user_id="usr_1",
            workspace_id="ws_1",
            db=AsyncMock(),
            context_entity_ids=["ent_x"],
            limit=10,
        )
        # mem_1 boosted: 0.7 * 1.2 = 0.84 > 0.8
        assert results[0]["id"] == "mem_1"
