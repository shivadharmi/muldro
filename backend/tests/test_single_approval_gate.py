"""Tests for single TrustEngine approval gate in GraphExecutor."""

from unittest.mock import AsyncMock, MagicMock, patch

import pytest

from tests.conftest import make_mock_settings


@pytest.fixture
def mock_db():
    db = AsyncMock()
    db.add = MagicMock()
    db.flush = AsyncMock()
    db.commit = AsyncMock()
    db.refresh = AsyncMock()
    return db


@pytest.fixture
def settings():
    return make_mock_settings()


@pytest.fixture
def mock_trust_engine():
    engine = AsyncMock()
    engine.evaluate = AsyncMock()
    return engine


def _make_executor(settings, mock_db, trust_engine=None):
    with patch("src.services.graph_executor.get_anthropic_client") as mock_client:
        mock_client.return_value = MagicMock()
        from src.services.graph_executor import GraphExecutor

        return GraphExecutor(settings, mock_db, trust_engine=trust_engine)


class TestTrustEngineWiring:
    def test_executor_accepts_trust_engine(self, settings, mock_db, mock_trust_engine):
        executor = _make_executor(settings, mock_db, trust_engine=mock_trust_engine)
        assert executor._trust_engine is mock_trust_engine

    def test_executor_works_without_trust_engine(self, settings, mock_db):
        executor = _make_executor(settings, mock_db)
        assert executor._trust_engine is None
