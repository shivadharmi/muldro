"""Tests for the /v1/events/ingest endpoint."""

from unittest.mock import AsyncMock, MagicMock, patch

import pytest
from fastapi.testclient import TestClient

from src.api.app import app
from src.api.deps import get_current_user, get_current_user_id


@pytest.fixture(autouse=True)
def _override_auth():
    mock_user = MagicMock()
    mock_user.user_id = "usr_default"

    app.dependency_overrides[get_current_user] = lambda: mock_user
    app.dependency_overrides[get_current_user_id] = lambda: "usr_default"
    yield
    app.dependency_overrides.pop(get_current_user, None)
    app.dependency_overrides.pop(get_current_user_id, None)


client = TestClient(app)

VALID_PAYLOAD = {
    "source": "gmail",
    "event_type": "email_received",
    "entity_type": "email_thread",
    "entity_id": "thr_123",
    "title": "Investor follow-up on deck",
    "summary": "Investor requested latest deck and quick call",
    "actor": {"type": "person", "email": "investor@fund.com", "name": "John Doe"},
}


@patch("src.api.routes_events._make_event_processor")
def test_ingest_valid_event(mock_make_processor):
    """Valid event payload returns event_id and processed status."""
    mock_processor = MagicMock()
    mock_processor.process = AsyncMock(return_value="evt_test123")
    mock_make_processor.return_value = mock_processor

    response = client.post("/v1/events/ingest", json=VALID_PAYLOAD)

    assert response.status_code == 200
    data = response.json()
    assert data["status"] == "processed"
    assert data["event_id"] == "evt_test123"
    mock_processor.process.assert_called_once()


@patch("src.api.routes_events._make_event_processor")
def test_ingest_duplicate_event(mock_make_processor):
    """Duplicate event (same idempotency key) returns duplicate status."""
    mock_processor = MagicMock()
    mock_processor.process = AsyncMock(return_value=None)
    mock_make_processor.return_value = mock_processor

    response = client.post("/v1/events/ingest", json=VALID_PAYLOAD)

    assert response.status_code == 200
    data = response.json()
    assert data["status"] == "duplicate"
    assert data["event_id"] is None


def test_ingest_invalid_payload():
    """Missing required fields returns 422."""
    response = client.post("/v1/events/ingest", json={"source": "gmail"})
    assert response.status_code == 422


@patch("src.api.routes_events._make_event_processor")
def test_ingest_minimal_payload(mock_make_processor):
    """Minimal payload (only required fields) is accepted."""
    mock_processor = MagicMock()
    mock_processor.process = AsyncMock(return_value="evt_min123")
    mock_make_processor.return_value = mock_processor

    minimal = {
        "source": "github",
        "event_type": "pr_opened",
        "entity_type": "pull_request",
        "entity_id": "pr_42",
        "title": "feat: add login page",
    }
    response = client.post("/v1/events/ingest", json=minimal)

    assert response.status_code == 200
    data = response.json()
    assert data["status"] == "processed"
    assert data["event_id"] == "evt_min123"


@patch("src.api.routes_events._make_event_processor")
def test_ingest_passes_raw_event_fields(mock_make_processor):
    """Event processor receives correctly mapped RawEvent fields."""
    mock_processor = MagicMock()
    mock_processor.process = AsyncMock(return_value="evt_fields123")
    mock_make_processor.return_value = mock_processor

    payload = {
        **VALID_PAYLOAD,
        "occurred_at": "2026-03-14T10:00:00Z",
        "raw_payload": {"message_id": "msg_abc"},
    }
    response = client.post("/v1/events/ingest", json=payload)

    assert response.status_code == 200
    # Verify the RawEvent passed to process has correct fields
    call_args = mock_processor.process.call_args
    raw_event = call_args[0][0]
    assert raw_event.source == "gmail"
    assert raw_event.entity_id == "thr_123"
    assert raw_event.title == "Investor follow-up on deck"
    assert raw_event.actor == {"type": "person", "email": "investor@fund.com", "name": "John Doe"}
    assert raw_event.raw_payload == {"message_id": "msg_abc"}
