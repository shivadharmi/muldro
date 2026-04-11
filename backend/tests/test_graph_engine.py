"""Tests for GraphEngine typed relationship edges with strength and temporal data."""

from unittest.mock import AsyncMock

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
