"""Tests for ToolRegistry — DB-backed tool definitions."""

from unittest.mock import AsyncMock, MagicMock

import pytest

from src.services.tool_registry import ToolRegistry
from src.tools.catalog import EXTERNAL_TOOL_SEEDS, INTERNAL_TOOLS, get_internal_tool_names


@pytest.fixture
def mock_db():
    db = AsyncMock()
    db.add = MagicMock()
    db.flush = AsyncMock()
    return db


@pytest.fixture
def registry(mock_db):
    return ToolRegistry(mock_db)


def _make_tool_def(
    name="gmail_send",
    risk_level="high",
    requires_approval=True,
    connector_type="gmail",
    enabled=True,
    description=None,
):
    t = MagicMock()
    t.tool_id = "tool_001"
    t.name = name
    t.backend = None
    t.source = None
    t.server = None
    t.risk_level = risk_level
    t.requires_approval = requires_approval
    t.connector_type = connector_type
    t.enabled = enabled
    t.description = description
    t.capability = None
    t.verified = False
    t.input_schema = None
    t.timeout_seconds = 30
    t.idempotent = False
    return t


class TestSeedDefaults:
    @pytest.mark.asyncio
    async def test_seed_defaults(self, registry, mock_db):
        """Seeds all tools from catalog (2-pass) when none exist."""
        result_mock = MagicMock()
        result_mock.scalars.return_value = result_mock
        result_mock.all.return_value = []
        mock_db.execute = AsyncMock(return_value=result_mock)

        # Calculate expected total: internal + external catalog tools
        catalog_names = get_internal_tool_names() | {s.name for s in EXTERNAL_TOOL_SEEDS}
        total_unique = len(catalog_names)

        added = await registry.seed_defaults()
        assert added == total_unique
        assert mock_db.add.call_count == total_unique
        mock_db.flush.assert_called_once()

    @pytest.mark.asyncio
    async def test_seed_defaults_skips_existing(self, registry, mock_db):
        """Skips tools that already exist (with matching fields)."""
        from src.services.tool_registry import _schema_for_claude

        # Simulate all catalog tools already existing with matching fields
        existing_tools = []

        # Add internal tools
        for tool in INTERNAL_TOOLS:
            t = _make_tool_def(name=tool.name)
            t.backend = "internal_mcp"
            t.source = "internal"
            t.server = tool.server
            t.capability = tool.capability
            t.risk_level = tool.risk_level
            t.requires_approval = tool.requires_approval
            t.verified = True
            t.input_schema = _schema_for_claude(tool.input_model)
            t.description = tool.description
            existing_tools.append(t)

        # Add external seeds
        for seed in EXTERNAL_TOOL_SEEDS:
            t = _make_tool_def(name=seed.name)
            t.backend = "composite" if seed.server == "_composite" else "external_mcp"
            t.source = "seed"
            t.server = seed.server
            t.capability = seed.capability
            t.risk_level = seed.risk_level
            t.requires_approval = seed.requires_approval
            t.verified = seed.verified
            existing_tools.append(t)

        result_mock = MagicMock()
        result_mock.scalars.return_value = result_mock
        result_mock.all.return_value = existing_tools
        mock_db.execute = AsyncMock(return_value=result_mock)

        added = await registry.seed_defaults()
        assert added == 0
        mock_db.add.assert_not_called()

    @pytest.mark.asyncio
    async def test_seed_defaults_composite_backend(self, registry, mock_db):
        """web_search tool should get backend='composite', not 'external_mcp'."""
        result_mock = MagicMock()
        result_mock.scalars.return_value = result_mock
        result_mock.all.return_value = []
        mock_db.execute = AsyncMock(return_value=result_mock)

        await registry.seed_defaults()

        # Find the web_search add call
        for call in mock_db.add.call_args_list:
            tool_def = call[0][0]
            if tool_def.name == "web_search":
                assert tool_def.backend == "composite", (
                    f"web_search should have backend='composite', got '{tool_def.backend}'"
                )
                return

        pytest.fail("web_search tool was not seeded")

    @pytest.mark.asyncio
    async def test_seed_defaults_writes_internal_description(self, registry, mock_db):
        """Internal tools are inserted with their catalog description (TOOL-P1-2)."""
        result_mock = MagicMock()
        result_mock.scalars.return_value = result_mock
        result_mock.all.return_value = []
        mock_db.execute = AsyncMock(return_value=result_mock)

        await registry.seed_defaults()

        catalog = {tool.name: tool for tool in INTERNAL_TOOLS}
        seeded = {
            call[0][0].name: call[0][0]
            for call in mock_db.add.call_args_list
            if call[0][0].name in catalog
        }
        # Every internal tool seeded carries its catalog description (not NULL).
        for name, tool_def in seeded.items():
            assert tool_def.description == catalog[name].description

    @pytest.mark.asyncio
    async def test_seed_defaults_syncs_diverged_description(self, registry, mock_db):
        """A drifted description on an existing row is re-synced (TOOL-P1-2)."""
        from src.services.tool_registry import _schema_for_claude

        sample = INTERNAL_TOOLS[0]
        existing = _make_tool_def(name=sample.name)
        existing.backend = "internal_mcp"
        existing.source = "internal"
        existing.server = sample.server
        existing.capability = sample.capability
        existing.risk_level = sample.risk_level
        existing.requires_approval = sample.requires_approval
        existing.verified = True
        existing.input_schema = _schema_for_claude(sample.input_model)
        existing.description = "STALE DESCRIPTION"  # only this field diverges

        result_mock = MagicMock()
        result_mock.scalars.return_value = result_mock
        result_mock.all.return_value = [existing]
        mock_db.execute = AsyncMock(return_value=result_mock)

        changed = await registry.seed_defaults()

        assert changed >= 1
        assert existing.description == sample.description


class TestRegisterTool:
    @pytest.mark.asyncio
    async def test_register_new_tool(self, registry, mock_db):
        result_mock = MagicMock()
        result_mock.scalar_one_or_none.return_value = None
        mock_db.execute = AsyncMock(return_value=result_mock)

        tool = await registry.register_tool(
            name="custom_tool",
            risk_level="medium",
            requires_approval=True,
            connector_type="custom",
        )
        mock_db.add.assert_called_once()
        mock_db.flush.assert_called_once()
        assert tool.name == "custom_tool"

    @pytest.mark.asyncio
    async def test_register_updates_existing(self, registry, mock_db):
        existing = _make_tool_def(name="gmail_send", risk_level="high")
        result_mock = MagicMock()
        result_mock.scalar_one_or_none.return_value = existing
        mock_db.execute = AsyncMock(return_value=result_mock)

        tool = await registry.register_tool(
            name="gmail_send",
            risk_level="critical",
            requires_approval=True,
        )
        assert tool.risk_level == "critical"
        mock_db.add.assert_not_called()


class TestGetToolCaching:
    @pytest.mark.asyncio
    async def test_get_tool_from_db(self, registry, mock_db):
        tool_def = _make_tool_def(name="gmail_read")
        result_mock = MagicMock()
        result_mock.scalar_one_or_none.return_value = tool_def
        mock_db.execute = AsyncMock(return_value=result_mock)

        tool = await registry.get_tool("gmail_read")
        assert tool.name == "gmail_read"
        mock_db.execute.assert_called_once()

    @pytest.mark.asyncio
    async def test_get_tool_caching(self, registry, mock_db):
        """Second call should use cache, not hit DB again."""
        tool_def = _make_tool_def(name="gmail_read")
        result_mock = MagicMock()
        result_mock.scalar_one_or_none.return_value = tool_def
        mock_db.execute = AsyncMock(return_value=result_mock)

        await registry.get_tool("gmail_read")
        await registry.get_tool("gmail_read")
        # Only one DB call — second was cached
        assert mock_db.execute.call_count == 1

    @pytest.mark.asyncio
    async def test_get_tool_not_found(self, registry, mock_db):
        result_mock = MagicMock()
        result_mock.scalar_one_or_none.return_value = None
        mock_db.execute = AsyncMock(return_value=result_mock)

        tool = await registry.get_tool("nonexistent")
        assert tool is None


class TestIsWriteTool:
    @pytest.mark.asyncio
    async def test_is_write_tool(self, registry, mock_db):
        tool_def = _make_tool_def(name="gmail_send", requires_approval=True)
        result_mock = MagicMock()
        result_mock.scalar_one_or_none.return_value = tool_def
        mock_db.execute = AsyncMock(return_value=result_mock)

        assert await registry.is_write_tool("gmail_send") is True

    @pytest.mark.asyncio
    async def test_is_write_tool_read_only(self, registry, mock_db):
        tool_def = _make_tool_def(name="gmail_list", requires_approval=False)
        result_mock = MagicMock()
        result_mock.scalar_one_or_none.return_value = tool_def
        mock_db.execute = AsyncMock(return_value=result_mock)

        assert await registry.is_write_tool("gmail_list") is False

    @pytest.mark.asyncio
    async def test_is_write_tool_unknown(self, registry, mock_db):
        result_mock = MagicMock()
        result_mock.scalar_one_or_none.return_value = None
        mock_db.execute = AsyncMock(return_value=result_mock)

        assert await registry.is_write_tool("unknown") is False


class TestIsBlockedTool:
    @pytest.mark.asyncio
    async def test_is_blocked_tool(self, registry, mock_db):
        tool_def = _make_tool_def(name="gmail_delete", enabled=False)
        result_mock = MagicMock()
        result_mock.scalar_one_or_none.return_value = tool_def
        mock_db.execute = AsyncMock(return_value=result_mock)

        assert await registry.is_blocked_tool("gmail_delete") is True

    @pytest.mark.asyncio
    async def test_is_not_blocked(self, registry, mock_db):
        tool_def = _make_tool_def(name="gmail_send", enabled=True)
        result_mock = MagicMock()
        result_mock.scalar_one_or_none.return_value = tool_def
        mock_db.execute = AsyncMock(return_value=result_mock)

        assert await registry.is_blocked_tool("gmail_send") is False

    @pytest.mark.asyncio
    async def test_is_blocked_unknown(self, registry, mock_db):
        result_mock = MagicMock()
        result_mock.scalar_one_or_none.return_value = None
        mock_db.execute = AsyncMock(return_value=result_mock)

        assert await registry.is_blocked_tool("unknown") is False


class TestClassifyRisk:
    @pytest.mark.asyncio
    async def test_classify_risk(self, registry, mock_db):
        tool_def = _make_tool_def(name="gmail_send", risk_level="high")
        result_mock = MagicMock()
        result_mock.scalar_one_or_none.return_value = tool_def
        mock_db.execute = AsyncMock(return_value=result_mock)

        assert await registry.classify_risk("gmail_send") == "high"

    @pytest.mark.asyncio
    async def test_classify_risk_unknown(self, registry, mock_db):
        result_mock = MagicMock()
        result_mock.scalar_one_or_none.return_value = None
        mock_db.execute = AsyncMock(return_value=result_mock)

        assert await registry.classify_risk("unknown") == "low"


class TestListForTaskType:
    @pytest.mark.asyncio
    async def test_list_for_task_type(self, registry, mock_db):
        gmail_tool = _make_tool_def(name="gmail_send", connector_type="gmail")
        result_mock = MagicMock()
        result_mock.scalars.return_value.all.return_value = [gmail_tool]
        mock_db.execute = AsyncMock(return_value=result_mock)

        tools = await registry.list_for_task_type("send_email")
        assert len(tools) == 1
        assert tools[0].name == "gmail_send"

    @pytest.mark.asyncio
    async def test_list_for_unknown_type_defaults_internal(self, registry, mock_db):
        internal_tool = _make_tool_def(name="search_memory", connector_type="internal")
        result_mock = MagicMock()
        result_mock.scalars.return_value.all.return_value = [internal_tool]
        mock_db.execute = AsyncMock(return_value=result_mock)

        tools = await registry.list_for_task_type("unknown_type")
        assert len(tools) == 1
        assert tools[0].connector_type == "internal"
