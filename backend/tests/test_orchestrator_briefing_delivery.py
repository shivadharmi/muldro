"""Bug B: orchestrator.generate_briefing delivers exactly once per (user, date).

A scheduled briefing run must produce exactly ONE notification and ONE surface
push. A second run for the same date (slow tick / worker restart) must NOT
re-deliver — the per-day briefing row is the idempotency key.
"""

from unittest.mock import AsyncMock, MagicMock, patch

import pytest

from src.orchestrator.services import ServiceContainer
from tests.conftest import TEST_USER_ID, make_mock_settings

WS = "ws_test"


def _build_orchestrator(notifier, *, briefing_exists: bool):
    """Construct a JarvisOrchestrator wired so generate_briefing can run without
    real DB/Claude. `briefing_exists` controls whether a briefing row already
    exists for today (the idempotency signal).
    """
    from src.orchestrator.jarvis import JarvisOrchestrator

    settings = make_mock_settings(use_bedrock=False)

    mock_db = MagicMock()
    mock_db.add = MagicMock()
    mock_db.flush = AsyncMock()
    mock_db.commit = AsyncMock()

    # db.execute → existing-briefing lookup returns a row iff briefing_exists.
    existing_result = MagicMock()
    existing_result.scalar_one_or_none.return_value = (
        MagicMock(briefing_id="brief_existing") if briefing_exists else None
    )
    mock_db.execute = AsyncMock(return_value=existing_result)

    db_ctx = AsyncMock()
    db_ctx.__aenter__ = AsyncMock(return_value=mock_db)
    db_ctx.__aexit__ = AsyncMock(return_value=False)
    db_factory = MagicMock(return_value=db_ctx)

    services = ServiceContainer(notifier=notifier)

    with patch("src.orchestrator.jarvis.get_anthropic_client", return_value=AsyncMock()):
        orch = JarvisOrchestrator(
            settings=settings,
            db_factory=db_factory,
            services=services,
        )

    # Avoid real tool execution + agent calls.
    orch._execute_tool = AsyncMock(return_value={"headline": "quiet"})
    orch._call_agent = AsyncMock(return_value="Today is quiet.")
    orch._publish_event = AsyncMock()
    orch._push_workspace_surface = AsyncMock()
    # request_services returns a container whose notifier is the one under test.
    orch._request_services = MagicMock(return_value=services)
    return orch


@pytest.mark.asyncio
async def test_scheduled_briefing_delivers_once():
    """First scheduled run (no prior briefing today) → exactly 1 notify + 1 push."""
    notifier = MagicMock()
    notifier.notify = AsyncMock()

    orch = _build_orchestrator(notifier, briefing_exists=False)
    await orch.generate_briefing(user_id=TEST_USER_ID, workspace_id=WS)

    assert notifier.notify.await_count == 1
    assert orch._push_workspace_surface.await_count == 1


@pytest.mark.asyncio
async def test_scheduled_briefing_second_run_does_not_redeliver():
    """A re-fire for the same date (briefing already exists) must NOT re-deliver."""
    notifier = MagicMock()
    notifier.notify = AsyncMock()

    orch = _build_orchestrator(notifier, briefing_exists=True)
    await orch.generate_briefing(user_id=TEST_USER_ID, workspace_id=WS)

    assert notifier.notify.await_count == 0
    assert orch._push_workspace_surface.await_count == 0
