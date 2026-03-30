"""Tests for MCP onboarding service."""

from unittest.mock import AsyncMock, MagicMock

from tests.conftest import TEST_USER_ID, TEST_WORKSPACE_ID


class TestOnboarding:
    async def test_register_creates_catalog_entry(self):
        from src.integrations.onboarding import MCPOnboardingService

        mock_result = MagicMock()
        mock_result.scalar_one_or_none.return_value = None

        db = AsyncMock()
        db.execute = AsyncMock(return_value=mock_result)
        db.add = MagicMock()
        db.flush = AsyncMock()

        svc = MCPOnboardingService(db, TEST_WORKSPACE_ID)
        result = await svc.register(
            user_id=TEST_USER_ID,
            server_name="test-server",
            display_name="Test Server",
            description="A test server",
        )

        assert result.status == "registered"
        db.add.assert_called_once()

    async def test_register_duplicate_fails(self):
        from src.integrations.onboarding import MCPOnboardingService

        mock_existing = MagicMock()
        mock_result = MagicMock()
        mock_result.scalar_one_or_none.return_value = mock_existing

        db = AsyncMock()
        db.execute = AsyncMock(return_value=mock_result)

        svc = MCPOnboardingService(db, TEST_WORKSPACE_ID)
        result = await svc.register(
            user_id=TEST_USER_ID,
            server_name="existing-server",
            display_name="Existing",
        )

        assert result.status == "failed"
        assert "already exists" in result.error

    async def test_inspect_updates_catalog(self):
        from src.integrations.onboarding import MCPOnboardingService

        mock_catalog = MagicMock()
        mock_catalog.server_name = "test-server"
        mock_catalog.status = "pending"
        mock_result = MagicMock()
        mock_result.scalar_one_or_none.return_value = mock_catalog

        db = AsyncMock()
        db.execute = AsyncMock(return_value=mock_result)

        svc = MCPOnboardingService(db, TEST_WORKSPACE_ID)
        result = await svc.inspect(
            catalog_id="mcat_001",
            tools=[
                {"name": "search", "description": "Search items"},
                {"name": "list", "description": "List items"},
            ],
        )

        assert result.status == "inspected"
        assert result.inspection is not None
        assert result.inspection.tool_count == 2
        assert mock_catalog.manifest_hash is not None

    async def test_inspect_not_found(self):
        from src.integrations.onboarding import MCPOnboardingService

        mock_result = MagicMock()
        mock_result.scalar_one_or_none.return_value = None

        db = AsyncMock()
        db.execute = AsyncMock(return_value=mock_result)

        svc = MCPOnboardingService(db, TEST_WORKSPACE_ID)
        result = await svc.inspect("mcat_nonexistent", [])
        assert result.status == "failed"
        assert "not found" in result.error

    async def test_activate_creates_installation(self):
        from src.integrations.onboarding import MCPOnboardingService

        mock_catalog = MagicMock()
        mock_catalog.server_name = "test-server"
        mock_catalog.display_name = "Test"
        mock_catalog.status = "inspected"
        mock_catalog.transport = "stdio"
        mock_catalog.command = "npx"
        mock_catalog.args_template = None
        mock_catalog.env_template = None
        mock_catalog.remote_url = None
        mock_catalog.default_trust_tier = "T2"
        mock_catalog.manifest_hash = "abc123"
        mock_catalog.capabilities = ["search.web"]

        mock_result = MagicMock()
        mock_result.scalar_one_or_none.return_value = mock_catalog

        db = AsyncMock()
        db.execute = AsyncMock(return_value=mock_result)
        db.add = MagicMock()
        db.flush = AsyncMock()

        svc = MCPOnboardingService(db, TEST_WORKSPACE_ID)
        result = await svc.activate("mcat_001", TEST_USER_ID)

        assert result.status == "activated"
        # trust + installation = 2 adds (capability bindings removed)
        assert db.add.call_count == 2
