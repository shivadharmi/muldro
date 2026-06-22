"""Tests for provider webhook ingress → perception wake-signal.

The provider callback endpoint (`/v1/webhooks/{provider}/{subscription_id}`) is
the URL WebhookManager registers with external providers. A delivery there must
be handed to PushReceiver, which verifies + sets the perception wake-signal.
"""

from unittest.mock import AsyncMock, MagicMock, patch

from fastapi.testclient import TestClient

from src.api.app import app
from src.api.deps import get_session
from src.integrations.sync.push_receiver import DeliveryResult

client = TestClient(app)


def _override_session():
    mock_db = AsyncMock()
    mock_db.commit = AsyncMock()
    app.dependency_overrides[get_session] = lambda: mock_db
    return mock_db


def _clear_session():
    app.dependency_overrides.pop(get_session, None)


def test_provider_webhook_invokes_push_receiver():
    """A delivery to the provider callback route reaches PushReceiver and
    returns accepted, committing the wake-signal."""
    _override_session()
    try:
        with patch("src.integrations.sync.push_receiver.PushReceiver") as mock_pr:
            instance = MagicMock()
            instance.handle_delivery = AsyncMock(
                return_value=DeliveryResult(accepted=True, subscription_id="whsub_1")
            )
            mock_pr.return_value = instance

            resp = client.post(
                "/v1/webhooks/github/whsub_1",
                json={"action": "opened"},
                headers={"X-Hub-Signature-256": "sha256=deadbeef"},
            )

            assert resp.status_code == 200
            assert resp.json()["status"] == "accepted"
            instance.handle_delivery.assert_awaited_once()
            kwargs = instance.handle_delivery.call_args.kwargs
            assert kwargs["provider"] == "github"
            assert kwargs["subscription_id"] == "whsub_1"
            assert kwargs["raw_body"] is not None
    finally:
        _clear_session()


def test_provider_webhook_unknown_subscription_returns_404():
    _override_session()
    try:
        with patch("src.integrations.sync.push_receiver.PushReceiver") as mock_pr:
            instance = MagicMock()
            instance.handle_delivery = AsyncMock(
                return_value=DeliveryResult(accepted=False, error="unknown_subscription")
            )
            mock_pr.return_value = instance

            resp = client.post("/v1/webhooks/github/whsub_missing", json={})
            assert resp.status_code == 404
    finally:
        _clear_session()


def test_provider_webhook_signature_mismatch_returns_403():
    _override_session()
    try:
        with patch("src.integrations.sync.push_receiver.PushReceiver") as mock_pr:
            instance = MagicMock()
            instance.handle_delivery = AsyncMock(
                return_value=DeliveryResult(accepted=False, error="signature_mismatch")
            )
            mock_pr.return_value = instance

            resp = client.post("/v1/webhooks/slack/whsub_1", json={})
            assert resp.status_code == 403
    finally:
        _clear_session()


def test_provider_webhook_maps_backpressure_to_429():
    """The route maps a ``backpressure`` DeliveryResult to HTTP 429.

    The real lag decision now lives in PushReceiver (it needs the
    subscription's workspace, unknown at route-dependency time); the route's
    job is only to translate the result. See
    test_push_receiver.TestBackpressure for the real per-workspace lag path.
    """
    _override_session()
    try:
        with patch("src.integrations.sync.push_receiver.PushReceiver") as mock_pr:
            instance = MagicMock()
            instance.handle_delivery = AsyncMock(
                return_value=DeliveryResult(
                    accepted=False, subscription_id="whsub_1", error="backpressure"
                )
            )
            mock_pr.return_value = instance

            resp = client.post(
                "/v1/webhooks/github/whsub_1",
                json={"action": "opened"},
                headers={"X-Hub-Signature-256": "sha256=deadbeef"},
            )

            assert resp.status_code == 429
    finally:
        _clear_session()


def test_provider_webhook_forwards_provider_headers():
    """The route must forward provider-specific headers (Slack ts, Google
    channel headers, GitHub delivery id) to PushReceiver for verification."""
    _override_session()
    try:
        with patch("src.integrations.sync.push_receiver.PushReceiver") as mock_pr:
            instance = MagicMock()
            instance.handle_delivery = AsyncMock(
                return_value=DeliveryResult(accepted=True, subscription_id="whsub_1")
            )
            mock_pr.return_value = instance

            resp = client.post(
                "/v1/webhooks/google/whsub_1",
                content=b"",
                headers={
                    "X-Goog-Channel-Id": "chan_1",
                    "X-Goog-Channel-Token": "tok_1",
                    "X-Goog-Resource-State": "exists",
                    "X-Goog-Message-Number": "7",
                },
            )

            assert resp.status_code == 200
            kwargs = instance.handle_delivery.call_args.kwargs
            headers = kwargs["headers"]
            assert headers["x-goog-channel-id"] == "chan_1"
            assert headers["x-goog-channel-token"] == "tok_1"
            assert headers["x-goog-resource-state"] == "exists"
    finally:
        _clear_session()
