"""Tests for the scheduler webhook-renewal tick.

The tick periodically finds active push subscriptions inside the renewal buffer
and re-watches them. It is a no-op when webhooks aren't configured (poll-only
default) and renews only the about-to-expire ones.
"""

from contextlib import asynccontextmanager
from datetime import datetime, timedelta, timezone
from unittest.mock import AsyncMock, MagicMock, patch

from tests.conftest import TEST_USER_ID, TEST_WORKSPACE_ID, make_mock_settings


def _factory(db):
    @asynccontextmanager
    async def factory():
        yield db

    return factory


def _make_tick(settings):
    from src.services.scheduler.webhook_renewal_tick import WebhookRenewalTickMixin

    tick = WebhookRenewalTickMixin()
    tick._settings = settings
    tick._user_ids = [TEST_USER_ID]
    return tick


class TestWebhookRenewalTick:
    async def test_noop_when_not_configured(self):
        settings = make_mock_settings(webhooks_configured=False)
        tick = _make_tick(settings)

        db = AsyncMock()
        # Must not even query.
        await tick._tick_webhook_renewal(_factory(db))
        db.execute.assert_not_called()

    async def test_renews_expiring_subscription(self):
        settings = make_mock_settings(
            webhooks_configured=True,
            webhook_callback_base_url="https://host",
            oauth_encryption_key="k",
        )
        tick = _make_tick(settings)

        expiring = MagicMock()
        expiring.subscription_id = "whsub_cal"
        expiring.workspace_id = TEST_WORKSPACE_ID
        expiring.expires_at = datetime.now(timezone.utc) + timedelta(hours=1)

        result = MagicMock()
        result.scalars = MagicMock(return_value=MagicMock(all=MagicMock(return_value=[expiring])))
        db = AsyncMock()
        db.execute = AsyncMock(return_value=result)
        db.commit = AsyncMock()

        mock_mgr = AsyncMock()
        mock_mgr.renew = AsyncMock()

        with (
            patch("src.services.oauth_manager.OAuthManager", MagicMock()),
            patch(
                "src.integrations.sync.webhook_manager.WebhookManager",
                return_value=mock_mgr,
            ),
        ):
            await tick._tick_webhook_renewal(_factory(db))

        mock_mgr.renew.assert_awaited_once_with("whsub_cal")
        db.commit.assert_awaited()

    async def test_skips_when_no_expiring(self):
        settings = make_mock_settings(
            webhooks_configured=True,
            webhook_callback_base_url="https://host",
            oauth_encryption_key="k",
        )
        tick = _make_tick(settings)

        result = MagicMock()
        result.scalars = MagicMock(return_value=MagicMock(all=MagicMock(return_value=[])))
        db = AsyncMock()
        db.execute = AsyncMock(return_value=result)
        db.commit = AsyncMock()

        with (
            patch("src.services.oauth_manager.OAuthManager", MagicMock()),
            patch("src.integrations.sync.webhook_manager.WebhookManager") as mgr_cls,
        ):
            await tick._tick_webhook_renewal(_factory(db))

        mgr_cls.assert_not_called()

    async def test_renew_failure_does_not_crash_tick(self):
        settings = make_mock_settings(
            webhooks_configured=True,
            webhook_callback_base_url="https://host",
            oauth_encryption_key="k",
        )
        tick = _make_tick(settings)

        expiring = MagicMock()
        expiring.subscription_id = "whsub_cal"
        expiring.workspace_id = TEST_WORKSPACE_ID

        result = MagicMock()
        result.scalars = MagicMock(return_value=MagicMock(all=MagicMock(return_value=[expiring])))
        db = AsyncMock()
        db.execute = AsyncMock(return_value=result)
        db.commit = AsyncMock()

        mock_mgr = AsyncMock()
        mock_mgr.renew = AsyncMock(side_effect=RuntimeError("boom"))

        with (
            patch("src.services.oauth_manager.OAuthManager", MagicMock()),
            patch(
                "src.integrations.sync.webhook_manager.WebhookManager",
                return_value=mock_mgr,
            ),
        ):
            # Must not raise.
            await tick._tick_webhook_renewal(_factory(db))
