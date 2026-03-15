"""Tests for OAuth token management and encryption."""

import os
from datetime import datetime, timedelta, timezone
from unittest.mock import AsyncMock, MagicMock, patch

from cryptography.fernet import Fernet

# Generate a test encryption key
TEST_KEY = Fernet.generate_key().decode()


class TestOAuthManager:
    """Test OAuth token storage, retrieval, and refresh."""

    def _make_mock_db(self):
        """Create a mock async session factory."""
        db = AsyncMock()
        db.add = MagicMock()
        db.flush = AsyncMock()
        db.commit = AsyncMock()
        db.rollback = AsyncMock()
        db.delete = AsyncMock()

        factory = MagicMock(return_value=db)
        return factory, db

    @patch.dict(os.environ, {"JARVIS_OAUTH_ENCRYPTION_KEY": TEST_KEY})
    async def test_store_new_token(self):
        factory, db = self._make_mock_db()

        # No existing token
        mock_result = MagicMock()
        mock_result.scalar_one_or_none.return_value = None
        db.execute = AsyncMock(return_value=mock_result)

        from src.services.oauth_manager import OAuthManager

        manager = OAuthManager(factory)
        token_id = await manager.store_token(
            user_id="usr_default",
            provider="google",
            access_token="access_123",
            refresh_token="refresh_456",
            expires_at=datetime.now(timezone.utc) + timedelta(hours=1),
            scopes=["gmail.readonly"],
        )

        assert token_id.startswith("token_")
        db.add.assert_called_once()
        db.commit.assert_awaited_once()

    @patch.dict(os.environ, {"JARVIS_OAUTH_ENCRYPTION_KEY": TEST_KEY})
    async def test_store_updates_existing_token(self):
        factory, db = self._make_mock_db()

        existing = MagicMock()
        existing.token_id = "token_existing"
        mock_result = MagicMock()
        mock_result.scalar_one_or_none.return_value = existing
        db.execute = AsyncMock(return_value=mock_result)

        from src.services.oauth_manager import OAuthManager

        manager = OAuthManager(factory)
        token_id = await manager.store_token(
            user_id="usr_default",
            provider="google",
            access_token="new_access",
        )

        assert token_id == "token_existing"
        # Should not add new, just update
        db.add.assert_not_called()
        db.commit.assert_awaited_once()

    @patch.dict(os.environ, {"JARVIS_OAUTH_ENCRYPTION_KEY": TEST_KEY})
    async def test_get_valid_token_not_expired(self):
        factory, db = self._make_mock_db()
        f = Fernet(TEST_KEY.encode())

        existing = MagicMock()
        existing.access_token_encrypted = f.encrypt(b"my_access_token").decode()
        existing.refresh_token_encrypted = None
        existing.expires_at = datetime.now(timezone.utc) + timedelta(hours=1)
        mock_result = MagicMock()
        mock_result.scalar_one_or_none.return_value = existing
        db.execute = AsyncMock(return_value=mock_result)

        from src.services.oauth_manager import OAuthManager

        manager = OAuthManager(factory)
        token = await manager.get_valid_token("usr_default", "google")

        assert token == "my_access_token"

    @patch.dict(os.environ, {"JARVIS_OAUTH_ENCRYPTION_KEY": TEST_KEY})
    async def test_get_token_not_found(self):
        factory, db = self._make_mock_db()

        mock_result = MagicMock()
        mock_result.scalar_one_or_none.return_value = None
        db.execute = AsyncMock(return_value=mock_result)

        from src.services.oauth_manager import OAuthManager

        manager = OAuthManager(factory)
        token = await manager.get_valid_token("usr_default", "google")

        assert token is None

    @patch.dict(os.environ, {"JARVIS_OAUTH_ENCRYPTION_KEY": TEST_KEY})
    async def test_get_expired_token_no_refresh(self):
        factory, db = self._make_mock_db()
        f = Fernet(TEST_KEY.encode())

        existing = MagicMock()
        existing.access_token_encrypted = f.encrypt(b"expired_token").decode()
        existing.refresh_token_encrypted = None
        existing.expires_at = datetime.now(timezone.utc) - timedelta(hours=1)
        mock_result = MagicMock()
        mock_result.scalar_one_or_none.return_value = existing
        db.execute = AsyncMock(return_value=mock_result)

        from src.services.oauth_manager import OAuthManager

        manager = OAuthManager(factory)
        token = await manager.get_valid_token("usr_default", "google")

        assert token is None

    @patch.dict(os.environ, {"JARVIS_OAUTH_ENCRYPTION_KEY": TEST_KEY})
    async def test_delete_token(self):
        factory, db = self._make_mock_db()

        existing = MagicMock()
        mock_result = MagicMock()
        mock_result.scalar_one_or_none.return_value = existing
        db.execute = AsyncMock(return_value=mock_result)

        from src.services.oauth_manager import OAuthManager

        manager = OAuthManager(factory)
        result = await manager.delete_token("usr_default", "google")

        assert result is True
        db.delete.assert_awaited_once_with(existing)
        db.commit.assert_awaited_once()

    @patch.dict(os.environ, {"JARVIS_OAUTH_ENCRYPTION_KEY": TEST_KEY})
    async def test_delete_token_not_found(self):
        factory, db = self._make_mock_db()

        mock_result = MagicMock()
        mock_result.scalar_one_or_none.return_value = None
        db.execute = AsyncMock(return_value=mock_result)

        from src.services.oauth_manager import OAuthManager

        manager = OAuthManager(factory)
        result = await manager.delete_token("usr_default", "google")

        assert result is False

    def test_encryption_roundtrip(self):
        """Verify Fernet encryption produces valid encrypted tokens."""
        f = Fernet(TEST_KEY.encode())
        original = "my_secret_token_12345"
        encrypted = f.encrypt(original.encode()).decode()
        decrypted = f.decrypt(encrypted.encode()).decode()
        assert decrypted == original
        assert encrypted != original
