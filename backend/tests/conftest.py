"""Shared test fixtures for Jarvis backend tests."""

from datetime import datetime, timezone
from unittest.mock import MagicMock

from src.services.event_processor import RawEvent


def make_raw_event(**overrides) -> RawEvent:
    """Factory for test RawEvent instances."""
    defaults = dict(
        source="gmail",
        source_account_id="gmail_primary",
        event_type="email_received",
        entity_type="email_thread",
        entity_id="thr_001",
        occurred_at=datetime(2026, 3, 13, 8, 0, tzinfo=timezone.utc),
        title="Investor follow-up on deck",
        summary="Investor requested latest deck and quick call",
        actor={"type": "person", "email": "investor@fund.com", "name": "John Doe"},
        raw_payload=None,
    )
    defaults.update(overrides)
    return RawEvent(**defaults)


def make_mock_settings(**overrides) -> MagicMock:
    """Factory for mock Settings."""
    settings = MagicMock()
    defaults = dict(
        anthropic_api_key="test-key",
        anthropic_model="claude-sonnet-4-20250514",
        database_url="postgresql+asyncpg://test:test@localhost/test",
        importance_threshold=0.7,
        briefing_lookback_hours=24,
        debug=False,
    )
    defaults.update(overrides)
    for k, v in defaults.items():
        setattr(settings, k, v)
    return settings
