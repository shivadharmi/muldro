"""Tests for fail-fast secret validation and production secret enforcement.

Covers:
- Settings.validate_startup(): missing Anthropic credentials and missing OAuth
  encryption key in production must raise with a clear, actionable message.
- AuthService._encrypt_token(): in production, a missing/failed encryption key
  must raise rather than silently storing the token as plaintext (B7).
"""

import pytest

from src.config.settings import Settings

# --------------------------------------------------------------------------- #
# Settings.validate_startup
# --------------------------------------------------------------------------- #


def test_validate_startup_raises_when_anthropic_key_missing():
    s = Settings(
        anthropic_api_key="",
        use_bedrock=False,
        environment="development",
        oauth_encryption_key="",
    )
    with pytest.raises(RuntimeError) as exc:
        s.validate_startup()
    assert "JARVIS_ANTHROPIC_API_KEY" in str(exc.value)


def test_validate_startup_allows_bedrock_without_anthropic_key():
    s = Settings(
        anthropic_api_key="",
        use_bedrock=True,
        environment="development",
        oauth_encryption_key="",
    )
    # Bedrock uses AWS credentials, not an Anthropic API key — must not raise.
    s.validate_startup()


def test_validate_startup_raises_when_oauth_key_missing_in_production():
    s = Settings(
        anthropic_api_key="anthropic-key-present",
        use_bedrock=False,
        environment="production",
        oauth_encryption_key="",
    )
    with pytest.raises(RuntimeError) as exc:
        s.validate_startup()
    assert "JARVIS_OAUTH_ENCRYPTION_KEY" in str(exc.value)


def test_validate_startup_passes_in_production_with_all_secrets():
    s = Settings(
        anthropic_api_key="anthropic-key-present",
        use_bedrock=False,
        environment="production",
        oauth_encryption_key="oauth-key-present",
    )
    s.validate_startup()


def test_is_production_property():
    assert Settings(environment="production").is_production is True
    assert Settings(environment="development").is_production is False


# --------------------------------------------------------------------------- #
# AuthService._encrypt_token production enforcement (B7)
# --------------------------------------------------------------------------- #


def _auth_service(environment: str, oauth_encryption_key: str):
    from unittest.mock import AsyncMock

    from src.services.auth_service import AuthService
    from tests.conftest import make_mock_settings

    settings = make_mock_settings(
        environment=environment,
        oauth_encryption_key=oauth_encryption_key,
    )
    return AuthService(settings, AsyncMock())


def test_encrypt_token_raises_in_production_without_key():
    auth = _auth_service(environment="production", oauth_encryption_key="")
    with pytest.raises(RuntimeError) as exc:
        auth._encrypt_token("secret-access-token")
    assert "JARVIS_OAUTH_ENCRYPTION_KEY" in str(exc.value)


def test_encrypt_token_allows_plaintext_in_development_without_key():
    auth = _auth_service(environment="development", oauth_encryption_key="")
    # Dev ergonomics preserved: no key → stored as-is (with a warning).
    assert auth._encrypt_token("secret-access-token") == "secret-access-token"
