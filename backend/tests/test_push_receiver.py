"""Tests for push_receiver normalization functions."""

from src.integrations.sync.push_receiver import _normalize_gmail


def test_normalize_gmail_uses_stable_entity_id():
    """Gmail webhook normalization must NOT use historyId as entity_id."""
    payload = {"historyId": "9876543", "emailAddress": "user@gmail.com"}
    result = _normalize_gmail(payload)
    assert result["entity_id"] == "user@gmail.com"
    assert result["event_type"] == "gmail_webhook_signal"


def test_normalize_gmail_fallback_entity_id():
    """When emailAddress missing, fall back to 'gmail_push'."""
    payload = {"historyId": "9876543"}
    result = _normalize_gmail(payload)
    assert result["entity_id"] == "gmail_push"
