"""Bug B: orchestrator.generate_briefing delivers exactly once per (user, date).

A scheduled briefing run must produce exactly ONE notification and ONE surface
push. A second run for the same date (slow tick / worker restart) must NOT
re-deliver — the per-day briefing row is the idempotency key.
"""

from unittest.mock import AsyncMock, MagicMock

import pytest

from src.orchestrator.services import ServiceContainer
from tests.conftest import TEST_USER_ID, make_mock_settings

WS = "ws_test"


def _build_orchestrator(notifier, *, briefing_exists: bool):
    """Construct a MuldroOrchestrator wired so generate_briefing can run without
    real DB/Claude. `briefing_exists` controls whether a briefing row already
    exists for today (the idempotency signal).
    """
    from src.orchestrator.muldro import MuldroOrchestrator

    settings = make_mock_settings()

    mock_db = MagicMock()
    mock_db.add = MagicMock()
    mock_db.flush = AsyncMock()
    mock_db.commit = AsyncMock()

    # db.execute → first call is the idempotency check (existing-briefing
    # lookup, returns a row iff briefing_exists); subsequent calls are the
    # delivery-path Briefing fetch (only reached when briefing_exists=False).
    idem_result = MagicMock()
    idem_result.scalar_one_or_none.return_value = (
        MagicMock(briefing_id="brief_existing") if briefing_exists else None
    )
    delivery_result = MagicMock()
    delivery_result.scalar_one_or_none.return_value = MagicMock(
        briefing_id="brief_new", created_at=None
    )
    # first execute() = idempotency check; subsequent = delivery Briefing fetch.
    mock_db.execute = AsyncMock(side_effect=[idem_result, delivery_result, delivery_result])

    db_ctx = AsyncMock()
    db_ctx.__aenter__ = AsyncMock(return_value=mock_db)
    db_ctx.__aexit__ = AsyncMock(return_value=False)
    db_factory = MagicMock(return_value=db_ctx)

    services = ServiceContainer(notifier=notifier)

    orch = MuldroOrchestrator(
        settings=settings,
        db_factory=db_factory,
        services=services,
    )

    # Avoid real tool execution + agent calls.
    orch._execute_tool = AsyncMock(return_value={"headline": "quiet"})
    orch._call_agent = AsyncMock(return_value="Today is quiet.")
    orch._publish_event = AsyncMock()
    orch._push_briefing_surface = AsyncMock()
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
    orch._push_briefing_surface.assert_awaited_once()


@pytest.mark.asyncio
async def test_scheduled_briefing_second_run_does_not_redeliver():
    """A re-fire for the same date (briefing already exists) must NOT re-deliver."""
    notifier = MagicMock()
    notifier.notify = AsyncMock()

    orch = _build_orchestrator(notifier, briefing_exists=True)
    await orch.generate_briefing(user_id=TEST_USER_ID, workspace_id=WS)

    assert notifier.notify.await_count == 0
    orch._push_briefing_surface.assert_not_awaited()


@pytest.mark.asyncio
async def test_scheduled_briefing_second_run_skips_generation_entirely():
    """A re-fire must short-circuit BEFORE get_briefing + the Presenter agent.

    The duplicate-briefing bug was that the "already delivered" guard ran AFTER
    get_briefing (the tool) and the Presenter agent — and the Presenter agent's
    own push_ui_update had already shipped the surface to the UI before the guard
    fired. The guard must check-before-generate: when today's briefing exists, no
    get_briefing call, no Presenter LLM reformat, no push.
    """
    notifier = MagicMock()
    notifier.notify = AsyncMock()

    orch = _build_orchestrator(notifier, briefing_exists=True)
    result = await orch.generate_briefing(user_id=TEST_USER_ID, workspace_id=WS)

    # No regeneration: the tool and the Presenter agent must not run at all.
    assert orch._execute_tool.await_count == 0
    assert orch._call_agent.await_count == 0
    assert result.get("status") == "skipped"
