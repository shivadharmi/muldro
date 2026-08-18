"""Tests for webhook subscription management and PushReceiver."""

from datetime import datetime, timedelta, timezone
from unittest.mock import AsyncMock, MagicMock, patch

from tests.conftest import TEST_USER_ID, TEST_WORKSPACE_ID, make_mock_settings


class TestWebhookManager:
    async def test_register_creates_subscription(self):
        from src.integrations.sync.webhook_manager import WebhookManager

        db = AsyncMock()
        db.add = MagicMock()
        db.flush = AsyncMock()

        mgr = WebhookManager(db, TEST_WORKSPACE_ID, "https://api.muldro.test")
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
        assert sub.callback_url.startswith("https://api.muldro.test/v1/webhooks/github/")
        assert sub.expires_at is not None
        db.add.assert_called_once()
        db.flush.assert_called_once()

    async def test_deactivate_sets_expired_status(self):
        from src.integrations.sync.webhook_manager import WebhookManager

        db = AsyncMock()
        db.execute = AsyncMock()

        mgr = WebhookManager(db, TEST_WORKSPACE_ID, "https://api.muldro.test")
        await mgr.deactivate("whsub_123")

        db.execute.assert_called_once()

    async def test_record_delivery_updates_counters(self):
        from src.integrations.sync.webhook_manager import WebhookManager

        db = AsyncMock()
        db.execute = AsyncMock()

        mgr = WebhookManager(db, TEST_WORKSPACE_ID, "https://api.muldro.test")
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

        mgr = WebhookManager(db, TEST_WORKSPACE_ID, "https://api.muldro.test")
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

        receiver = PushReceiver(db, TEST_WORKSPACE_ID, "https://api.muldro.test")
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

        receiver = PushReceiver(db, TEST_WORKSPACE_ID, "https://api.muldro.test")
        result = await receiver.handle_delivery(
            provider="github",
            subscription_id="whsub_123",
            payload={"action": "opened"},
        )

        assert result.accepted is False
        assert result.error == "subscription_expired"


class TestWebhooksConfigured:
    """The master gating property — drives the poll-only-by-default guarantee."""

    def test_disabled_by_default(self):
        from src.config.settings import Settings

        s = Settings(anthropic_api_key="x")
        assert s.webhooks_enabled is False
        assert s.webhook_callback_base_url == ""
        assert s.gmail_pubsub_topic == ""
        assert s.webhooks_configured is False

    def test_requires_both_switch_and_base_url(self):
        from src.config.settings import Settings

        # Switch on but no callback URL → still not configured.
        s = Settings(anthropic_api_key="x", webhooks_enabled=True)
        assert s.webhooks_configured is False

        # Base URL set but switch off → still not configured.
        s = Settings(
            anthropic_api_key="x",
            webhook_callback_base_url="https://host",
        )
        assert s.webhooks_configured is False

    def test_configured_when_both_set(self):
        from src.config.settings import Settings

        s = Settings(
            anthropic_api_key="x",
            webhooks_enabled=True,
            webhook_callback_base_url="https://host",
        )
        assert s.webhooks_configured is True


class TestWebhookRegisterGating:
    """register() must never call a provider when webhooks aren't configured."""

    async def test_register_noop_when_not_configured(self):
        """Default config: DB row written, NO provider API call, status active."""
        from src.integrations.sync.webhook_manager import WebhookManager

        db = AsyncMock()
        db.add = MagicMock()
        db.flush = AsyncMock()
        db.execute = AsyncMock(
            return_value=MagicMock(
                scalar_one_or_none=MagicMock(return_value=None),
                scalars=MagicMock(return_value=MagicMock(all=MagicMock(return_value=[]))),
            )
        )

        settings = make_mock_settings(webhooks_configured=False)
        oauth = AsyncMock()

        mgr = WebhookManager(
            db,
            TEST_WORKSPACE_ID,
            "https://api.muldro.test",
            settings=settings,
            oauth_manager=oauth,
        )
        sub = await mgr.register(
            user_id=TEST_USER_ID,
            provider="gmail",
            resource_type="mailbox",
            resource_id="me",
        )

        # No provider token fetch, no external channel.
        oauth.get_valid_token.assert_not_called()
        assert sub.external_id is None
        assert sub.status == "active"
        db.add.assert_called_once()

    async def test_register_noop_when_no_settings(self):
        """Legacy 3-arg construction stays DB-only (back-compat)."""
        from src.integrations.sync.webhook_manager import WebhookManager

        db = AsyncMock()
        db.add = MagicMock()
        db.flush = AsyncMock()
        db.execute = AsyncMock(
            return_value=MagicMock(
                scalar_one_or_none=MagicMock(return_value=None),
            )
        )

        mgr = WebhookManager(db, TEST_WORKSPACE_ID, "https://api.muldro.test")
        sub = await mgr.register(
            user_id=TEST_USER_ID,
            provider="github",
            resource_type="repository",
            resource_id="owner/repo",
        )
        assert sub.external_id is None
        assert sub.status == "active"


def _no_existing_sub_db():
    """AsyncMock DB whose idempotency lookup finds no existing subscription."""
    db = AsyncMock()
    db.add = MagicMock()
    db.flush = AsyncMock()
    empty = MagicMock()
    empty.scalar_one_or_none = MagicMock(return_value=None)
    db.execute = AsyncMock(return_value=empty)
    return db


class TestWebhookRegisterGmail:
    async def test_register_gmail_is_poll_only(self):
        """Gmail push is delivered via Pub/Sub (no X-Goog-Channel-* headers),
        which the header-based inbound _verify_google cannot accept. So gmail is
        deliberately NOT in _PUSH_PROVIDERS: register() must never create a watch
        and the source stays poll-only (no token fetch, no external channel).
        """
        from src.integrations.sync.webhook_manager import WebhookManager

        db = _no_existing_sub_db()
        settings = make_mock_settings(
            webhooks_configured=True,
            gmail_pubsub_topic="projects/p/topics/t",
        )
        oauth = AsyncMock()
        oauth.get_valid_token = AsyncMock(return_value="tok_abc")

        mgr = WebhookManager(
            db,
            TEST_WORKSPACE_ID,
            "https://api.muldro.test",
            settings=settings,
            oauth_manager=oauth,
        )
        sub = await mgr.register(
            user_id=TEST_USER_ID,
            provider="gmail",
            resource_type="mailbox",
            resource_id="me",
        )

        # No watch call → no token fetch, no external channel; poll-only row.
        oauth.get_valid_token.assert_not_called()
        assert sub.external_id is None
        assert sub.status == "active"

    async def test_gmail_watch_unit_still_works(self):
        """_gmail_watch itself is DEFERRED (unreachable from register until the
        Pub/Sub inbound path lands) but still works when called directly."""
        from src.integrations.sync import webhook_manager as wm_mod
        from src.integrations.sync.webhook_manager import WebhookManager

        settings = make_mock_settings(
            webhooks_configured=True,
            gmail_pubsub_topic="projects/p/topics/t",
        )
        oauth = AsyncMock()

        watch_resp = MagicMock()
        watch_resp.status_code = 200
        watch_resp.json = MagicMock(
            return_value={"historyId": "999", "expiration": "1700000000000"}
        )
        mock_client = AsyncMock()
        mock_client.post = AsyncMock(return_value=watch_resp)
        client_cm = MagicMock()
        client_cm.__aenter__ = AsyncMock(return_value=mock_client)
        client_cm.__aexit__ = AsyncMock(return_value=False)

        mgr = WebhookManager(
            AsyncMock(),
            TEST_WORKSPACE_ID,
            "https://api.muldro.test",
            settings=settings,
            oauth_manager=oauth,
        )
        with patch.object(wm_mod.httpx, "AsyncClient", MagicMock(return_value=client_cm)):
            channel = await mgr._gmail_watch("tok_abc", "whsub_chan", "tok_secret")

        mock_client.post.assert_called_once()
        url = mock_client.post.call_args[0][0]
        assert "gmail.googleapis.com" in url and "watch" in url
        body = mock_client.post.call_args.kwargs["json"]
        assert body["topicName"] == "projects/p/topics/t"
        assert channel.external_id == "whsub_chan"  # minted channel id
        assert channel.secret == "tok_secret"  # channel token
        assert channel.expires_at is not None


class TestWebhookRegisterCalendar:
    async def test_register_calls_calendar_watch(self):
        from src.integrations.sync import webhook_manager as wm_mod
        from src.integrations.sync.webhook_manager import WebhookManager

        db = _no_existing_sub_db()
        settings = make_mock_settings(webhooks_configured=True)
        oauth = AsyncMock()
        oauth.get_valid_token = AsyncMock(return_value="tok_abc")

        watch_resp = MagicMock()
        watch_resp.status_code = 200
        watch_resp.json = MagicMock(
            return_value={"resourceId": "res_xyz", "expiration": "1700000000000"}
        )
        mock_client = AsyncMock()
        mock_client.post = AsyncMock(return_value=watch_resp)
        client_cm = MagicMock()
        client_cm.__aenter__ = AsyncMock(return_value=mock_client)
        client_cm.__aexit__ = AsyncMock(return_value=False)

        with patch.object(wm_mod.httpx, "AsyncClient", MagicMock(return_value=client_cm)):
            mgr = WebhookManager(
                db,
                TEST_WORKSPACE_ID,
                "https://api.muldro.test",
                settings=settings,
                oauth_manager=oauth,
            )
            sub = await mgr.register(
                user_id=TEST_USER_ID,
                provider="calendar",
                resource_type="calendar",
                resource_id="primary",
            )

        mock_client.post.assert_called_once()
        url = mock_client.post.call_args[0][0]
        assert "calendar/v3" in url and "watch" in url
        body = mock_client.post.call_args.kwargs["json"]
        assert body["type"] == "web_hook"
        assert body["address"].startswith("https://api.muldro.test/v1/webhooks/calendar/")
        assert body["id"] == sub.subscription_id
        assert body["token"] == sub.secret
        # external_id stores the minted channel id (== subscription_id == watch
        # request `id`), which Google echoes back as X-Goog-Channel-Id. The
        # resourceId is NOT the channel id — it is preserved in config so
        # channels.stop can be called (needs both id + resourceId).
        assert sub.external_id == sub.subscription_id
        assert sub.config == {"resource_id": "res_xyz"}
        assert sub.expires_at is not None


class TestWebhookRegisterIdempotency:
    async def test_register_reuses_existing_active_sub(self):
        from src.integrations.sync.webhook_manager import WebhookManager
        from src.models.webhook_subscription import WebhookSubscription

        existing = MagicMock(spec=WebhookSubscription)
        existing.subscription_id = "whsub_existing"
        existing.status = "active"
        existing.external_id = "chan_existing"
        existing.expires_at = datetime.now(timezone.utc) + timedelta(days=5)

        db = AsyncMock()
        db.add = MagicMock()
        db.flush = AsyncMock()
        found = MagicMock()
        found.scalar_one_or_none = MagicMock(return_value=existing)
        db.execute = AsyncMock(return_value=found)

        settings = make_mock_settings(
            webhooks_configured=True, gmail_pubsub_topic="projects/p/topics/t"
        )
        oauth = AsyncMock()

        mgr = WebhookManager(
            db,
            TEST_WORKSPACE_ID,
            "https://api.muldro.test",
            settings=settings,
            oauth_manager=oauth,
        )
        sub = await mgr.register(
            user_id=TEST_USER_ID,
            provider="gmail",
            resource_type="mailbox",
            resource_id="me",
        )

        # Reused — no new row, no provider call.
        assert sub is existing
        db.add.assert_not_called()
        oauth.get_valid_token.assert_not_called()


class TestWebhookRenew:
    async def test_renew_recalls_provider_and_updates(self):
        from src.integrations.sync import webhook_manager as wm_mod
        from src.integrations.sync.webhook_manager import WebhookManager
        from src.models.webhook_subscription import WebhookSubscription

        sub = MagicMock(spec=WebhookSubscription)
        sub.subscription_id = "whsub_cal"
        sub.provider = "calendar"
        sub.resource_type = "calendar"
        sub.resource_id = "primary"
        sub.user_id = TEST_USER_ID
        sub.status = "active"
        sub.external_id = "old_chan"
        sub.secret = "old_secret"
        sub.config = {"resource_id": "old_res"}
        sub.expires_at = datetime.now(timezone.utc) + timedelta(hours=1)

        db = AsyncMock()
        found = MagicMock()
        found.scalar_one_or_none = MagicMock(return_value=sub)
        db.execute = AsyncMock(return_value=found)

        settings = make_mock_settings(webhooks_configured=True)
        oauth = AsyncMock()
        oauth.get_valid_token = AsyncMock(return_value="tok_abc")

        watch_resp = MagicMock()
        watch_resp.status_code = 200
        watch_resp.json = MagicMock(
            return_value={"resourceId": "new_res", "expiration": "1700000000000"}
        )
        mock_client = AsyncMock()
        mock_client.post = AsyncMock(return_value=watch_resp)
        client_cm = MagicMock()
        client_cm.__aenter__ = AsyncMock(return_value=mock_client)
        client_cm.__aexit__ = AsyncMock(return_value=False)

        with patch.object(wm_mod.httpx, "AsyncClient", MagicMock(return_value=client_cm)):
            mgr = WebhookManager(
                db,
                TEST_WORKSPACE_ID,
                "https://api.muldro.test",
                settings=settings,
                oauth_manager=oauth,
            )
            await mgr.renew("whsub_cal")

        mock_client.post.assert_called_once()
        # external_id is the freshly-minted channel id (NOT the resourceId), so
        # X-Goog-Channel-Id verification will match. The new resourceId lands in
        # config for channels.stop.
        assert sub.external_id != "old_chan"
        assert sub.external_id.startswith("whsub_")
        assert sub.config == {"resource_id": "new_res"}
        assert sub.status == "active"

    async def test_renew_db_only_when_not_configured(self):
        """Without provider config, renew just bumps expiry (legacy behavior)."""
        from src.integrations.sync.webhook_manager import WebhookManager

        db = AsyncMock()
        db.execute = AsyncMock()

        mgr = WebhookManager(db, TEST_WORKSPACE_ID, "https://api.muldro.test")
        await mgr.renew("whsub_123")
        db.execute.assert_called_once()


class TestWebhookRegisterVerifyContract:
    """Cross-boundary contract: what register() persists for a Calendar watch
    must be what the inbound PushReceiver._verify_google accepts.

    This locks the data contract across both halves of the subsystem. It would
    FAIL if external_id stored resourceId (the old bug) instead of the channel
    id that Google echoes as X-Goog-Channel-Id.
    """

    async def test_calendar_register_then_verify_roundtrip(self):
        from src.integrations.sync import webhook_manager as wm_mod
        from src.integrations.sync.push_receiver import PushReceiver
        from src.integrations.sync.webhook_manager import WebhookManager

        db = _no_existing_sub_db()
        settings = make_mock_settings(webhooks_configured=True)
        oauth = AsyncMock()
        oauth.get_valid_token = AsyncMock(return_value="tok_abc")

        watch_resp = MagicMock()
        watch_resp.status_code = 200
        watch_resp.json = MagicMock(
            return_value={"resourceId": "res_xyz", "expiration": "1700000000000"}
        )
        mock_client = AsyncMock()
        mock_client.post = AsyncMock(return_value=watch_resp)
        client_cm = MagicMock()
        client_cm.__aenter__ = AsyncMock(return_value=mock_client)
        client_cm.__aexit__ = AsyncMock(return_value=False)

        with patch.object(wm_mod.httpx, "AsyncClient", MagicMock(return_value=client_cm)):
            mgr = WebhookManager(
                db,
                TEST_WORKSPACE_ID,
                "https://api.muldro.test",
                settings=settings,
                oauth_manager=oauth,
            )
            sub = await mgr.register(
                user_id=TEST_USER_ID,
                provider="calendar",
                resource_type="calendar",
                resource_id="primary",
            )

        # Google echoes the watch request `id` (== our minted channel id ==
        # subscription_id) in X-Goog-Channel-Id, and the `token` in
        # X-Goog-Channel-Token. We feed the ACTUAL header value Google sends
        # (the channel id), NOT sub.external_id, so the test fails if register
        # stored anything other than the channel id in external_id (the old bug
        # stored resourceId → header would never match).
        headers = {
            "x-goog-channel-id": sub.subscription_id,
            "x-goog-channel-token": sub.secret,
        }
        assert PushReceiver._verify_google(sub, headers) is True
