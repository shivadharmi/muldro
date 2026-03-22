"""Tests for connector credential lifecycle — dynamic sources."""

from unittest.mock import AsyncMock, MagicMock


class TestGetCredentialsUsesOAuthManager:
    async def test_returns_plaintext_token(self):
        from src.services.connector_manager import ConnectorManager

        mock_db = AsyncMock()
        mock_oauth = AsyncMock()
        mock_oauth.get_valid_token = AsyncMock(return_value="plaintext_token_123")

        mgr = ConnectorManager(mock_db, oauth_manager=mock_oauth)
        creds = await mgr._get_credentials("usr_1", "gmail")

        assert creds == {"access_token": "plaintext_token_123"}
        mock_oauth.get_valid_token.assert_called_once_with("usr_1", "google")


class TestDynamicObservationSources:
    async def test_default_sources(self):
        from src.services.settings_service import SettingsService

        mock_db = AsyncMock()
        mock_db.execute = AsyncMock(
            return_value=MagicMock(scalar_one_or_none=MagicMock(return_value=None))
        )
        svc = SettingsService(mock_db)
        sources = await svc.get_observation_sources("usr_1")

        providers = [s["provider"] for s in sources]
        assert "gmail" in providers
        assert "calendar" in providers
        assert "github" in providers
        assert "slack" in providers
        assert all(s["enabled"] for s in sources)

    async def test_get_observation_intervals_uses_dynamic_sources(self):
        from src.services.settings_service import SettingsService

        mock_db = AsyncMock()
        mock_db.execute = AsyncMock(
            return_value=MagicMock(scalar_one_or_none=MagicMock(return_value=None))
        )
        svc = SettingsService(mock_db)
        intervals = await svc.get_observation_intervals("usr_1")

        assert intervals["gmail"] == 30
        assert intervals["calendar"] == 180
        assert intervals["github"] == 60
        assert intervals["slack"] == 15
