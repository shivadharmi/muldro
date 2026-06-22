"""Tests for webhook registration wired into the OAuth integration-connect flow.

Covers ``_register_webhooks_for_sources`` (called best-effort after a Google
connect): it is a no-op when webhooks aren't configured, calls
``WebhookManager.register`` per source when configured, and never raises when
registration errors (connect must not fail).
"""

from contextlib import asynccontextmanager
from unittest.mock import AsyncMock, MagicMock, patch

from tests.conftest import TEST_USER_ID, TEST_WORKSPACE_ID, make_mock_settings


def _db_factory(db):
    @asynccontextmanager
    async def factory():
        yield db

    return factory


class TestRegisterWebhooksOnConnect:
    async def test_noop_when_not_configured(self):
        from src.api.routes_auth_oauth_integration import _register_webhooks_for_sources

        settings = make_mock_settings(webhooks_configured=False)
        db = AsyncMock()

        with (
            patch(
                "src.config.settings.get_settings",
                return_value=settings,
            ),
            patch("src.integrations.sync.webhook_manager.WebhookManager") as mgr_cls,
        ):
            await _register_webhooks_for_sources(
                _db_factory(db), TEST_USER_ID, ["gmail", "calendar"], TEST_WORKSPACE_ID
            )

        mgr_cls.assert_not_called()

    async def test_registers_each_source_when_configured(self):
        from src.api.routes_auth_oauth_integration import _register_webhooks_for_sources

        settings = make_mock_settings(
            webhooks_configured=True,
            webhook_callback_base_url="https://host",
            oauth_encryption_key="k",
        )
        db = AsyncMock()
        db.commit = AsyncMock()

        mock_mgr = AsyncMock()
        mock_sub = MagicMock()
        mock_sub.subscription_id = "whsub_x"
        mock_sub.status = "active"
        mock_mgr.register = AsyncMock(return_value=mock_sub)

        with (
            patch("src.config.settings.get_settings", return_value=settings),
            patch("src.services.oauth_manager.OAuthManager", MagicMock()),
            patch(
                "src.integrations.sync.webhook_manager.WebhookManager",
                return_value=mock_mgr,
            ),
        ):
            await _register_webhooks_for_sources(
                _db_factory(db), TEST_USER_ID, ["gmail", "calendar"], TEST_WORKSPACE_ID
            )

        assert mock_mgr.register.await_count == 2
        providers = {c.kwargs["provider"] for c in mock_mgr.register.await_args_list}
        assert providers == {"gmail", "calendar"}
        db.commit.assert_awaited_once()

    async def test_does_not_raise_when_registration_fails(self):
        from src.api.routes_auth_oauth_integration import _register_webhooks_for_sources

        settings = make_mock_settings(
            webhooks_configured=True,
            webhook_callback_base_url="https://host",
            oauth_encryption_key="k",
        )
        db = AsyncMock()
        db.commit = AsyncMock()

        mock_mgr = AsyncMock()
        mock_mgr.register = AsyncMock(side_effect=RuntimeError("provider down"))

        with (
            patch("src.config.settings.get_settings", return_value=settings),
            patch("src.services.oauth_manager.OAuthManager", MagicMock()),
            patch(
                "src.integrations.sync.webhook_manager.WebhookManager",
                return_value=mock_mgr,
            ),
        ):
            # Must NOT raise — connect resilience.
            await _register_webhooks_for_sources(
                _db_factory(db), TEST_USER_ID, ["gmail"], TEST_WORKSPACE_ID
            )

        assert mock_mgr.register.await_count == 1
