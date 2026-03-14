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
        redis_url="redis://localhost:6379/0",
        importance_threshold=0.7,
        briefing_lookback_hours=24,
        debug=False,
        retry_max_attempts=3,
        retry_base_delay=0.01,
        retry_max_delay=0.1,
        plan_ttl_hours=72,
        approval_ttl_hours=24,
        dlq_max_attempts=3,
        rate_limit_rpm=120,
        max_request_body_bytes=1_048_576,
        cors_allowed_origins="",
        openclaw_gateway_url="http://localhost:18789",
        openclaw_hook_token="test-hook-token",
        openclaw_gateway_token="test-gateway-token",
    )
    defaults.update(overrides)
    for k, v in defaults.items():
        setattr(settings, k, v)
    return settings
