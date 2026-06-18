"""Tests for PushReceiver — verified wake-signal path."""

from unittest.mock import AsyncMock, MagicMock, patch

from tests.conftest import TEST_USER_ID, TEST_WORKSPACE_ID

# The PerceptionPolicyService is imported lazily inside handle_delivery, so we
# patch the class at its definition site.
_POLICY_PATCH = "src.services.perception_policy.PerceptionPolicyService"


def _make_active_sub(
    subscription_id: str = "whsub_123",
    provider: str = "github",
    secret: str | None = None,
    workspace_id: str = TEST_WORKSPACE_ID,
    user_id: str = TEST_USER_ID,
):
    """Build a mock active WebhookSubscription."""
    from src.models.webhook_subscription import WebhookSubscription

    sub = MagicMock(spec=WebhookSubscription)
    sub.subscription_id = subscription_id
    sub.provider = provider
    sub.status = "active"
    sub.secret = secret
    sub.workspace_id = workspace_id
    sub.user_id = user_id
    sub.resource_type = "repository"
    sub.resource_id = "owner/repo"
    return sub


def _make_db_for_sub(sub_return):
    """Build an AsyncMock db whose first execute() returns the subscription."""
    mock_result = MagicMock()
    mock_result.scalar_one_or_none.return_value = sub_return

    db = AsyncMock()
    db.add = MagicMock()
    # Default: every execute returns the subscription result; override per test when needed.
    db.execute = AsyncMock(return_value=mock_result)
    return db


def _make_record_failure_mock():
    """Return a mock suitable for record_failure calls (a coroutine)."""
    return AsyncMock()


class TestHandleDeliveryUnknownSubscription:
    """Unknown subscription → rejected immediately, no side effects."""

    async def test_returns_rejected_with_error(self):
        from src.integrations.sync.push_receiver import PushReceiver

        db = _make_db_for_sub(None)
        receiver = PushReceiver(db, TEST_WORKSPACE_ID, "https://api.jarvis.test")
        result = await receiver.handle_delivery(
            provider="github",
            subscription_id="whsub_nonexistent",
            payload={"action": "opened"},
        )

        assert result.accepted is False
        assert result.error == "unknown_subscription"
        assert result.event_id is None

    async def test_no_normalized_event_created(self):
        from src.integrations.sync.push_receiver import PushReceiver

        db = _make_db_for_sub(None)
        receiver = PushReceiver(db, TEST_WORKSPACE_ID, "https://api.jarvis.test")
        await receiver.handle_delivery(
            provider="github",
            subscription_id="whsub_nonexistent",
            payload={"action": "opened"},
        )

        # db.add is never called — no NormalizedEvent rows
        db.add.assert_not_called()


class TestHandleDeliveryInactiveSubscription:
    """Inactive/expired subscription → rejected, no wake signal."""

    async def test_expired_subscription_rejected(self):
        from src.integrations.sync.push_receiver import PushReceiver

        sub = _make_active_sub()
        sub.status = "expired"
        db = _make_db_for_sub(sub)

        receiver = PushReceiver(db, TEST_WORKSPACE_ID, "https://api.jarvis.test")
        result = await receiver.handle_delivery(
            provider="github",
            subscription_id="whsub_123",
            payload={"action": "opened"},
        )

        assert result.accepted is False
        assert result.error == "subscription_expired"
        assert result.event_id is None

    async def test_no_normalized_event_on_inactive(self):
        from src.integrations.sync.push_receiver import PushReceiver

        sub = _make_active_sub()
        sub.status = "failed"
        db = _make_db_for_sub(sub)

        receiver = PushReceiver(db, TEST_WORKSPACE_ID, "https://api.jarvis.test")
        await receiver.handle_delivery(
            provider="github",
            subscription_id="whsub_123",
            payload={"action": "opened"},
        )

        db.add.assert_not_called()


class TestHandleDeliverySignatureMismatch:
    """Invalid HMAC signature → rejected, no wake signal, no event."""

    async def test_bad_signature_returns_rejected(self):
        from src.integrations.sync.push_receiver import PushReceiver

        secret = "s3cr3t"
        raw_body = b'{"action":"opened"}'
        bad_sig = "sha256=0000000000000000000000000000000000000000000000000000000000000000"

        sub = _make_active_sub(secret=secret)

        sub_result = MagicMock()
        sub_result.scalar_one_or_none.return_value = sub
        db = AsyncMock()
        db.add = MagicMock()
        db.execute = AsyncMock(return_value=sub_result)

        receiver = PushReceiver(db, TEST_WORKSPACE_ID, "https://api.jarvis.test")
        # Patch record_failure so WebhookManager doesn't do a real DB re-fetch
        receiver._webhook_manager.record_failure = AsyncMock()

        result = await receiver.handle_delivery(
            provider="github",
            subscription_id="whsub_123",
            payload={"action": "opened"},
            signature=bad_sig,
            raw_body=raw_body,
        )

        assert result.accepted is False
        assert result.error == "signature_mismatch"
        assert result.event_id is None

    async def test_no_wake_signal_on_bad_signature(self):
        """PerceptionPolicyService.request_run must NOT be called on signature mismatch."""
        from src.integrations.sync.push_receiver import PushReceiver

        secret = "s3cr3t"
        raw_body = b'{"action":"opened"}'
        bad_sig = "sha256=0000000000000000000000000000000000000000000000000000000000000000"

        sub = _make_active_sub(secret=secret)

        sub_result = MagicMock()
        sub_result.scalar_one_or_none.return_value = sub
        db = AsyncMock()
        db.add = MagicMock()
        db.execute = AsyncMock(side_effect=[sub_result, MagicMock()])

        mock_request_run = AsyncMock()
        receiver = PushReceiver(db, TEST_WORKSPACE_ID, "https://api.jarvis.test")
        # Patch record_failure to avoid real DB calls; also spy to ensure request_run is not called
        receiver._webhook_manager.record_failure = AsyncMock()

        with patch(_POLICY_PATCH) as mock_policy_cls:
            mock_policy_cls.return_value.request_run = mock_request_run
            await receiver.handle_delivery(
                provider="github",
                subscription_id="whsub_123",
                payload={"action": "opened"},
                signature=bad_sig,
                raw_body=raw_body,
            )

        mock_request_run.assert_not_called()

    async def test_no_normalized_event_on_bad_signature(self):
        from src.integrations.sync.push_receiver import PushReceiver

        sub = _make_active_sub(secret="s3cr3t")

        sub_result = MagicMock()
        sub_result.scalar_one_or_none.return_value = sub
        db = AsyncMock()
        db.add = MagicMock()
        db.execute = AsyncMock(side_effect=[sub_result, MagicMock()])

        receiver = PushReceiver(db, TEST_WORKSPACE_ID, "https://api.jarvis.test")
        receiver._webhook_manager.record_failure = AsyncMock()
        await receiver.handle_delivery(
            provider="github",
            subscription_id="whsub_123",
            payload={"action": "opened"},
            signature="sha256=bad",
            raw_body=b'{"action":"opened"}',
        )

        db.add.assert_not_called()


class TestHandleDeliveryValidSignedWebhook:
    """Valid signed webhook → wake signal set, no NormalizedEvent created."""

    def _valid_sig(self, secret: str, body: bytes, provider: str = "github") -> str:
        import hashlib
        import hmac as hmac_mod

        digest = hmac_mod.new(secret.encode(), body, hashlib.sha256).hexdigest()
        if provider == "github":
            return f"sha256={digest}"
        return digest

    def _db_for_signed_delivery(self, sub):
        """db that handles: subscription lookup + record_delivery update."""
        sub_result = MagicMock()
        sub_result.scalar_one_or_none.return_value = sub
        db = AsyncMock()
        db.add = MagicMock()
        db.execute = AsyncMock(side_effect=[sub_result, MagicMock()])
        return db

    async def test_sets_pending_run_via_request_run(self):
        """Valid delivery triggers request_run (sets pending_run=True on PerceptionState)."""
        from src.integrations.sync.push_receiver import PushReceiver

        secret = "webhook_secret"
        raw_body = b'{"action":"opened"}'
        sig = self._valid_sig(secret, raw_body, "github")

        sub = _make_active_sub(provider="github", secret=secret)
        db = self._db_for_signed_delivery(sub)

        mock_state = MagicMock()
        mock_state.pending_run = True

        with patch(_POLICY_PATCH) as mock_policy_cls:
            mock_policy = mock_policy_cls.return_value
            mock_policy.request_run = AsyncMock(return_value=mock_state)

            receiver = PushReceiver(db, TEST_WORKSPACE_ID, "https://api.jarvis.test")
            result = await receiver.handle_delivery(
                provider="github",
                subscription_id="whsub_123",
                payload={"action": "opened"},
                signature=sig,
                raw_body=raw_body,
            )

        assert result.accepted is True
        assert result.error is None
        mock_policy.request_run.assert_awaited_once_with(
            workspace_id=sub.workspace_id,
            user_id=sub.user_id,
            source=sub.provider,
            signal_source="webhook",
        )

    async def test_creates_zero_normalized_event_rows(self):
        """Webhook delivery must not insert any NormalizedEvent."""
        from src.integrations.sync.push_receiver import PushReceiver

        secret = "webhook_secret"
        raw_body = b'{"action":"opened"}'
        sig = self._valid_sig(secret, raw_body, "github")

        sub = _make_active_sub(provider="github", secret=secret)
        db = self._db_for_signed_delivery(sub)

        with patch(_POLICY_PATCH) as mock_policy_cls:
            mock_policy_cls.return_value.request_run = AsyncMock()

            receiver = PushReceiver(db, TEST_WORKSPACE_ID, "https://api.jarvis.test")
            await receiver.handle_delivery(
                provider="github",
                subscription_id="whsub_123",
                payload={"action": "opened"},
                signature=sig,
                raw_body=raw_body,
            )

        # db.add must never be called — no NormalizedEvent rows
        db.add.assert_not_called()

    async def test_event_id_is_none(self):
        """DeliveryResult.event_id is None (events come via scheduler funnel)."""
        from src.integrations.sync.push_receiver import PushReceiver

        secret = "webhook_secret"
        raw_body = b'{"action":"opened"}'
        sig = self._valid_sig(secret, raw_body, "github")

        sub = _make_active_sub(provider="github", secret=secret)
        db = self._db_for_signed_delivery(sub)

        with patch(_POLICY_PATCH) as mock_policy_cls:
            mock_policy_cls.return_value.request_run = AsyncMock()

            receiver = PushReceiver(db, TEST_WORKSPACE_ID, "https://api.jarvis.test")
            result = await receiver.handle_delivery(
                provider="github",
                subscription_id="whsub_123",
                payload={"action": "opened"},
                signature=sig,
                raw_body=raw_body,
            )

        assert result.event_id is None

    async def test_valid_unsigned_webhook_accepted(self):
        """Subscription without secret: skip signature check, accept delivery."""
        from src.integrations.sync.push_receiver import PushReceiver

        sub = _make_active_sub(provider="slack", secret=None)
        db = self._db_for_signed_delivery(sub)

        with patch(_POLICY_PATCH) as mock_policy_cls:
            mock_policy_cls.return_value.request_run = AsyncMock()

            receiver = PushReceiver(db, TEST_WORKSPACE_ID, "https://api.jarvis.test")
            result = await receiver.handle_delivery(
                provider="slack",
                subscription_id="whsub_123",
                payload={"event": {"type": "message", "text": "hello"}},
            )

        assert result.accepted is True
        assert result.event_id is None
        mock_policy_cls.return_value.request_run.assert_awaited_once()


class TestHandleDeliveryRequestRunFailure:
    """If request_run raises, return accepted=False and call record_failure."""

    def _db_for_unsigned(self, sub):
        """db that handles: subscription lookup + record_failure update."""
        sub_result = MagicMock()
        sub_result.scalar_one_or_none.return_value = sub
        db = AsyncMock()
        db.add = MagicMock()
        db.execute = AsyncMock(side_effect=[sub_result, MagicMock()])
        return db

    async def test_returns_rejected_on_request_run_error(self):
        from src.integrations.sync.push_receiver import PushReceiver

        sub = _make_active_sub(provider="github", secret=None)
        db = self._db_for_unsigned(sub)

        with patch(_POLICY_PATCH) as mock_policy_cls:
            mock_policy_cls.return_value.request_run = AsyncMock(
                side_effect=RuntimeError("db connection lost")
            )

            receiver = PushReceiver(db, TEST_WORKSPACE_ID, "https://api.jarvis.test")
            receiver._webhook_manager.record_failure = AsyncMock()
            result = await receiver.handle_delivery(
                provider="github",
                subscription_id="whsub_123",
                payload={"action": "opened"},
            )

        assert result.accepted is False
        assert result.error == "wake_signal_failed"
        assert result.subscription_id == "whsub_123"

    async def test_calls_record_failure_on_request_run_error(self):
        from src.integrations.sync.push_receiver import PushReceiver

        sub = _make_active_sub(provider="github", secret=None)
        db = self._db_for_unsigned(sub)

        with patch(_POLICY_PATCH) as mock_policy_cls:
            mock_policy_cls.return_value.request_run = AsyncMock(
                side_effect=RuntimeError("db connection lost")
            )

            receiver = PushReceiver(db, TEST_WORKSPACE_ID, "https://api.jarvis.test")
            receiver._webhook_manager.record_failure = AsyncMock()

            await receiver.handle_delivery(
                provider="github",
                subscription_id="whsub_123",
                payload={"action": "opened"},
            )

        receiver._webhook_manager.record_failure.assert_awaited_once_with(
            "whsub_123", "wake_signal_failed"
        )

    async def test_no_normalized_event_on_request_run_failure(self):
        from src.integrations.sync.push_receiver import PushReceiver

        sub = _make_active_sub(provider="github", secret=None)
        db = self._db_for_unsigned(sub)

        with patch(_POLICY_PATCH) as mock_policy_cls:
            mock_policy_cls.return_value.request_run = AsyncMock(
                side_effect=RuntimeError("db connection lost")
            )

            receiver = PushReceiver(db, TEST_WORKSPACE_ID, "https://api.jarvis.test")
            receiver._webhook_manager.record_failure = AsyncMock()
            await receiver.handle_delivery(
                provider="github",
                subscription_id="whsub_123",
                payload={"action": "opened"},
            )

        db.add.assert_not_called()
