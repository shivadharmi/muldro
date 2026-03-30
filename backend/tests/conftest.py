"""Shared test fixtures for Jarvis backend tests."""

import asyncio
import inspect
from datetime import datetime, timezone
from unittest.mock import MagicMock

import pytest

from src.services.event_processor import RawEvent

# Deterministic test user ID in proper usr_{ULID} format.
# ULID: 26 chars Crockford base32 (0-9, A-H, J-K, M-N, P-T, V-Z).
TEST_USER_ID = "usr_01JTEST00000000000000000000"
TEST_WORKSPACE_ID = "ws_test"


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
        elasticsearch_url="",
        daily_token_budget_usd=5.0,
        ses_from_address="",
        ses_region="ap-south-1",
        ses_enabled=False,
        bedrock_region="us-east-1",
        use_bedrock=False,
        event_processor_concurrency=5,
        max_perception_per_tick=5,
        webhook_lag_threshold=5000,
    )
    defaults.update(overrides)
    for k, v in defaults.items():
        setattr(settings, k, v)
    return settings


def pytest_configure(config):
    """Register local markers used by tests.

    The CI/local environment for this kata may not always provide pytest-asyncio.
    We still register the marker to avoid unknown-mark warnings.
    """
    config.addinivalue_line("markers", "asyncio: mark test as async")


@pytest.hookimpl(tryfirst=True)
def pytest_pyfunc_call(pyfuncitem):
    """Run async test functions without requiring external pytest plugins.

    If pytest-asyncio is installed, it can still handle tests first; this
    fallback only triggers for coroutine test functions that reach this hook.
    """
    test_fn = pyfuncitem.obj
    if not inspect.iscoroutinefunction(test_fn):
        return None

    kwargs = {
        name: pyfuncitem.funcargs[name]
        for name in pyfuncitem._fixtureinfo.argnames
        if name in pyfuncitem.funcargs
    }
    asyncio.run(test_fn(**kwargs))
    return True
