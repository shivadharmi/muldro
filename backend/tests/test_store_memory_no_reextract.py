"""Task 10 Change B: the store_memory MCP tool must stop re-running entity
extraction (WorldModel.extract_from_text) as a side effect of storing a memory.
Extraction is now solely owned by the tier-gated worker consumers
(_handle_entity_extraction). The memory itself must still be stored and the
tool's return shape (status/memory_id/entity_ids) must stay stable.
"""

from unittest.mock import AsyncMock, MagicMock, patch

import pytest

from tests.conftest import TEST_USER_ID, TEST_WORKSPACE_ID


def _wire_shared(mock_db):
    import src.tools.intelligence_server._shared as shared

    db_ctx = AsyncMock()
    db_ctx.__aenter__ = AsyncMock(return_value=mock_db)
    db_ctx.__aexit__ = AsyncMock(return_value=False)
    db_factory = MagicMock(return_value=db_ctx)

    settings = MagicMock()
    settings.neo4j_url = ""
    services = MagicMock()

    shared.configure(db_factory, settings, services)
    return shared


class TestStoreMemoryNoReExtract:
    @pytest.mark.asyncio
    async def test_store_memory_does_not_call_extract_from_text(self):
        from src.tools.intelligence_server.memory import store_memory

        mock_db = AsyncMock()
        mock_db.commit = AsyncMock()
        mock_db.rollback = AsyncMock()
        shared = _wire_shared(mock_db)

        try:
            with (
                patch("src.services.memory_service.MemoryService") as mock_mem_cls,
                patch(
                    "src.services.world_model.WorldModel.extract_from_text",
                    new=AsyncMock(return_value=["ent_should_not_appear"]),
                ) as mock_extract,
            ):
                mem_instance = mock_mem_cls.return_value
                mem_instance.store_memory = AsyncMock(return_value="mem_123")

                ctx = MagicMock()
                ctx.info = AsyncMock()

                result = await store_memory(
                    user_id=TEST_USER_ID,
                    text="Met with Grace from Acme today",
                    ctx=ctx,
                    memory_type="fact",
                    scope="general",
                    workspace_id=TEST_WORKSPACE_ID,
                )

                mock_extract.assert_not_awaited()
                mem_instance.store_memory.assert_awaited_once()
                assert result["status"] == "stored"
                assert result["memory_id"] == "mem_123"
                assert result["entity_ids"] == []
        finally:
            shared.configure(None, None, None)
