"""Tests for Neo4j sync in the update_entity MCP tool."""

from contextlib import asynccontextmanager
from unittest.mock import AsyncMock, MagicMock, patch

import pytest

from tests.conftest import TEST_USER_ID, TEST_WORKSPACE_ID, make_mock_settings

# GraphSyncService is imported lazily inside update_entity via:
#   from src.services.graph_sync import GraphSyncService
# We must patch it at the source so the lazy import picks up the mock.
_GSS_PATH = "src.services.graph_sync.GraphSyncService"


# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------


def _make_entity(entity_id: str = "ent_001"):
    entity = MagicMock()
    entity.entity_id = entity_id
    entity.attributes = {}
    return entity


def _make_db(entity=None):
    """Return a mock AsyncSession whose execute() returns the given entity."""
    db = AsyncMock()
    scalar_result = MagicMock()
    scalar_result.scalar_one_or_none = MagicMock(return_value=entity)
    db.execute = AsyncMock(return_value=scalar_result)
    db.flush = AsyncMock()
    db.commit = AsyncMock()
    db.rollback = AsyncMock()
    db.add = MagicMock()

    # record_fact wraps its close+insert in a begin_nested() SAVEPOINT; model it as a no-op
    # async context manager so the mocked-DB path exercises the real code without a real txn.
    @asynccontextmanager
    async def _savepoint():
        yield

    db.begin_nested = MagicMock(side_effect=lambda: _savepoint())
    return db


@asynccontextmanager
async def _db_ctx(db):
    yield db


# ---------------------------------------------------------------------------
# Tests
# ---------------------------------------------------------------------------


@pytest.mark.asyncio
class TestUpdateEntityNeo4jSync:
    async def test_sync_called_when_neo4j_url_set(self):
        """When neo4j_url is configured, GraphSyncService.sync_entity_by_id is called."""
        import src.tools.intelligence_server as srv

        settings = make_mock_settings()
        settings.neo4j_url = "bolt://localhost:7687"

        entity = _make_entity("ent_123")
        db = _make_db(entity)

        mock_gs = AsyncMock()
        mock_gs.sync_entity_by_id = AsyncMock()
        mock_gs.close = AsyncMock()

        original_settings = srv._shared._settings
        original_db_factory = srv._shared._db_factory
        try:
            srv._shared._settings = settings
            srv._shared._db_factory = lambda: _db_ctx(db)

            with patch(_GSS_PATH, return_value=mock_gs) as mock_gss_cls:
                ctx = MagicMock()
                result = await srv.update_entity(
                    entity_id="ent_123",
                    ctx=ctx,
                    user_id=TEST_USER_ID,
                    attributes='{"foo": "bar"}',
                    workspace_id=TEST_WORKSPACE_ID,
                )

            assert result["status"] == "updated"
            assert result["entity_id"] == "ent_123"
            mock_gss_cls.assert_called_once_with(settings, db)
            mock_gs.sync_entity_by_id.assert_awaited_once_with("ent_123")
            mock_gs.close.assert_awaited_once()
        finally:
            srv._shared._settings = original_settings
            srv._shared._db_factory = original_db_factory

    async def test_sync_not_called_when_neo4j_url_empty(self):
        """When neo4j_url is empty/unset, GraphSyncService is never instantiated."""
        import src.tools.intelligence_server as srv

        settings = make_mock_settings()
        settings.neo4j_url = ""

        entity = _make_entity("ent_456")
        db = _make_db(entity)

        original_settings = srv._shared._settings
        original_db_factory = srv._shared._db_factory
        try:
            srv._shared._settings = settings
            srv._shared._db_factory = lambda: _db_ctx(db)

            with patch(_GSS_PATH) as mock_gss_cls:
                ctx = MagicMock()
                result = await srv.update_entity(
                    entity_id="ent_456",
                    ctx=ctx,
                    user_id=TEST_USER_ID,
                    attributes='{"key": "value"}',
                    workspace_id=TEST_WORKSPACE_ID,
                )

            assert result["status"] == "updated"
            mock_gss_cls.assert_not_called()
        finally:
            srv._shared._settings = original_settings
            srv._shared._db_factory = original_db_factory

    async def test_sync_failure_is_best_effort(self):
        """When Neo4j sync raises, update_entity still returns status=updated."""
        import src.tools.intelligence_server as srv

        settings = make_mock_settings()
        settings.neo4j_url = "bolt://localhost:7687"

        entity = _make_entity("ent_789")
        db = _make_db(entity)

        mock_gs = AsyncMock()
        mock_gs.sync_entity_by_id = AsyncMock(side_effect=RuntimeError("Neo4j connection refused"))
        mock_gs.close = AsyncMock()

        original_settings = srv._shared._settings
        original_db_factory = srv._shared._db_factory
        try:
            srv._shared._settings = settings
            srv._shared._db_factory = lambda: _db_ctx(db)

            with patch(_GSS_PATH, return_value=mock_gs):
                ctx = MagicMock()
                result = await srv.update_entity(
                    entity_id="ent_789",
                    ctx=ctx,
                    user_id=TEST_USER_ID,
                    workspace_id=TEST_WORKSPACE_ID,
                )

            # Still succeeds — sync is best-effort
            assert result["status"] == "updated"
            assert result["entity_id"] == "ent_789"
            db.commit.assert_awaited()
        finally:
            srv._shared._settings = original_settings
            srv._shared._db_factory = original_db_factory

    async def test_entity_not_found_skips_sync(self):
        """When entity is not found, update_entity returns not_found without touching Neo4j."""
        import src.tools.intelligence_server as srv

        settings = make_mock_settings()
        settings.neo4j_url = "bolt://localhost:7687"

        db = _make_db(entity=None)  # scalar_one_or_none returns None

        original_settings = srv._shared._settings
        original_db_factory = srv._shared._db_factory
        try:
            srv._shared._settings = settings
            srv._shared._db_factory = lambda: _db_ctx(db)

            with patch(_GSS_PATH) as mock_gss_cls:
                ctx = MagicMock()
                result = await srv.update_entity(
                    entity_id="ent_missing",
                    ctx=ctx,
                    user_id=TEST_USER_ID,
                    workspace_id=TEST_WORKSPACE_ID,
                )

            assert result["status"] == "not_found"
            mock_gss_cls.assert_not_called()
        finally:
            srv._shared._settings = original_settings
            srv._shared._db_factory = original_db_factory

    async def test_sync_not_called_when_settings_none(self):
        """When _settings is None (unconfigured server), sync is safely skipped."""
        import src.tools.intelligence_server as srv

        entity = _make_entity("ent_999")
        db = _make_db(entity)

        original_settings = srv._shared._settings
        original_db_factory = srv._shared._db_factory
        try:
            srv._shared._settings = None
            srv._shared._db_factory = lambda: _db_ctx(db)

            with patch(_GSS_PATH) as mock_gss_cls:
                ctx = MagicMock()
                result = await srv.update_entity(
                    entity_id="ent_999",
                    ctx=ctx,
                    user_id=TEST_USER_ID,
                    workspace_id=TEST_WORKSPACE_ID,
                )

            assert result["status"] == "updated"
            mock_gss_cls.assert_not_called()
        finally:
            srv._shared._settings = original_settings
            srv._shared._db_factory = original_db_factory
