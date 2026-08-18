"""Tests for PushReceiver — verified wake-signal path."""

import hashlib
import hmac as hmac_mod
import time
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
    external_id: str | None = None,
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
    sub.external_id = external_id
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
        receiver = PushReceiver(db, TEST_WORKSPACE_ID, "https://api.muldro.test")
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
        receiver = PushReceiver(db, TEST_WORKSPACE_ID, "https://api.muldro.test")
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

        receiver = PushReceiver(db, TEST_WORKSPACE_ID, "https://api.muldro.test")
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

        receiver = PushReceiver(db, TEST_WORKSPACE_ID, "https://api.muldro.test")
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

        receiver = PushReceiver(db, TEST_WORKSPACE_ID, "https://api.muldro.test")
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
        db.execute = AsyncMock(return_value=sub_result)

        receiver = PushReceiver(db, TEST_WORKSPACE_ID, "https://api.muldro.test")
        # Patch record_failure to avoid real DB calls; also spy to ensure request_run is not called
        receiver._webhook_manager.record_failure = AsyncMock()

        with patch(_POLICY_PATCH) as mock_policy_cls:
            mock_policy_cls.return_value.request_run = AsyncMock()
            await receiver.handle_delivery(
                provider="github",
                subscription_id="whsub_123",
                payload={"action": "opened"},
                signature=bad_sig,
                raw_body=raw_body,
            )

        mock_policy_cls.return_value.request_run.assert_not_called()

    async def test_no_normalized_event_on_bad_signature(self):
        from src.integrations.sync.push_receiver import PushReceiver

        sub = _make_active_sub(secret="s3cr3t")

        sub_result = MagicMock()
        sub_result.scalar_one_or_none.return_value = sub
        db = AsyncMock()
        db.add = MagicMock()
        db.execute = AsyncMock(return_value=sub_result)

        receiver = PushReceiver(db, TEST_WORKSPACE_ID, "https://api.muldro.test")
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

            receiver = PushReceiver(db, TEST_WORKSPACE_ID, "https://api.muldro.test")
            receiver._webhook_manager.record_delivery = AsyncMock()
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
        receiver._webhook_manager.record_delivery.assert_awaited_once_with("whsub_123")

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

            receiver = PushReceiver(db, TEST_WORKSPACE_ID, "https://api.muldro.test")
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

            receiver = PushReceiver(db, TEST_WORKSPACE_ID, "https://api.muldro.test")
            result = await receiver.handle_delivery(
                provider="github",
                subscription_id="whsub_123",
                payload={"action": "opened"},
                signature=sig,
                raw_body=raw_body,
            )

        assert result.event_id is None

    async def test_unsigned_signed_provider_rejected(self):
        """Fail-CLOSED: a signed provider with NO secret stored must be rejected.

        Previously the receiver fail-OPEN'd (``if sub.secret and raw_body``),
        accepting any delivery when the secret was NULL. Now a provider that
        should be signed but has no secret on the subscription is rejected.
        """
        from src.integrations.sync.push_receiver import PushReceiver

        sub = _make_active_sub(provider="slack", secret=None)
        db = self._db_for_signed_delivery(sub)

        with patch(_POLICY_PATCH) as mock_policy_cls:
            mock_policy_cls.return_value.request_run = AsyncMock()

            receiver = PushReceiver(db, TEST_WORKSPACE_ID, "https://api.muldro.test")
            receiver._webhook_manager.record_failure = AsyncMock()
            result = await receiver.handle_delivery(
                provider="slack",
                subscription_id="whsub_123",
                payload={"event": {"type": "message", "text": "hello"}},
            )

        assert result.accepted is False
        assert result.error == "signature_mismatch"
        mock_policy_cls.return_value.request_run.assert_not_called()

    async def test_unsigned_github_rejected(self):
        """GitHub with no secret + no signature header → rejected (fail-closed)."""
        from src.integrations.sync.push_receiver import PushReceiver

        sub = _make_active_sub(provider="github", secret=None)
        db = self._db_for_signed_delivery(sub)

        with patch(_POLICY_PATCH) as mock_policy_cls:
            mock_policy_cls.return_value.request_run = AsyncMock()

            receiver = PushReceiver(db, TEST_WORKSPACE_ID, "https://api.muldro.test")
            receiver._webhook_manager.record_failure = AsyncMock()
            result = await receiver.handle_delivery(
                provider="github",
                subscription_id="whsub_123",
                payload={"action": "opened"},
                raw_body=b'{"action":"opened"}',
            )

        assert result.accepted is False
        assert result.error == "signature_mismatch"
        mock_policy_cls.return_value.request_run.assert_not_called()


class TestHandleDeliveryRequestRunFailure:
    """If request_run raises, return accepted=False and call record_failure.

    Verification must PASS first (fail-closed), so these use a validly-signed
    GitHub delivery and only the wake-signal step fails.
    """

    _SECRET = "ghsecret"
    _BODY = b'{"action":"opened"}'

    def _sig(self) -> str:
        return (
            "sha256=" + hmac_mod.new(self._SECRET.encode(), self._BODY, hashlib.sha256).hexdigest()
        )

    def _db_for_signed(self, sub):
        """db that handles: subscription lookup + record_failure update."""
        sub_result = MagicMock()
        sub_result.scalar_one_or_none.return_value = sub
        db = AsyncMock()
        db.add = MagicMock()
        db.execute = AsyncMock(side_effect=[sub_result, MagicMock()])
        return db

    async def test_returns_rejected_on_request_run_error(self):
        from src.integrations.sync.push_receiver import PushReceiver

        sub = _make_active_sub(provider="github", secret=self._SECRET)
        db = self._db_for_signed(sub)

        with patch(_POLICY_PATCH) as mock_policy_cls:
            mock_policy_cls.return_value.request_run = AsyncMock(
                side_effect=RuntimeError("db connection lost")
            )

            receiver = PushReceiver(db, TEST_WORKSPACE_ID, "https://api.muldro.test")
            receiver._webhook_manager.record_failure = AsyncMock()
            receiver._webhook_manager.record_delivery = AsyncMock()
            result = await receiver.handle_delivery(
                provider="github",
                subscription_id="whsub_123",
                payload={"action": "opened"},
                signature=self._sig(),
                raw_body=self._BODY,
            )

        assert result.accepted is False
        assert result.error == "wake_signal_failed"
        assert result.subscription_id == "whsub_123"
        # record_delivery must NOT be called when the wake signal fails
        receiver._webhook_manager.record_delivery.assert_not_awaited()

    async def test_calls_record_failure_on_request_run_error(self):
        from src.integrations.sync.push_receiver import PushReceiver

        sub = _make_active_sub(provider="github", secret=self._SECRET)
        db = self._db_for_signed(sub)

        with patch(_POLICY_PATCH) as mock_policy_cls:
            mock_policy_cls.return_value.request_run = AsyncMock(
                side_effect=RuntimeError("db connection lost")
            )

            receiver = PushReceiver(db, TEST_WORKSPACE_ID, "https://api.muldro.test")
            receiver._webhook_manager.record_failure = AsyncMock()

            await receiver.handle_delivery(
                provider="github",
                subscription_id="whsub_123",
                payload={"action": "opened"},
                signature=self._sig(),
                raw_body=self._BODY,
            )

        receiver._webhook_manager.record_failure.assert_awaited_once_with(
            "whsub_123", "wake_signal_failed"
        )

    async def test_no_normalized_event_on_request_run_failure(self):
        from src.integrations.sync.push_receiver import PushReceiver

        sub = _make_active_sub(provider="github", secret=self._SECRET)
        db = self._db_for_signed(sub)

        with patch(_POLICY_PATCH) as mock_policy_cls:
            mock_policy_cls.return_value.request_run = AsyncMock(
                side_effect=RuntimeError("db connection lost")
            )

            receiver = PushReceiver(db, TEST_WORKSPACE_ID, "https://api.muldro.test")
            receiver._webhook_manager.record_failure = AsyncMock()
            await receiver.handle_delivery(
                provider="github",
                subscription_id="whsub_123",
                payload={"action": "opened"},
                signature=self._sig(),
                raw_body=self._BODY,
            )

        db.add.assert_not_called()


# ---------------------------------------------------------------------------
# Slack signature scheme (flaw 3): v0:{ts}:{body} + 5-min replay window
# ---------------------------------------------------------------------------


def _slack_sig(secret: str, ts: str, body: bytes) -> str:
    base = f"v0:{ts}:".encode() + body
    return "v0=" + hmac_mod.new(secret.encode(), base, hashlib.sha256).hexdigest()


def _db_lookup_then_update(sub):
    sub_result = MagicMock()
    sub_result.scalar_one_or_none.return_value = sub
    db = AsyncMock()
    db.add = MagicMock()
    db.execute = AsyncMock(side_effect=[sub_result, MagicMock(), MagicMock()])
    return db


class TestSlackSignatureScheme:
    async def test_valid_slack_v0_signature_accepted(self):
        from src.integrations.sync.push_receiver import PushReceiver

        secret = "slack_signing_secret"
        body = b'{"type":"event_callback"}'
        ts = str(int(time.time()))
        sig = _slack_sig(secret, ts, body)

        sub = _make_active_sub(provider="slack", secret=secret)
        db = _db_lookup_then_update(sub)

        with patch(_POLICY_PATCH) as mock_policy_cls:
            mock_policy_cls.return_value.request_run = AsyncMock()
            receiver = PushReceiver(db, TEST_WORKSPACE_ID, "https://api.muldro.test")
            receiver._webhook_manager.record_delivery = AsyncMock()
            result = await receiver.handle_delivery(
                provider="slack",
                subscription_id="whsub_123",
                payload={"type": "event_callback"},
                signature=sig,
                raw_body=body,
                headers={"x-slack-request-timestamp": ts},
            )

        assert result.accepted is True
        mock_policy_cls.return_value.request_run.assert_awaited_once()

    async def test_raw_body_hmac_slack_rejected(self):
        """The OLD (wrong) scheme — raw-body HMAC — must now be rejected."""
        from src.integrations.sync.push_receiver import PushReceiver

        secret = "slack_signing_secret"
        body = b'{"type":"event_callback"}'
        ts = str(int(time.time()))
        # Old, incorrect signature: HMAC over the raw body only.
        wrong_sig = hmac_mod.new(secret.encode(), body, hashlib.sha256).hexdigest()

        sub = _make_active_sub(provider="slack", secret=secret)
        db = _db_lookup_then_update(sub)

        with patch(_POLICY_PATCH) as mock_policy_cls:
            mock_policy_cls.return_value.request_run = AsyncMock()
            receiver = PushReceiver(db, TEST_WORKSPACE_ID, "https://api.muldro.test")
            receiver._webhook_manager.record_failure = AsyncMock()
            result = await receiver.handle_delivery(
                provider="slack",
                subscription_id="whsub_123",
                payload={"type": "event_callback"},
                signature=wrong_sig,
                raw_body=body,
                headers={"x-slack-request-timestamp": ts},
            )

        assert result.accepted is False
        assert result.error == "signature_mismatch"

    async def test_stale_slack_timestamp_rejected(self):
        """A correctly-signed but stale (>5 min) request is a replay → rejected."""
        from src.integrations.sync.push_receiver import PushReceiver

        secret = "slack_signing_secret"
        body = b'{"type":"event_callback"}'
        stale_ts = str(int(time.time()) - 600)  # 10 minutes ago
        sig = _slack_sig(secret, stale_ts, body)

        sub = _make_active_sub(provider="slack", secret=secret)
        db = _db_lookup_then_update(sub)

        with patch(_POLICY_PATCH) as mock_policy_cls:
            mock_policy_cls.return_value.request_run = AsyncMock()
            receiver = PushReceiver(db, TEST_WORKSPACE_ID, "https://api.muldro.test")
            receiver._webhook_manager.record_failure = AsyncMock()
            result = await receiver.handle_delivery(
                provider="slack",
                subscription_id="whsub_123",
                payload={"type": "event_callback"},
                signature=sig,
                raw_body=body,
                headers={"x-slack-request-timestamp": stale_ts},
            )

        assert result.accepted is False
        assert result.error == "signature_mismatch"
        mock_policy_cls.return_value.request_run.assert_not_called()

    async def test_missing_slack_timestamp_rejected(self):
        from src.integrations.sync.push_receiver import PushReceiver

        secret = "slack_signing_secret"
        body = b'{"type":"event_callback"}'
        ts = str(int(time.time()))
        sig = _slack_sig(secret, ts, body)

        sub = _make_active_sub(provider="slack", secret=secret)
        db = _db_lookup_then_update(sub)

        with patch(_POLICY_PATCH) as mock_policy_cls:
            mock_policy_cls.return_value.request_run = AsyncMock()
            receiver = PushReceiver(db, TEST_WORKSPACE_ID, "https://api.muldro.test")
            receiver._webhook_manager.record_failure = AsyncMock()
            result = await receiver.handle_delivery(
                provider="slack",
                subscription_id="whsub_123",
                payload={"type": "event_callback"},
                signature=sig,
                raw_body=body,
                headers={},  # no timestamp
            )

        assert result.accepted is False
        assert result.error == "signature_mismatch"


# ---------------------------------------------------------------------------
# GitHub replay dedup (flaw 4): X-GitHub-Delivery seen-set
# ---------------------------------------------------------------------------


class _FakeRedis:
    """Minimal async Redis stand-in supporting set(nx=, ex=) + exists()."""

    def __init__(self):
        self._store: dict[str, str] = {}

    async def set(self, key, value, nx=False, ex=None):
        if nx and key in self._store:
            return None
        self._store[key] = value
        return True

    async def exists(self, key):
        return 1 if key in self._store else 0


class TestGitHubReplayDedup:
    def _signed(self, secret, body):
        return "sha256=" + hmac_mod.new(secret.encode(), body, hashlib.sha256).hexdigest()

    async def test_replayed_delivery_id_ignored(self):
        from src.integrations.sync.push_receiver import PushReceiver

        secret = "ghsecret"
        body = b'{"action":"opened"}'
        sig = self._signed(secret, body)
        delivery_id = "12345678-1234-1234-1234-123456789abc"
        redis = _FakeRedis()

        sub = _make_active_sub(provider="github", secret=secret)

        with patch(_POLICY_PATCH) as mock_policy_cls:
            mock_policy_cls.return_value.request_run = AsyncMock()

            # First delivery: accepted, wake signal fires.
            db1 = _db_lookup_then_update(sub)
            r1 = PushReceiver(db1, TEST_WORKSPACE_ID, "https://api.muldro.test", redis=redis)
            r1._webhook_manager.record_delivery = AsyncMock()
            res1 = await r1.handle_delivery(
                provider="github",
                subscription_id="whsub_123",
                payload={"action": "opened"},
                signature=sig,
                raw_body=body,
                headers={"x-github-delivery": delivery_id},
            )
            assert res1.accepted is True

            # Second delivery with the SAME delivery id: ignored as replay.
            db2 = _db_lookup_then_update(sub)
            r2 = PushReceiver(db2, TEST_WORKSPACE_ID, "https://api.muldro.test", redis=redis)
            r2._webhook_manager.record_delivery = AsyncMock()
            res2 = await r2.handle_delivery(
                provider="github",
                subscription_id="whsub_123",
                payload={"action": "opened"},
                signature=sig,
                raw_body=body,
                headers={"x-github-delivery": delivery_id},
            )

        assert res2.accepted is False
        assert res2.error == "duplicate_delivery"
        # request_run fired exactly once (only the first, non-replayed delivery)
        assert mock_policy_cls.return_value.request_run.await_count == 1


# ---------------------------------------------------------------------------
# Google push (flaw 6): token==secret, channel-id==external_id, sync ACK
# ---------------------------------------------------------------------------


class TestGooglePush:
    async def test_sync_handshake_acked_without_run(self):
        """X-Goog-Resource-State: sync is the watch handshake — ACK, no wake."""
        from src.integrations.sync.push_receiver import PushReceiver

        sub = _make_active_sub(provider="google", secret="chan_token", external_id="chan_id_1")
        db = _db_lookup_then_update(sub)

        with patch(_POLICY_PATCH) as mock_policy_cls:
            mock_policy_cls.return_value.request_run = AsyncMock()
            receiver = PushReceiver(db, TEST_WORKSPACE_ID, "https://api.muldro.test")
            receiver._webhook_manager.record_delivery = AsyncMock()
            result = await receiver.handle_delivery(
                provider="google",
                subscription_id="whsub_123",
                payload={},
                raw_body=b"",
                headers={
                    "x-goog-channel-id": "chan_id_1",
                    "x-goog-channel-token": "chan_token",
                    "x-goog-resource-state": "sync",
                    "x-goog-message-number": "1",
                },
            )

        assert result.accepted is True
        mock_policy_cls.return_value.request_run.assert_not_called()

    async def test_exists_triggers_run_with_valid_token(self):
        from src.integrations.sync.push_receiver import PushReceiver

        sub = _make_active_sub(provider="google", secret="chan_token", external_id="chan_id_1")
        db = _db_lookup_then_update(sub)

        with patch(_POLICY_PATCH) as mock_policy_cls:
            mock_policy_cls.return_value.request_run = AsyncMock()
            receiver = PushReceiver(db, TEST_WORKSPACE_ID, "https://api.muldro.test")
            receiver._webhook_manager.record_delivery = AsyncMock()
            result = await receiver.handle_delivery(
                provider="google",
                subscription_id="whsub_123",
                payload={},
                raw_body=b"",
                headers={
                    "x-goog-channel-id": "chan_id_1",
                    "x-goog-channel-token": "chan_token",
                    "x-goog-resource-state": "exists",
                    "x-goog-message-number": "2",
                },
            )

        assert result.accepted is True
        mock_policy_cls.return_value.request_run.assert_awaited_once()

    async def test_bad_channel_token_rejected(self):
        from src.integrations.sync.push_receiver import PushReceiver

        sub = _make_active_sub(provider="google", secret="chan_token", external_id="chan_id_1")
        db = _db_lookup_then_update(sub)

        with patch(_POLICY_PATCH) as mock_policy_cls:
            mock_policy_cls.return_value.request_run = AsyncMock()
            receiver = PushReceiver(db, TEST_WORKSPACE_ID, "https://api.muldro.test")
            receiver._webhook_manager.record_failure = AsyncMock()
            result = await receiver.handle_delivery(
                provider="google",
                subscription_id="whsub_123",
                payload={},
                raw_body=b"",
                headers={
                    "x-goog-channel-id": "chan_id_1",
                    "x-goog-channel-token": "WRONG_TOKEN",
                    "x-goog-resource-state": "exists",
                },
            )

        assert result.accepted is False
        assert result.error == "signature_mismatch"
        mock_policy_cls.return_value.request_run.assert_not_called()

    async def test_bad_channel_id_rejected(self):
        from src.integrations.sync.push_receiver import PushReceiver

        sub = _make_active_sub(provider="google", secret="chan_token", external_id="chan_id_1")
        db = _db_lookup_then_update(sub)

        with patch(_POLICY_PATCH) as mock_policy_cls:
            mock_policy_cls.return_value.request_run = AsyncMock()
            receiver = PushReceiver(db, TEST_WORKSPACE_ID, "https://api.muldro.test")
            receiver._webhook_manager.record_failure = AsyncMock()
            result = await receiver.handle_delivery(
                provider="google",
                subscription_id="whsub_123",
                payload={},
                raw_body=b"",
                headers={
                    "x-goog-channel-id": "WRONG_CHANNEL",
                    "x-goog-channel-token": "chan_token",
                    "x-goog-resource-state": "exists",
                },
            )

        assert result.accepted is False
        assert result.error == "signature_mismatch"
        mock_policy_cls.return_value.request_run.assert_not_called()


# ---------------------------------------------------------------------------
# Backpressure (flaw 2 fix): per-workspace event-stream lag guard
# ---------------------------------------------------------------------------


class _LagRedis:
    """Async Redis stand-in for get_stream_lag: xinfo_groups reports a fixed lag
    for any stream. Records which streams were queried."""

    def __init__(self, lag: int):
        self._lag = lag
        self.queried: list[str] = []

    async def xinfo_groups(self, stream):
        self.queried.append(stream)
        return [{"lag": self._lag}]

    async def set(self, key, value, nx=False, ex=None):
        return True


class TestBackpressure:
    """A verified delivery is dropped (→ route 429) only when the
    SUBSCRIPTION'S workspace event stream is backlogged above threshold."""

    _SECRET = "ghsecret"
    _BODY = b'{"action":"opened"}'

    def _sig(self) -> str:
        return (
            "sha256=" + hmac_mod.new(self._SECRET.encode(), self._BODY, hashlib.sha256).hexdigest()
        )

    async def test_over_threshold_drops_and_no_wake(self):
        from src.integrations.sync.push_receiver import PushReceiver

        sub = _make_active_sub(provider="github", secret=self._SECRET)
        db = _db_lookup_then_update(sub)
        redis = _LagRedis(lag=10_000)

        with patch(_POLICY_PATCH) as mock_policy_cls:
            mock_policy_cls.return_value.request_run = AsyncMock()
            receiver = PushReceiver(
                db,
                TEST_WORKSPACE_ID,
                "https://api.muldro.test",
                redis=redis,
                lag_threshold=5000,
            )
            receiver._webhook_manager.record_delivery = AsyncMock()
            result = await receiver.handle_delivery(
                provider="github",
                subscription_id="whsub_123",
                payload={"action": "opened"},
                signature=self._sig(),
                raw_body=self._BODY,
            )

        assert result.accepted is False
        assert result.error == "backpressure"
        # The lag check used the subscription's OWN workspace stream...
        assert f"muldro:events:{sub.workspace_id}" in redis.queried
        # ...and the wake-signal was NOT reached.
        mock_policy_cls.return_value.request_run.assert_not_called()

    async def test_under_threshold_proceeds(self):
        from src.integrations.sync.push_receiver import PushReceiver

        sub = _make_active_sub(provider="github", secret=self._SECRET)
        db = _db_lookup_then_update(sub)
        redis = _LagRedis(lag=10)

        with patch(_POLICY_PATCH) as mock_policy_cls:
            mock_policy_cls.return_value.request_run = AsyncMock()
            receiver = PushReceiver(
                db,
                TEST_WORKSPACE_ID,
                "https://api.muldro.test",
                redis=redis,
                lag_threshold=5000,
            )
            receiver._webhook_manager.record_delivery = AsyncMock()
            result = await receiver.handle_delivery(
                provider="github",
                subscription_id="whsub_123",
                payload={"action": "opened"},
                signature=self._sig(),
                raw_body=self._BODY,
            )

        assert result.accepted is True
        mock_policy_cls.return_value.request_run.assert_awaited_once()

    async def test_lag_check_error_fails_open(self):
        """A lag-computation error must NOT block a verified delivery."""
        from src.integrations.sync.push_receiver import PushReceiver

        sub = _make_active_sub(provider="github", secret=self._SECRET)
        db = _db_lookup_then_update(sub)

        class _BoomRedis:
            async def xinfo_groups(self, stream):
                raise RuntimeError("redis down")

            async def set(self, *a, **k):
                return True

        with patch(_POLICY_PATCH) as mock_policy_cls:
            mock_policy_cls.return_value.request_run = AsyncMock()
            receiver = PushReceiver(
                db,
                TEST_WORKSPACE_ID,
                "https://api.muldro.test",
                redis=_BoomRedis(),
                lag_threshold=5000,
            )
            receiver._webhook_manager.record_delivery = AsyncMock()
            result = await receiver.handle_delivery(
                provider="github",
                subscription_id="whsub_123",
                payload={"action": "opened"},
                signature=self._sig(),
                raw_body=self._BODY,
            )

        # get_stream_lag swallows the error and returns 0 → not backpressured.
        assert result.accepted is True
        mock_policy_cls.return_value.request_run.assert_awaited_once()
