"""Expiring an approval must cancel its run through the state machine, not around it.

``HeartbeatService._expire_approvals`` set ``run.status = "cancelled"`` directly.
Three things were lost by going around ``transition_run``:

* ``completed_at`` stayed NULL, so the run looked open forever to anything that
  reads it (that NULL is what identified this code path among three candidate
  cancel sites during the live perception diagnosis);
* ``run.error`` was empty, so the UI showed a bare "cancelled" with no cause —
  which is how four dead perception runs read as unexplained failures;
* the transition was never validated, so an illegal source status was silently
  accepted rather than raising.

CLAUDE.md: "Do not mutate TaskRun/TaskStep status directly — use
``transition_run()`` / ``transition_step()``."
"""

from datetime import datetime, timedelta, timezone
from unittest.mock import AsyncMock, MagicMock

from src.models.approvals import Approval
from src.models.task_graph import TaskRun
from src.services.heartbeat import HeartbeatService


def _make_approval(run_id: str) -> Approval:
    approval = Approval()
    approval.approval_id = "apr_expired"
    approval.user_id = "usr_test"
    approval.workspace_id = "ws_test"
    approval.execution_id = run_id
    approval.status = "pending"
    approval.expires_at = datetime.now(timezone.utc) - timedelta(hours=1)
    return approval


def _make_run(status: str = "awaiting_approval") -> TaskRun:
    run = TaskRun()
    run.run_id = "run_expired"
    run.user_id = "usr_test"
    run.workspace_id = "ws_test"
    run.status = status
    return run


def _make_service(approval: Approval, run: TaskRun | None) -> HeartbeatService:
    db = MagicMock()
    db.flush = AsyncMock()

    approvals_result = MagicMock()
    approvals_result.scalars.return_value.all.return_value = [approval]
    run_result = MagicMock()
    run_result.scalar_one_or_none.return_value = run

    db.execute = AsyncMock(side_effect=[approvals_result, run_result])
    return HeartbeatService(settings=MagicMock(), db=db)


async def test_expired_approval_cancels_run_with_completed_at_and_reason():
    approval = _make_approval("run_expired")
    run = _make_run()
    service = _make_service(approval, run)

    count = await service._expire_approvals("usr_test")

    assert count == 1
    assert approval.status == "expired"
    assert run.status == "cancelled"
    assert run.completed_at is not None, "a terminal run must record when it ended"
    assert run.error, "a cancelled run must say why it was cancelled"
    assert "approval" in str(run.error).lower()


async def test_run_not_awaiting_approval_is_left_alone():
    """Only a run actually parked on the approval may be cancelled by its expiry."""
    approval = _make_approval("run_expired")
    run = _make_run(status="running")
    service = _make_service(approval, run)

    await service._expire_approvals("usr_test")

    assert run.status == "running"
    assert run.completed_at is None


async def test_missing_run_does_not_break_expiry():
    """A prepared/chat approval has no linked run — expiry must still succeed."""
    approval = _make_approval("run_gone")
    service = _make_service(approval, None)

    count = await service._expire_approvals("usr_test")

    assert count == 1
    assert approval.status == "expired"
