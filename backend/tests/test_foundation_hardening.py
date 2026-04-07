"""Tests for Spec 0: Foundation Hardening."""


class TestSettingsCleanup:
    """Fix 6.1: Remove unused settings + add environment field."""

    def test_environment_field_defaults_to_development(self):
        from src.config.settings import Settings

        s = Settings(
            anthropic_api_key="test",
            database_url="postgresql+asyncpg://x/x",
            redis_url="redis://localhost",
        )
        assert s.environment == "development"

    def test_environment_field_accepts_production(self):
        from src.config.settings import Settings

        s = Settings(
            anthropic_api_key="test",
            database_url="postgresql+asyncpg://x/x",
            redis_url="redis://localhost",
            environment="production",
        )
        assert s.environment == "production"

    def test_unused_twilio_fields_removed(self):
        from src.config.settings import Settings

        assert not hasattr(Settings, "twilio_account_sid")
        assert not hasattr(Settings, "twilio_auth_token")
        assert not hasattr(Settings, "twilio_from_number")

    def test_unused_whatsapp_fields_removed(self):
        from src.config.settings import Settings

        assert not hasattr(Settings, "whatsapp_phone_number_id")
        assert not hasattr(Settings, "whatsapp_access_token")
        assert not hasattr(Settings, "whatsapp_verify_token")
        assert not hasattr(Settings, "whatsapp_app_secret")

    def test_unused_session_secret_removed(self):
        from src.config.settings import Settings

        assert not hasattr(Settings, "session_secret_key")

    def test_unused_stale_observation_fields_removed(self):
        from src.config.settings import Settings

        assert not hasattr(Settings, "observation_stale_jira_minutes")
        assert not hasattr(Settings, "observation_stale_linkedin_minutes")
        assert not hasattr(Settings, "observation_stale_twitter_minutes")
