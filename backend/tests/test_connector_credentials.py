"""Tests for connector credential lifecycle — health check, dynamic sources."""

from datetime import datetime, timedelta, timezone
from unittest.mock import AsyncMock, MagicMock


class TestCredentialHealthCheck:
    async def test_valid_token(self):
        from src.services.connector_manager import ConnectorManager

        mock_db = AsyncMock()
        mock_oauth = MagicMock()

        # Mock the internal db_factory to return a token
        mock_token = MagicMock()
        mock_token.expires_at = datetime.now(timezone.utc) + timedelta(hours=1)
        mock_token.refresh_token_encrypted = "encrypted"

        mock_inner_db = AsyncMock()
        mock_result = MagicMock()
        mock_result.scalar_one_or_none = MagicMock(return_value=mock_token)
        mock_inner_db.execute = AsyncMock(return_value=mock_result)

        async_cm = AsyncMock()
        async_cm.__aenter__ = AsyncMock(return_value=mock_inner_db)
        async_cm.__aexit__ = AsyncMock(return_value=False)
        mock_oauth._db_factory = MagicMock(return_value=async_cm)

        mgr = ConnectorManager(mock_db, oauth_manager=mock_oauth)
        status = await mgr.check_credential_health("usr_1", "gmail")
        assert status == "valid"

    async def test_missing_token(self):
        from src.services.connector_manager import ConnectorManager

        mock_db = AsyncMock()
        mock_oauth = MagicMock()

        mock_inner_db = AsyncMock()
        mock_result = MagicMock()
        mock_result.scalar_one_or_none = MagicMock(return_value=None)
        mock_inner_db.execute = AsyncMock(return_value=mock_result)

        async_cm = AsyncMock()
        async_cm.__aenter__ = AsyncMock(return_value=mock_inner_db)
        async_cm.__aexit__ = AsyncMock(return_value=False)
        mock_oauth._db_factory = MagicMock(return_value=async_cm)

        mgr = ConnectorManager(mock_db, oauth_manager=mock_oauth)
        status = await mgr.check_credential_health("usr_1", "github")
        assert status == "missing"


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
