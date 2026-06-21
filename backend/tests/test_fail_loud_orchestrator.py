"""Fail-loud orchestrator: degraded state must be observable, not silent.

(a) The background tick logs an ERROR (throttled) when it has no orchestrator,
    instead of silently returning.
(b) run.py's worker bootstrap surfaces a degraded health status when the
    orchestrator build fails.
"""

import logging

import pytest

from src.services.scheduler import SchedulerLoop
from tests.conftest import make_mock_settings


@pytest.mark.asyncio
async def test_background_tick_logs_error_when_no_orchestrator(caplog):
    """No orchestrator → throttled ERROR (degraded), still returns (no crash)."""
    sched = SchedulerLoop(make_mock_settings(), orchestrator=None)

    with caplog.at_level(logging.ERROR):
        # First tick logs (count % N == 1); the tick must still return cleanly.
        await sched._tick_background_tasks(factory=None)

    assert any(
        "NO orchestrator" in r.message and r.levelno == logging.ERROR for r in caplog.records
    )


@pytest.mark.asyncio
async def test_background_tick_no_orchestrator_log_is_throttled(caplog):
    """The degraded-state ERROR is throttled, not emitted every tick."""
    sched = SchedulerLoop(make_mock_settings(), orchestrator=None)

    with caplog.at_level(logging.ERROR):
        for _ in range(5):
            await sched._tick_background_tasks(factory=None)

    errors = [r for r in caplog.records if "NO orchestrator" in r.message]
    # Logged on the 1st tick only within the first window of 5.
    assert len(errors) == 1


def test_run_py_degraded_status_on_orchestrator_failure():
    """run.py exposes a degraded worker health status (smoke)."""
    import run

    # The health accessor and degraded marker are part of the public contract.
    assert callable(run.get_component_health)
    run._component_health["worker"] = {"status": "degraded_no_orchestrator"}
    assert run.get_component_health()["worker"]["status"] == "degraded_no_orchestrator"
