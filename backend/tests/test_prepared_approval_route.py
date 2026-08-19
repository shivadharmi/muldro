"""Confirming a PREPARED approval replays the recorded action (single-lead cutover, Task 6).

A prepared action was fully derived on the original turn and recorded on its ``Approval`` row.
Confirmation REPLAYS that payload — it is never routed through ``GraphExecutor``, whose agent
would re-derive the action and could run something other than what the founder reviewed.

Two things are pinned here:

1. A prepared approval belongs on the STANDARD approval endpoints. It carries no ``chat`` key,
   so ``_guard_not_chat_approval`` must let it through (a 409 would strand it forever).
2. The route wires the replay with BOTH the idempotency ledger and the cross-path write lock.
"""

from __future__ import annotations

from types import SimpleNamespace
from unittest.mock import AsyncMock, MagicMock, patch

import pytest
from fastapi import HTTPException

from src.api.routes_approvals import _guard_not_chat_approval, _run_prepared_action
from src.services.prepared_actions import PreparedActionResult
from tests.conftest import make_mock_settings

USER_ID = "usr_founder"
WORKSPACE_ID = "ws_prepared"


def _prepared_approval(**extra_refs):
    refs = {
        "prepared": True,
        "tool_name": "send_email",
        "capability": "email.send",
        "tool_input": '{"to": "a@b.com"}',
        "capability_scope": ["email.send"],
        **extra_refs,
    }
    return SimpleNamespace(
        approval_id="apr_prepared_001",
        user_id=USER_ID,
        workspace_id=WORKSPACE_ID,
        approval_type="prepared_action",
        status="approved",
        artifact_refs=refs,
    )


# ── invariant 6: prepared work rides the standard endpoints ──────────────────────


def test_prepared_approval_is_not_chat_guarded():
    """A prepared approval has NO ``chat`` key, so the chat guard must not fire.

    The prepare path deliberately omits the marker: a 409 here would push prepared work at
    ``POST /v1/muldro/chat/resume``, which has no turn to resume, stranding the row.
    """
    _guard_not_chat_approval(_prepared_approval())


def test_a_chat_approval_is_still_guarded():
    """The same row plus ``chat: True`` still 409s — the guard itself is unchanged."""
    with pytest.raises(HTTPException) as exc:
        _guard_not_chat_approval(_prepared_approval(chat=True))
    assert exc.value.status_code == 409


# ── the replay helper ────────────────────────────────────────────────────────────


async def test_approving_a_prepared_action_executes_it():
    approval = _prepared_approval()
    capture: dict = {}
    settings = make_mock_settings()
    orchestrator = SimpleNamespace(_execute_tool=AsyncMock(return_value={"ok": True}))
    redis_client = MagicMock()
    redis_client.aclose = AsyncMock()

    async def _fake_execute(appr, **kwargs):
        capture["approval"] = appr
        capture["kwargs"] = kwargs
        return PreparedActionResult(True, result={"ok": True})

    with (
        patch("src.api.routes_approvals.execute_prepared_action", _fake_execute),
        patch("src.api.routes_chat._get_orchestrator", AsyncMock(return_value=orchestrator)),
        patch("redis.asyncio.from_url", return_value=redis_client),
    ):
        outcome = await _run_prepared_action(approval, user_id=USER_ID, settings=settings)

    assert capture["approval"] is approval
    assert outcome.executed is True
    assert approval.status == "executed"


async def test_a_failed_prepared_action_marks_the_approval_failed():
    """A refusal is persisted, not raised — the queue drops the row either way and shows why."""
    approval = _prepared_approval()
    settings = make_mock_settings()
    orchestrator = SimpleNamespace(_execute_tool=AsyncMock())
    redis_client = MagicMock()
    redis_client.aclose = AsyncMock()

    async def _fake_execute(appr, **kwargs):
        return PreparedActionResult(False, error="unknown tool 'x' — refusing")

    with (
        patch("src.api.routes_approvals.execute_prepared_action", _fake_execute),
        patch("src.api.routes_chat._get_orchestrator", AsyncMock(return_value=orchestrator)),
        patch("redis.asyncio.from_url", return_value=redis_client),
    ):
        await _run_prepared_action(approval, user_id=USER_ID, settings=settings)

    assert approval.status == "failed"
    assert approval.artifact_refs["prepared_error"] == "unknown tool 'x' — refusing"


async def test_the_route_supplies_both_the_ledger_and_the_write_lock():
    """Both ``ledger`` and ``redis`` default to ``None`` in ``execute_prepared_action``, and
    both defaults are fail-OPEN: without the ledger a double-confirm double-fires the external
    write, and without redis a prepared confirm does not mutually exclude with a concurrent
    chat write to the same capability. A test that only asserted "executed once" would pass
    with ``ledger=None``, so this asserts the WIRING rather than the effect.
    """
    approval = _prepared_approval()
    capture: dict = {}
    settings = make_mock_settings()
    orchestrator = SimpleNamespace(_execute_tool=AsyncMock(return_value={"ok": True}))
    redis_client = MagicMock()
    redis_client.aclose = AsyncMock()

    async def _fake_execute(appr, **kwargs):
        capture["kwargs"] = kwargs
        return PreparedActionResult(True, result={"ok": True})

    with (
        patch("src.api.routes_approvals.execute_prepared_action", _fake_execute),
        patch("src.api.routes_chat._get_orchestrator", AsyncMock(return_value=orchestrator)),
        patch("redis.asyncio.from_url", return_value=redis_client),
    ):
        await _run_prepared_action(approval, user_id=USER_ID, settings=settings)

    kwargs = capture["kwargs"]
    assert kwargs["ledger"] is not None, "no ledger → a double-confirm double-fires the write"
    assert kwargs["redis"] is not None, "no redis → no cross-path write lock"
    # And the executor it was handed is the real dispatcher entry point, not a re-derivation.
    assert kwargs["execute_tool"] is orchestrator._execute_tool
