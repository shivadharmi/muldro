"""Tests for webhook subscription management and PushReceiver."""

from unittest.mock import AsyncMock, MagicMock

from tests.conftest import TEST_USER_ID, TEST_WORKSPACE_ID


class TestWebhookManager:
    async def test_register_creates_subscription(self):
        from src.integrations.sync.webhook_manager import WebhookManager

        db = AsyncMock()
        db.add = MagicMock()
        db.flush = AsyncMock()

        mgr = WebhookManager(db, TEST_WORKSPACE_ID, "https://api.jarvis.test")
        sub = await mgr.register(
            user_id=TEST_USER_ID,
            provider="github",
            resource_type="repository",
            resource_id="owner/repo",
        )

        assert sub.subscription_id.startswith("whsub_")
        assert sub.provider == "github"
        assert sub.resource_type == "repository"
        assert sub.resource_id == "owner/repo"
        assert sub.status == "active"
        assert sub.secret is not None
        assert sub.callback_url.startswith("https://api.jarvis.test/v1/webhooks/github/")
        assert sub.expires_at is not None
        db.add.assert_called_once()
        db.flush.assert_called_once()

    async def test_deactivate_sets_expired_status(self):
        from src.integrations.sync.webhook_manager import WebhookManager

        db = AsyncMock()
        db.execute = AsyncMock()

        mgr = WebhookManager(db, TEST_WORKSPACE_ID, "https://api.jarvis.test")
        await mgr.deactivate("whsub_123")

        db.execute.assert_called_once()

    async def test_record_delivery_updates_counters(self):
        from src.integrations.sync.webhook_manager import WebhookManager

        db = AsyncMock()
        db.execute = AsyncMock()

        mgr = WebhookManager(db, TEST_WORKSPACE_ID, "https://api.jarvis.test")
        await mgr.record_delivery("whsub_123")

        db.execute.assert_called_once()

    async def test_record_failure_auto_pauses_after_max(self):
        from src.integrations.sync.webhook_manager import MAX_CONSECUTIVE_FAILURES, WebhookManager
        from src.models.webhook_subscription import WebhookSubscription

        mock_sub = MagicMock(spec=WebhookSubscription)
        mock_sub.consecutive_failures = MAX_CONSECUTIVE_FAILURES - 1
        mock_sub.status = "active"

        mock_result = MagicMock()
        mock_result.scalar_one_or_none.return_value = mock_sub

        db = AsyncMock()
        db.execute = AsyncMock(return_value=mock_result)

        mgr = WebhookManager(db, TEST_WORKSPACE_ID, "https://api.jarvis.test")
        await mgr.record_failure("whsub_123", "connection_refused")

        assert mock_sub.status == "failed"
        assert mock_sub.consecutive_failures == MAX_CONSECUTIVE_FAILURES


class TestPushReceiver:
    async def test_handle_delivery_unknown_subscription(self):
        from src.integrations.sync.push_receiver import PushReceiver

        mock_result = MagicMock()
        mock_result.scalar_one_or_none.return_value = None

        db = AsyncMock()
        db.execute = AsyncMock(return_value=mock_result)

        receiver = PushReceiver(db, TEST_WORKSPACE_ID, "https://api.jarvis.test")
        result = await receiver.handle_delivery(
            provider="github",
            subscription_id="whsub_nonexistent",
            payload={"action": "opened"},
        )

        assert result.accepted is False
        assert result.error == "unknown_subscription"

    async def test_handle_delivery_inactive_subscription(self):
        from src.integrations.sync.push_receiver import PushReceiver
        from src.models.webhook_subscription import WebhookSubscription

        mock_sub = MagicMock(spec=WebhookSubscription)
        mock_sub.subscription_id = "whsub_123"
        mock_sub.status = "expired"
        mock_sub.provider = "github"

        mock_result = MagicMock()
        mock_result.scalar_one_or_none.return_value = mock_sub

        db = AsyncMock()
        db.execute = AsyncMock(return_value=mock_result)

        receiver = PushReceiver(db, TEST_WORKSPACE_ID, "https://api.jarvis.test")
        result = await receiver.handle_delivery(
            provider="github",
            subscription_id="whsub_123",
            payload={"action": "opened"},
        )

        assert result.accepted is False
        assert result.error == "subscription_expired"
