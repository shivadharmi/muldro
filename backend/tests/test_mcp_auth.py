"""Tests for MCP authentication wiring."""

from unittest.mock import AsyncMock, MagicMock, patch

import pytest

from tests.conftest import make_mock_settings


class TestOAuthManagerWiring:
    """OAuthManager must be passed to initialize_mcp_bridge."""

    @pytest.mark.asyncio
    async def test_oauth_manager_passed_to_bridge(self):
        """Verify OAuthManager is created and passed to initialize_mcp_bridge."""
        mock_settings = make_mock_settings()
        mock_settings.oauth_encryption_key = "test-key"
        mock_settings.redis_url = "redis://localhost:6379/0"

        mock_init_bridge = AsyncMock()
        mock_oauth_cls = MagicMock()
        mock_oauth_instance = MagicMock()
        mock_oauth_cls.return_value = mock_oauth_instance
        mock_db_factory = MagicMock()

        with (
            patch("src.api.app.get_settings", return_value=mock_settings),
            patch("src.connectors.mcp_bridge.initialize_mcp_bridge", mock_init_bridge),
            patch("src.services.oauth_manager.OAuthManager", mock_oauth_cls),
            patch("src.models.database.get_session_factory", return_value=mock_db_factory),
        ):
            from src.api.app import create_app

            app = create_app()

            # Trigger lifespan startup
            async with app.router.lifespan_context(app):
                pass

            # Verify OAuthManager was instantiated
            assert mock_oauth_cls.called, "OAuthManager should have been instantiated"

            # Verify initialize_mcp_bridge was called with an oauth_manager
            mock_init_bridge.assert_called_once()
            call_kwargs = mock_init_bridge.call_args
            oauth_arg = call_kwargs.kwargs.get("oauth_manager")

            assert oauth_arg is mock_oauth_instance, (
                f"initialize_mcp_bridge must receive the OAuthManager instance, got {oauth_arg}"
            )
