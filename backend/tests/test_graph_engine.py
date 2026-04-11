"""Tests for GraphEngine typed relationship edges with strength and temporal data."""

from unittest.mock import AsyncMock, MagicMock

import pytest

from tests.conftest import make_mock_settings


@pytest.fixture
def settings():
    s = make_mock_settings()
    s.neo4j_url = "bolt://localhost:7687"
    s.neo4j_user = "neo4j"
    setattr(s, "neo4j_password", "neo4j-local")
    return s


@pytest.fixture
def mock_session():
    session = AsyncMock()
    session.__aenter__ = AsyncMock(return_value=session)
    session.__aexit__ = AsyncMock(return_value=False)
    return session


@pytest.fixture
def mock_driver(mock_session):
    from unittest.mock import MagicMock

    driver = MagicMock()
    driver.session.return_value = mock_session
    return driver


class TestTypedEdgeSync:
    async def test_sync_relationship_uses_typed_label(self, settings, mock_driver, mock_session):
        """Verifies Cypher contains :WORKS_AT not :RELATES_TO, and params include strength/dates."""
        from src.services.graph_engine import GraphEngine

        engine = GraphEngine(settings)
        engine._driver = mock_driver

        await engine.sync_relationship(
            relation_id="rel_001",
            from_entity_id="ent_001",
            to_entity_id="ent_002",
            relation_type="works_at",
            user_id="usr_001",
            strength=0.8,
            start_date="2025-06-01",
            end_date=None,
        )

        mock_session.run.assert_called_once()
        call_args = mock_session.run.call_args
        cypher = call_args[0][0]
        kwargs = call_args[1]

        assert ":WORKS_AT" in cypher, f"Expected :WORKS_AT in cypher, got: {cypher}"
        assert ":RELATES_TO" not in cypher, "Should not use :RELATES_TO"
        assert kwargs.get("strength") == 0.8
        assert kwargs.get("start_date") == "2025-06-01"
        assert kwargs.get("end_date") is None

    async def test_sync_relationship_keeps_relation_type_property(
        self, settings, mock_driver, mock_session
    ):
        """Backward compat: r.relation_type still set as property."""
        from src.services.graph_engine import GraphEngine

        engine = GraphEngine(settings)
        engine._driver = mock_driver

        await engine.sync_relationship(
            relation_id="rel_002",
            from_entity_id="ent_001",
            to_entity_id="ent_002",
            relation_type="works_at",
            user_id="usr_001",
        )

        call_args = mock_session.run.call_args
        cypher = call_args[0][0]
        kwargs = call_args[1]

        assert "r.relation_type" in cypher or "relation_type" in cypher.lower()
        assert kwargs.get("rel_type") == "works_at"

    async def test_sync_relationship_sanitizes_label(self, settings, mock_driver, mock_session):
        """'member of' → :MEMBER_OF"""
        from src.services.graph_engine import GraphEngine

        engine = GraphEngine(settings)
        engine._driver = mock_driver

        await engine.sync_relationship(
            relation_id="rel_003",
            from_entity_id="ent_001",
            to_entity_id="ent_002",
            relation_type="member of",
            user_id="usr_001",
        )

        call_args = mock_session.run.call_args
        cypher = call_args[0][0]

        assert ":MEMBER_OF" in cypher, f"Expected :MEMBER_OF in cypher, got: {cypher}"

    async def test_sync_relationship_defaults_strength_to_1(
        self, settings, mock_driver, mock_session
    ):
        """No strength passed → params have strength=1.0"""
        from src.services.graph_engine import GraphEngine

        engine = GraphEngine(settings)
        engine._driver = mock_driver

        await engine.sync_relationship(
            relation_id="rel_004",
            from_entity_id="ent_001",
            to_entity_id="ent_002",
            relation_type="knows",
            user_id="usr_001",
        )

        call_args = mock_session.run.call_args
        kwargs = call_args[1]

        assert kwargs.get("strength") == 1.0

    async def test_sync_relationship_no_driver_is_noop(self, settings):
        """neo4j_url='' → no error, no driver call."""
        from src.services.graph_engine import GraphEngine

        settings.neo4j_url = ""
        engine = GraphEngine(settings)

        # Should not raise
        await engine.sync_relationship(
            relation_id="rel_005",
            from_entity_id="ent_001",
            to_entity_id="ent_002",
            relation_type="knows",
            user_id="usr_001",
        )
        assert engine._driver is None


class TestTraverseWeighted:
    @pytest.mark.asyncio
    async def test_returns_entities_sorted_by_strength(self, settings, mock_driver, mock_session):
        """traverse_weighted returns entities ordered by avg_strength desc."""
        from src.services.graph_engine import GraphEngine

        mock_result = AsyncMock()
        mock_result.data = AsyncMock(
            return_value=[
                {
                    "entity_id": "ent_b",
                    "name": "Alice",
                    "entity_type": "person",
                    "attributes": "{}",
                    "avg_strength": 0.9,
                    "distance": 1,
                },
                {
                    "entity_id": "ent_c",
                    "name": "Bob",
                    "entity_type": "person",
                    "attributes": "{}",
                    "avg_strength": 0.5,
                    "distance": 2,
                },
            ]
        )
        mock_session.run = AsyncMock(return_value=mock_result)

        engine = GraphEngine(settings)
        engine._driver = mock_driver

        results = await engine.traverse_weighted(
            entity_id="ent_a", user_id="usr_1", depth=2, min_strength=0.3
        )
        assert len(results) == 2
        assert results[0]["entity_id"] == "ent_b"
        assert results[0]["avg_strength"] == 0.9
        assert results[1]["entity_id"] == "ent_c"

    @pytest.mark.asyncio
    async def test_cypher_includes_min_strength_param(self, settings, mock_driver, mock_session):
        """Cypher query should filter by min_strength."""
        from src.services.graph_engine import GraphEngine

        mock_result = AsyncMock()
        mock_result.data = AsyncMock(return_value=[])
        mock_session.run = AsyncMock(return_value=mock_result)

        engine = GraphEngine(settings)
        engine._driver = mock_driver
        await engine.traverse_weighted(entity_id="ent_a", user_id="usr_1", min_strength=0.5)

        call_args = mock_session.run.call_args
        params = call_args[1] if call_args[1] else call_args[0][1]
        assert params["min_strength"] == 0.5

    @pytest.mark.asyncio
    async def test_no_driver_returns_empty(self, settings):
        """When Neo4j not configured, return empty list."""
        from src.services.graph_engine import GraphEngine

        settings.neo4j_url = ""
        engine = GraphEngine(settings)
        results = await engine.traverse_weighted(entity_id="ent_a", user_id="usr_1")
        assert results == []

    @pytest.mark.asyncio
    async def test_exception_returns_empty(self, settings, mock_driver, mock_session):
        """On Neo4j error, return empty list instead of crashing."""
        from src.services.graph_engine import GraphEngine

        mock_session.run = AsyncMock(side_effect=Exception("Neo4j down"))
        engine = GraphEngine(settings)
        engine._driver = mock_driver
        results = await engine.traverse_weighted(entity_id="ent_a", user_id="usr_1")
        assert results == []


class TestGraphSyncPassesStrength:
    @pytest.mark.asyncio
    async def test_on_relationship_change_passes_strength(self):
        """on_relationship_change should forward strength + dates to Neo4j."""
        from datetime import date

        from src.services.graph_sync import GraphSyncService

        settings = make_mock_settings()
        setattr(settings, "neo4j_url", "bolt://localhost:7687")
        setattr(settings, "neo4j_user", "neo4j")
        setattr(settings, "neo4j_password", "test")

        mock_db = AsyncMock()
        mock_rel = MagicMock()
        mock_rel.relation_id = "rel_100"
        mock_rel.from_entity_id = "ent_a"
        mock_rel.to_entity_id = "ent_b"
        mock_rel.relation_type = "invested_in"
        mock_rel.user_id = "usr_1"
        mock_rel.strength = 0.75
        mock_rel.start_date = date(2025, 3, 1)
        mock_rel.end_date = None

        mock_result = MagicMock()
        mock_result.scalar_one_or_none.return_value = mock_rel
        mock_db.execute = AsyncMock(return_value=mock_result)

        svc = GraphSyncService(settings, mock_db)
        svc._graph = AsyncMock()

        from src.services.event_bus import BusEvent

        event = BusEvent(
            event_type="relationship.updated",
            payload={"relation_id": "rel_100"},
            user_id="usr_1",
        )
        await svc.on_relationship_change(event)

        svc._graph.sync_relationship.assert_called_once_with(
            relation_id="rel_100",
            from_entity_id="ent_a",
            to_entity_id="ent_b",
            relation_type="invested_in",
            user_id="usr_1",
            strength=0.75,
            start_date="2025-03-01",
            end_date=None,
        )

    @pytest.mark.asyncio
    async def test_sync_relationships_for_entity_passes_strength(self):
        """sync_relationships_for_entity should forward strength + dates."""
        from datetime import date

        from src.services.graph_sync import GraphSyncService

        settings = make_mock_settings()
        setattr(settings, "neo4j_url", "bolt://localhost:7687")
        setattr(settings, "neo4j_user", "neo4j")
        setattr(settings, "neo4j_password", "test")

        mock_rel = MagicMock()
        mock_rel.relation_id = "rel_200"
        mock_rel.from_entity_id = "ent_x"
        mock_rel.to_entity_id = "ent_y"
        mock_rel.relation_type = "reports_to"
        mock_rel.user_id = "usr_1"
        mock_rel.strength = 0.9
        mock_rel.start_date = date(2024, 1, 15)
        mock_rel.end_date = date(2025, 12, 31)

        mock_db = AsyncMock()
        mock_scalars = MagicMock()
        mock_scalars.all.return_value = [mock_rel]
        mock_result = MagicMock()
        mock_result.scalars.return_value = mock_scalars
        mock_db.execute = AsyncMock(return_value=mock_result)

        svc = GraphSyncService(settings, mock_db)
        svc._graph = AsyncMock()

        await svc.sync_relationships_for_entity("ent_x")

        svc._graph.sync_relationship.assert_called_once_with(
            relation_id="rel_200",
            from_entity_id="ent_x",
            to_entity_id="ent_y",
            relation_type="reports_to",
            user_id="usr_1",
            strength=0.9,
            start_date="2024-01-15",
            end_date="2025-12-31",
        )


class TestTraverseTemporal:
    @pytest.mark.asyncio
    async def test_passes_after_param(self, settings, mock_driver, mock_session):
        """traverse_temporal passes 'after' as a Cypher parameter."""
        from src.services.graph_engine import GraphEngine

        mock_result = AsyncMock()
        mock_result.data = AsyncMock(return_value=[])
        mock_session.run = AsyncMock(return_value=mock_result)

        engine = GraphEngine(settings)
        engine._driver = mock_driver
        await engine.traverse_temporal(
            entity_id="ent_a",
            user_id="usr_1",
            after="2025-01-01",
        )

        call_args = mock_session.run.call_args
        cypher = call_args[0][0]
        params = call_args[1] if call_args[1] else call_args[0][1]
        assert params.get("after") == "2025-01-01"
        assert "r.start_date" in cypher

    @pytest.mark.asyncio
    async def test_passes_before_param(self, settings, mock_driver, mock_session):
        """traverse_temporal passes 'before' as a Cypher parameter."""
        from src.services.graph_engine import GraphEngine

        mock_result = AsyncMock()
        mock_result.data = AsyncMock(return_value=[])
        mock_session.run = AsyncMock(return_value=mock_result)

        engine = GraphEngine(settings)
        engine._driver = mock_driver
        await engine.traverse_temporal(
            entity_id="ent_a",
            user_id="usr_1",
            before="2026-06-01",
        )

        call_args = mock_session.run.call_args
        params = call_args[1] if call_args[1] else call_args[0][1]
        assert params.get("before") == "2026-06-01"

    @pytest.mark.asyncio
    async def test_returns_data(self, settings, mock_driver, mock_session):
        """traverse_temporal returns structured entity data."""
        from src.services.graph_engine import GraphEngine

        mock_result = AsyncMock()
        mock_result.data = AsyncMock(
            return_value=[
                {
                    "entity_id": "ent_b",
                    "name": "ProjectX",
                    "entity_type": "project",
                    "relation_type": "works_on",
                    "strength": 0.8,
                },
            ]
        )
        mock_session.run = AsyncMock(return_value=mock_result)

        engine = GraphEngine(settings)
        engine._driver = mock_driver
        results = await engine.traverse_temporal(
            entity_id="ent_a",
            user_id="usr_1",
            after="2025-01-01",
            before="2026-01-01",
        )
        assert len(results) == 1
        assert results[0]["entity_id"] == "ent_b"

    @pytest.mark.asyncio
    async def test_no_driver_returns_empty(self, settings):
        """When Neo4j not configured, return empty list."""
        from src.services.graph_engine import GraphEngine

        settings.neo4j_url = ""
        engine = GraphEngine(settings)
        results = await engine.traverse_temporal(
            entity_id="ent_a", user_id="usr_1", after="2025-01-01"
        )
        assert results == []
