"""Tests for AlertingService."""

from datetime import datetime, timezone

import pytest

from src.services.alerting import AlertingService, SLOCheck
from src.services.trace_store import TraceStore
from tests.conftest import TEST_USER_ID


@pytest.fixture
def trace_store():
    return TraceStore()


@pytest.fixture
def alerting(trace_store):
    return AlertingService(trace_store=trace_store)


@pytest.mark.asyncio
async def test_check_all_slos_no_data(alerting):
    checks = await alerting.check_all_slos()
    assert len(checks) == 3
    assert all(isinstance(c, SLOCheck) for c in checks)


@pytest.mark.asyncio
async def test_event_latency_ok(trace_store):
    await trace_store.store_trace(
        {
            "trace_id": "t1",
            "trigger": "test",
            "started_at": datetime.now(timezone.utc).isoformat(),
            "spans": [
                {
                    "agent_name": "observer",
                    "duration_ms": 500,
                    "input_tokens": 0,
                    "output_tokens": 0,
                },
            ],
        },
        user_id=TEST_USER_ID,
    )
    alerting = AlertingService(trace_store=trace_store)
    checks = await alerting.check_all_slos()
    latency = next(c for c in checks if c.name == "event_latency")
    assert latency.status == "ok"


@pytest.mark.asyncio
async def test_event_latency_critical(trace_store):
    await trace_store.store_trace(
        {
            "trace_id": "t1",
            "trigger": "test",
            "started_at": datetime.now(timezone.utc).isoformat(),
            "spans": [
                {
                    "agent_name": "observer",
                    "duration_ms": 3000,
                    "input_tokens": 0,
                    "output_tokens": 0,
                },
            ],
        },
        user_id=TEST_USER_ID,
    )
    alerting = AlertingService(trace_store=trace_store)
    checks = await alerting.check_all_slos()
    latency = next(c for c in checks if c.name == "event_latency")
    assert latency.status == "critical"


@pytest.mark.asyncio
async def test_error_rate_ok(trace_store):
    await trace_store.store_trace(
        {
            "trace_id": "t1",
            "trigger": "test",
            "started_at": datetime.now(timezone.utc).isoformat(),
            "spans": [
                {
                    "agent_name": "planner",
                    "duration_ms": 100,
                    "input_tokens": 0,
                    "output_tokens": 0,
                },
                {
                    "agent_name": "operator",
                    "duration_ms": 200,
                    "input_tokens": 0,
                    "output_tokens": 0,
                },
            ],
        },
        user_id=TEST_USER_ID,
    )
    alerting = AlertingService(trace_store=trace_store)
    checks = await alerting.check_all_slos()
    error_rate = next(c for c in checks if c.name == "error_rate")
    assert error_rate.status == "ok"
    assert error_rate.value == 0


@pytest.mark.asyncio
async def test_error_rate_critical(trace_store):
    await trace_store.store_trace(
        {
            "trace_id": "t1",
            "trigger": "test",
            "started_at": datetime.now(timezone.utc).isoformat(),
            "spans": [
                {
                    "agent_name": "planner",
                    "duration_ms": 100,
                    "input_tokens": 0,
                    "output_tokens": 0,
                    "error": "failed",
                },
                {
                    "agent_name": "observer",
                    "duration_ms": 200,
                    "input_tokens": 0,
                    "output_tokens": 0,
                },
            ],
        },
        user_id=TEST_USER_ID,
    )
    alerting = AlertingService(trace_store=trace_store)
    checks = await alerting.check_all_slos()
    error_rate = next(c for c in checks if c.name == "error_rate")
    assert error_rate.status == "critical"
    assert error_rate.value == 50.0


@pytest.mark.asyncio
async def test_budget_check_no_tracker():
    alerting = AlertingService()
    checks = await alerting.check_all_slos()
    budget = next(c for c in checks if c.name == "budget_usage")
    assert budget.status == "ok"


@pytest.mark.asyncio
async def test_budget_check_with_tracker():
    class FakeBudget:
        def snapshot(self):
            return {"daily_spend_usd": 4.8, "daily_limit_usd": 5.0}

    alerting = AlertingService(budget_tracker=FakeBudget())
    checks = await alerting.check_all_slos()
    budget = next(c for c in checks if c.name == "budget_usage")
    assert budget.status == "critical"
    assert budget.value == 96.0


@pytest.mark.asyncio
async def test_alert_cooldown(trace_store):
    fired = []

    class FakeNotifier:
        async def send(self, **kwargs):
            fired.append(kwargs)

    await trace_store.store_trace(
        {
            "trace_id": "t1",
            "trigger": "test",
            "started_at": datetime.now(timezone.utc).isoformat(),
            "spans": [
                {
                    "agent_name": "observer",
                    "duration_ms": 3000,
                    "input_tokens": 0,
                    "output_tokens": 0,
                },
            ],
        },
        user_id=TEST_USER_ID,
    )
    alerting = AlertingService(notifier=FakeNotifier(), trace_store=trace_store)

    await alerting.check_all_slos()
    count_first = len(fired)
    assert count_first > 0

    # Second check should be in cooldown
    await alerting.check_all_slos()
    assert len(fired) == count_first  # no new alerts
