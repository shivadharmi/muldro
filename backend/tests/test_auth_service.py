"""Tests for AuthService — magic link flow, session validation."""

from unittest.mock import AsyncMock, MagicMock

import pytest

from tests.conftest import make_mock_settings


@pytest.fixture
def mock_db():
    db = AsyncMock()
    db.add = MagicMock()
    db.flush = AsyncMock()
    db.commit = AsyncMock()
    return db


@pytest.fixture
def settings():
    return make_mock_settings(
        magic_link_ttl_minutes=15,
        session_ttl_hours=720,
    )


class TestSendMagicLink:
    async def test_creates_magic_link(self, settings, mock_db):
        from src.services.auth_service import AuthService

        auth = AuthService(settings, mock_db)
        token = await auth.send_magic_link("test@example.com")

        assert isinstance(token, str)
        assert len(token) > 20
        mock_db.add.assert_called_once()
        mock_db.commit.assert_called_once()


class TestVerifyMagicLink:
    async def test_rejects_invalid_token(self, settings, mock_db):
        from src.services.auth_service import AuthService

        # Mock no result found
        result_mock = MagicMock()
        result_mock.scalar_one_or_none.return_value = None
        mock_db.execute = AsyncMock(return_value=result_mock)

        auth = AuthService(settings, mock_db)
        with pytest.raises(ValueError, match="Invalid or expired"):
            await auth.verify_magic_link("bad-token")


class TestValidateSession:
    async def test_returns_none_for_invalid_token(self, settings, mock_db):
        from src.services.auth_service import AuthService

        result_mock = MagicMock()
        result_mock.scalar_one_or_none.return_value = None
        mock_db.execute = AsyncMock(return_value=result_mock)

        auth = AuthService(settings, mock_db)
        user = await auth.validate_session("bad-token")
        assert user is None


class TestHashToken:
    def test_deterministic(self):
        from src.services.auth_service import AuthService

        h1 = AuthService._hash_token("test")
        h2 = AuthService._hash_token("test")
        assert h1 == h2

    def test_different_inputs(self):
        from src.services.auth_service import AuthService

        h1 = AuthService._hash_token("token_a")
        h2 = AuthService._hash_token("token_b")
        assert h1 != h2
