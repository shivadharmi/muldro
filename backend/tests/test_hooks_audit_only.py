"""Tests for audit-only governor_pre_tool_hook (Spec 2B-i)."""

from unittest.mock import AsyncMock, MagicMock, patch

import pytest


@pytest.fixture
def mock_db_factory():
    db = AsyncMock()
    db.add = MagicMock()
    db.flush = AsyncMock()
    db.commit = AsyncMock()

    factory = MagicMock()
    factory.return_value.__aenter__ = AsyncMock(return_value=db)
    factory.return_value.__aexit__ = AsyncMock(return_value=False)
    return factory, db


class TestAuditOnlyHook:
    async def test_read_tool_returns_allowed(self):
        from src.orchestrator.hooks import governor_pre_tool_hook

        result = await governor_pre_tool_hook(
            "search", {}, "perceiver", user_id="usr_1", workspace_id="ws_1"
        )
        assert result["allowed"] is True

    async def test_write_tool_returns_allowed(self, mock_db_factory):
        """Write tools previously blocked — now always allowed."""
        factory, db = mock_db_factory

        mock_tool = MagicMock()
        mock_tool.enabled = True
        mock_tool.requires_approval = True
        mock_tool.risk_level = "high"

        with patch("src.orchestrator.hooks.ToolRegistry") as mock_registry_cls:
            registry_instance = AsyncMock()
            registry_instance.get_tool = AsyncMock(return_value=mock_tool)
            mock_registry_cls.return_value = registry_instance

            from src.orchestrator.hooks import governor_pre_tool_hook

            result = await governor_pre_tool_hook(
                "gmail_send_email",
                {"to": "test@example.com", "subject": "Hi"},
                "operator",
                user_id="usr_1",
                workspace_id="ws_1",
                db_factory=factory,
            )

        assert result["allowed"] is True

    async def test_blocked_tool_still_blocked(self, mock_db_factory):
        """Blocked tools remain blocked (safety invariant)."""
        factory, db = mock_db_factory

        mock_tool = MagicMock()
        mock_tool.enabled = False
        mock_tool.requires_approval = False
        mock_tool.risk_level = "low"

        with patch("src.orchestrator.hooks.ToolRegistry") as mock_registry_cls:
            registry_instance = AsyncMock()
            registry_instance.get_tool = AsyncMock(return_value=mock_tool)
            mock_registry_cls.return_value = registry_instance

            from src.orchestrator.hooks import governor_pre_tool_hook

            result = await governor_pre_tool_hook(
                "dangerous_tool",
                {},
                "operator",
                user_id="usr_1",
                workspace_id="ws_1",
                db_factory=factory,
            )

        assert result["allowed"] is False

    async def test_no_approval_record_created(self, mock_db_factory):
        """Hook must NOT create approval records anymore."""
        factory, db = mock_db_factory

        mock_tool = MagicMock()
        mock_tool.enabled = True
        mock_tool.requires_approval = True
        mock_tool.risk_level = "medium"

        with patch("src.orchestrator.hooks.ToolRegistry") as mock_registry_cls:
            registry_instance = AsyncMock()
            registry_instance.get_tool = AsyncMock(return_value=mock_tool)
            mock_registry_cls.return_value = registry_instance

            from src.orchestrator.hooks import governor_pre_tool_hook

            result = await governor_pre_tool_hook(
                "gmail_send_email",
                {"to": "test@example.com"},
                "operator",
                user_id="usr_1",
                workspace_id="ws_1",
                db_factory=factory,
            )

            assert result["allowed"] is True
            # Verify no approval was created (no db.add calls)
            db.add.assert_not_called()
