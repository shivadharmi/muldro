"""Tests for OAuth token management and encryption."""

import os
from datetime import datetime, timedelta, timezone
from unittest.mock import AsyncMock, MagicMock, patch

from cryptography.fernet import Fernet

from tests.conftest import TEST_USER_ID

# Generate a test encryption key
TEST_KEY = Fernet.generate_key().decode()


class TestOAuthManager:
    """Test OAuth token storage, retrieval, and refresh."""

    def _make_mock_db(self):
        """Create a mock async session factory with context manager support."""
        db = AsyncMock()
        db.add = MagicMock()
        db.flush = AsyncMock()
        db.commit = AsyncMock()
        db.rollback = AsyncMock()
        db.delete = AsyncMock()

        # Support both factory() and async with factory() as db:
        ctx = AsyncMock()
        ctx.__aenter__ = AsyncMock(return_value=db)
        ctx.__aexit__ = AsyncMock(return_value=False)
        factory = MagicMock(return_value=ctx)
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
            user_id=TEST_USER_ID,
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
            user_id=TEST_USER_ID,
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
        token = await manager.get_valid_token(TEST_USER_ID, "google")

        assert token == "my_access_token"

    @patch.dict(os.environ, {"JARVIS_OAUTH_ENCRYPTION_KEY": TEST_KEY})
    async def test_get_token_not_found(self):
        factory, db = self._make_mock_db()

        mock_result = MagicMock()
        mock_result.scalar_one_or_none.return_value = None
        db.execute = AsyncMock(return_value=mock_result)

        from src.services.oauth_manager import OAuthManager

        manager = OAuthManager(factory)
        token = await manager.get_valid_token(TEST_USER_ID, "google")

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
        token = await manager.get_valid_token(TEST_USER_ID, "google")

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
        result = await manager.delete_token(TEST_USER_ID, "google")

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
        result = await manager.delete_token(TEST_USER_ID, "google")

        assert result is False

    def test_encryption_roundtrip(self):
        """Verify Fernet encryption produces valid encrypted tokens."""
        f = Fernet(TEST_KEY.encode())
        original = "my_secret_token_12345"
        encrypted = f.encrypt(original.encode()).decode()
        decrypted = f.decrypt(encrypted.encode()).decode()
        assert decrypted == original
        assert encrypted != original


class TestGetValidTokenWithReason:
    """Characterize the reason-aware token outcome so callers can distinguish
    'never connected / unauthorized' (permanent) from a refresh blip (transient)."""

    def _make_mock_db(self):
        db = AsyncMock()
        db.add = MagicMock()
        db.commit = AsyncMock()
        ctx = AsyncMock()
        ctx.__aenter__ = AsyncMock(return_value=db)
        ctx.__aexit__ = AsyncMock(return_value=False)
        factory = MagicMock(return_value=ctx)
        return factory, db

    @patch.dict(os.environ, {"JARVIS_OAUTH_ENCRYPTION_KEY": TEST_KEY})
    async def test_ok_returns_token_and_reason(self):
        factory, db = self._make_mock_db()
        f = Fernet(TEST_KEY.encode())
        existing = MagicMock()
        existing.access_token_encrypted = f.encrypt(b"live_token").decode()
        existing.refresh_token_encrypted = None
        existing.expires_at = datetime.now(timezone.utc) + timedelta(hours=1)
        mock_result = MagicMock()
        mock_result.scalar_one_or_none.return_value = existing
        db.execute = AsyncMock(return_value=mock_result)

        from src.services.oauth_manager import OAuthManager

        manager = OAuthManager(factory)
        res = await manager.get_valid_token_with_reason(TEST_USER_ID, "google")

        assert res.token == "live_token"
        assert res.reason == "ok"
        # Back-compat wrapper still returns the bare token string.
        assert await manager.get_valid_token(TEST_USER_ID, "google") == "live_token"

    @patch.dict(os.environ, {"JARVIS_OAUTH_ENCRYPTION_KEY": TEST_KEY})
    async def test_no_token_row_returns_no_token_reason(self):
        factory, db = self._make_mock_db()
        mock_result = MagicMock()
        mock_result.scalar_one_or_none.return_value = None
        db.execute = AsyncMock(return_value=mock_result)

        from src.services.oauth_manager import OAuthManager

        manager = OAuthManager(factory)
        res = await manager.get_valid_token_with_reason(TEST_USER_ID, "google")

        assert res.token is None
        assert res.reason == "no_token"

    @patch.dict(os.environ, {"JARVIS_OAUTH_ENCRYPTION_KEY": TEST_KEY})
    async def test_expired_no_refresh_returns_no_refresh_token_reason(self):
        factory, db = self._make_mock_db()
        f = Fernet(TEST_KEY.encode())
        existing = MagicMock()
        existing.access_token_encrypted = f.encrypt(b"expired").decode()
        existing.refresh_token_encrypted = None
        existing.expires_at = datetime.now(timezone.utc) - timedelta(hours=1)
        mock_result = MagicMock()
        mock_result.scalar_one_or_none.return_value = existing
        db.execute = AsyncMock(return_value=mock_result)

        from src.services.oauth_manager import OAuthManager

        manager = OAuthManager(factory)
        res = await manager.get_valid_token_with_reason(TEST_USER_ID, "google")

        assert res.token is None
        assert res.reason == "no_refresh_token"

    @patch.dict(os.environ, {"JARVIS_OAUTH_ENCRYPTION_KEY": TEST_KEY})
    async def test_refresh_http_failure_returns_refresh_failed_reason(self):
        factory, db = self._make_mock_db()
        f = Fernet(TEST_KEY.encode())
        existing = MagicMock()
        existing.access_token_encrypted = f.encrypt(b"expired").decode()
        existing.refresh_token_encrypted = f.encrypt(b"refresh_tok").decode()
        existing.expires_at = datetime.now(timezone.utc) - timedelta(hours=1)
        mock_result = MagicMock()
        mock_result.scalar_one_or_none.return_value = existing
        db.execute = AsyncMock(return_value=mock_result)

        from src.services.oauth_manager import OAuthManager

        manager = OAuthManager(factory)
        # Refresh HTTP call fails (network/5xx) -> _refresh_token returns None.
        with patch.object(manager, "_refresh_token", AsyncMock(return_value=None)):
            res = await manager.get_valid_token_with_reason(TEST_USER_ID, "google")

        assert res.token is None
        assert res.reason == "refresh_failed"
