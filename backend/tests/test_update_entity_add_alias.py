"""Regression tests for the add_alias branch of the update_entity MCP tool.

Previously ``update_entity`` constructed ``EntityAlias(alias_id=..., alias_value=...)``
which failed at runtime because the model declares ``alias`` (not ``alias_value``),
has no ``alias_id`` field, and requires ``workspace_id``. The TypeError was raised
from SQLAlchemy's declarative constructor before the row could be added.

These tests lock in the corrected construction — correct field names, required
workspace_id, sensible alias_type guess, and a duplicate-skip guard.
"""

from __future__ import annotations

from contextlib import asynccontextmanager
from unittest.mock import AsyncMock, MagicMock, patch

import pytest

from tests.conftest import TEST_USER_ID, TEST_WORKSPACE_ID, make_mock_settings

_GSS_PATH = "src.services.graph_sync.GraphSyncService"


def _make_entity(entity_id: str = "ent_001"):
    entity = MagicMock()
    entity.entity_id = entity_id
    entity.attributes = {}
    return entity


def _make_db(entity=None, existing_alias=None):
    """Mock AsyncSession. First execute() returns the entity lookup,
    subsequent ones return the duplicate-alias check result."""
    db = AsyncMock()
    entity_result = MagicMock()
    entity_result.scalar_one_or_none = MagicMock(return_value=entity)
    dup_result = MagicMock()
    dup_result.scalar_one_or_none = MagicMock(return_value=existing_alias)

    db.execute = AsyncMock(side_effect=[entity_result, dup_result])
    db.flush = AsyncMock()
    db.commit = AsyncMock()
    db.rollback = AsyncMock()
    db.add = MagicMock()
    return db


@asynccontextmanager
async def _db_ctx(db):
    yield db


@pytest.mark.asyncio
class TestUpdateEntityAddAlias:
    async def test_add_alias_uses_correct_field_names(self):
        """The EntityAlias row must be constructed with fields the model accepts."""
        import src.tools.intelligence_server as srv
        from src.models.entities import EntityAlias

        settings = make_mock_settings()
        settings.neo4j_url = ""  # skip neo4j sync branch

        entity = _make_entity("ent_alpha")
        db = _make_db(entity, existing_alias=None)

        original_settings = srv._shared._settings
        original_db_factory = srv._shared._db_factory
        try:
            srv._shared._settings = settings
            srv._shared._db_factory = lambda: _db_ctx(db)

            with patch(_GSS_PATH):
                result = await srv.update_entity(
                    entity_id="ent_alpha",
                    ctx=MagicMock(),
                    user_id=TEST_USER_ID,
                    add_alias="alice@example.com",
                    workspace_id=TEST_WORKSPACE_ID,
                )

            assert result["status"] == "updated"
            # The row added must be an EntityAlias with correct fields
            added_args = [call.args[0] for call in db.add.call_args_list]
            aliases = [a for a in added_args if isinstance(a, EntityAlias)]
            assert len(aliases) == 1
            alias = aliases[0]
            assert alias.entity_id == "ent_alpha"
            assert alias.alias == "alice@example.com"
            assert alias.alias_type == "email"
            assert alias.workspace_id == TEST_WORKSPACE_ID
        finally:
            srv._shared._settings = original_settings
            srv._shared._db_factory = original_db_factory

    async def test_alias_type_handle_for_at_prefix(self):
        import src.tools.intelligence_server as srv
        from src.models.entities import EntityAlias

        settings = make_mock_settings()
        settings.neo4j_url = ""

        db = _make_db(_make_entity("ent_x"), existing_alias=None)

        original_settings = srv._shared._settings
        original_db_factory = srv._shared._db_factory
        try:
            srv._shared._settings = settings
            srv._shared._db_factory = lambda: _db_ctx(db)

            with patch(_GSS_PATH):
                await srv.update_entity(
                    entity_id="ent_x",
                    ctx=MagicMock(),
                    user_id=TEST_USER_ID,
                    add_alias="@shiva",
                    workspace_id=TEST_WORKSPACE_ID,
                )

            aliases = [
                c.args[0] for c in db.add.call_args_list if isinstance(c.args[0], EntityAlias)
            ]
            assert aliases[0].alias_type == "handle"
        finally:
            srv._shared._settings = original_settings
            srv._shared._db_factory = original_db_factory

    async def test_alias_type_name_for_plain_string(self):
        import src.tools.intelligence_server as srv
        from src.models.entities import EntityAlias

        settings = make_mock_settings()
        settings.neo4j_url = ""

        db = _make_db(_make_entity("ent_y"), existing_alias=None)

        original_settings = srv._shared._settings
        original_db_factory = srv._shared._db_factory
        try:
            srv._shared._settings = settings
            srv._shared._db_factory = lambda: _db_ctx(db)

            with patch(_GSS_PATH):
                await srv.update_entity(
                    entity_id="ent_y",
                    ctx=MagicMock(),
                    user_id=TEST_USER_ID,
                    add_alias="Shiva B",
                    workspace_id=TEST_WORKSPACE_ID,
                )

            aliases = [
                c.args[0] for c in db.add.call_args_list if isinstance(c.args[0], EntityAlias)
            ]
            assert aliases[0].alias_type == "name"
        finally:
            srv._shared._settings = original_settings
            srv._shared._db_factory = original_db_factory

    async def test_duplicate_alias_is_skipped(self):
        """If the alias already exists for this entity, we must not add it again."""
        import src.tools.intelligence_server as srv
        from src.models.entities import EntityAlias

        settings = make_mock_settings()
        settings.neo4j_url = ""

        existing = MagicMock()  # truthy value simulates an existing alias row
        db = _make_db(_make_entity("ent_dup"), existing_alias=existing)

        original_settings = srv._shared._settings
        original_db_factory = srv._shared._db_factory
        try:
            srv._shared._settings = settings
            srv._shared._db_factory = lambda: _db_ctx(db)

            with patch(_GSS_PATH):
                result = await srv.update_entity(
                    entity_id="ent_dup",
                    ctx=MagicMock(),
                    user_id=TEST_USER_ID,
                    add_alias="dupe@example.com",
                    workspace_id=TEST_WORKSPACE_ID,
                )

            assert result["status"] == "updated"
            aliases = [
                c.args[0] for c in db.add.call_args_list if isinstance(c.args[0], EntityAlias)
            ]
            assert aliases == []  # no new alias added
        finally:
            srv._shared._settings = original_settings
            srv._shared._db_factory = original_db_factory
