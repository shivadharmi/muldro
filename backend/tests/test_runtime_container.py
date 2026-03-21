"""Tests for RuntimeContainer.build() — verifies all tiers populate correctly."""

from unittest.mock import AsyncMock, MagicMock, patch

import pytest

from tests.conftest import make_mock_settings


@pytest.fixture
def mock_db():
    db = AsyncMock()
    db.execute = AsyncMock(return_value=MagicMock(scalar_one_or_none=MagicMock(return_value=None)))
    db.add = MagicMock()
    db.flush = AsyncMock()
    db.commit = AsyncMock()
    return db


@pytest.fixture
def settings():
    return make_mock_settings()


class TestRuntimeBuild:
    def test_all_tiers_populated(self, settings, mock_db):
        """When all services are available, RuntimeContainer should populate them all."""
        from src.runtime import build

        svc = build(settings, mock_db)

        # Tier 1 — must be present
        assert svc.world_model is not None
        assert svc.memory_service is not None
        assert svc.extras.get("embedding_service") is not None

        # Tier 2 — should be present when all deps available
        assert svc.event_processor is not None
        assert svc.planner is not None
        assert svc.governor is not None
        assert svc.presenter is not None
        assert svc.audit is not None
        assert svc.working_memory is not None
        assert svc.graph_executor is not None
        assert svc.operator is not None

    def test_tier1_failure_raises(self, settings, mock_db):
        """Tier 1 failure should raise RuntimeBuildError, not silently degrade."""
        from src.runtime import RuntimeBuildError, build

        with patch("src.services.world_model.WorldModel.__init__", side_effect=Exception("boom")):
            with pytest.raises(RuntimeBuildError, match="WorldModel"):
                build(settings, mock_db)

    def test_tier2_failure_degrades(self, settings, mock_db):
        """Tier 2 failure should degrade gracefully, not raise."""
        from src.runtime import build

        with patch("src.services.planner.Planner.__init__", side_effect=Exception("boom")):
            svc = build(settings, mock_db)
            assert svc.planner is None
            # Other services should still be populated
            assert svc.world_model is not None

    def test_tier3_failure_degrades(self, settings, mock_db):
        """Tier 3 failure should not affect other services."""
        from src.runtime import build

        with patch("src.services.vector_store.VectorStore.__init__", side_effect=Exception("boom")):
            svc = build(settings, mock_db)
            assert svc.vector_store is None
            assert svc.world_model is not None

    def test_graph_executor_and_operator_wired(self, settings, mock_db):
        """GraphExecutor and Operator should be wired into ServiceContainer."""
        from src.runtime import build

        svc = build(settings, mock_db)
        assert svc.graph_executor is not None
        assert svc.operator is not None

    def test_service_container_attribute_access(self, settings, mock_db):
        """ServiceContainer should support typed attribute access."""
        from src.runtime import build

        svc = build(settings, mock_db)
        assert svc.world_model is not None
        assert svc.memory_service is not None
