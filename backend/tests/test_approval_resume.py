"""Tests for approval resume failure handling — verifies runs transition to failed
when resume_run or execute_run raises after a successful approval decision."""

from datetime import datetime, timezone
from unittest.mock import AsyncMock, MagicMock, patch

import pytest

from tests.conftest import TEST_USER_ID, TEST_WORKSPACE_ID, make_mock_settings


def _make_approval(**overrides):
    """Factory for mock Approval objects."""
    approval = MagicMock()
    defaults = dict(
        approval_id="apr_001",
        status="pending",
        title="Send email",
        summary="Send email to investor",
        risk_level="medium",
        created_at=datetime(2026, 4, 12, tzinfo=timezone.utc),
        decided_at=None,
        decision_reason=None,
        approved_by=None,
        approval_type="email.send",
        run_id=None,
        execution_id=None,
        step_id=None,
        artifact_refs=None,
        user_id=TEST_USER_ID,
        workspace_id=TEST_WORKSPACE_ID,
    )
    defaults.update(overrides)
    for k, v in defaults.items():
        setattr(approval, k, v)
    return approval


def _make_run(run_id="run_001", status="running", plan_id=None, checkpoint=None):
    """Factory for mock TaskRun objects."""
    run = MagicMock()
    run.run_id = run_id
    run.status = status
    run.plan_id = plan_id
    run.checkpoint = checkpoint or {}
    run.error = None
    run.completed_at = None
    return run


def _make_step(step_id="step_001", run_id="run_001", status="waiting_approval"):
    """Factory for mock TaskStep objects."""
    step = MagicMock()
    step.step_id = step_id
    step.run_id = run_id
    step.status = status
    return step


class _FakeResult:
    """Mimics SQLAlchemy Result.scalar_one_or_none()."""

    def __init__(self, value):
        self._value = value

    def scalar_one_or_none(self):
        return self._value

    def scalars(self):
        return self

    def all(self):
        return [self._value] if self._value else []


@pytest.mark.asyncio
async def test_step_level_resume_failure_marks_run_failed():
    """When resume_run raises after step-level approval, the run should be
    transitioned to 'failed' with error details."""
    from src.api.routes_approvals import approve_action

    approval = _make_approval(run_id="run_001", step_id="step_001")
    step = _make_step()
    run_after_fail = _make_run(run_id="run_001", status="running")

    # Track which queries return what.
    # Since _get_approval is patched, db.execute calls are:
    #   1. select(TaskRun) for effective_run_id -> run_obj
    #   2. select(TaskStep) for step transition -> step
    #   3. (after resume_run fails + rollback) select(TaskRun) for failure marking -> run_after_fail
    call_count = 0
    run_obj = _make_run(run_id="run_001", status="awaiting_approval")

    async def fake_execute(stmt):
        nonlocal call_count
        call_count += 1
        if call_count == 1:
            # select(TaskRun) for effective_run_id
            return _FakeResult(run_obj)
        elif call_count == 2:
            # select(TaskStep) for step
            return _FakeResult(step)
        elif call_count == 3:
            # re-fetch run after resume failure
            return _FakeResult(run_after_fail)
        return _FakeResult(None)

    db = MagicMock()
    db.execute = AsyncMock(side_effect=fake_execute)
    db.commit = AsyncMock()
    db.flush = AsyncMock()
    db.rollback = AsyncMock()
    db.add = MagicMock()

    settings = make_mock_settings(qdrant_url="", redis_url="redis://localhost:6379/0")

    mock_executor = MagicMock()
    mock_executor.resume_run = AsyncMock(side_effect=RuntimeError("connection reset"))

    with (
        patch(
            "src.api.routes_approvals._get_approval",
            new_callable=AsyncMock,
            return_value=approval,
        ),
        patch("src.api.routes_approvals.AuditService") as mock_audit_cls,
        patch(
            "src.services.graph_executor.create_graph_executor",
            new_callable=AsyncMock,
            return_value=mock_executor,
        ),
        patch("src.services.execution_state.transition_run") as mock_transition_run,
        patch("src.services.execution_state.transition_step"),
        patch(
            "src.services.risk_assessor.record_approval_decision",
            new_callable=AsyncMock,
        ),
    ):
        mock_audit_cls.return_value.log = AsyncMock()

        result = await approve_action(
            approval_id="apr_001",
            req=None,
            user_id=TEST_USER_ID,
            workspace_id=TEST_WORKSPACE_ID,
            db=db,
            settings=settings,
        )

    # The endpoint still returns 200 (approval succeeded, resume failed)
    assert result.status == "approved"

    # The run should have been marked as failed
    assert run_after_fail.error == {"resume_failed": "connection reset"}
    assert run_after_fail.completed_at is not None
    mock_transition_run.assert_called_once_with(run_after_fail, "failed")
    # DB should have been committed after marking failure
    assert db.rollback.called
    assert db.commit.called


@pytest.mark.asyncio
async def test_plan_level_resume_failure_marks_run_failed():
    """When execute_run raises after plan-level approval, the run should be
    transitioned to 'failed' with error details."""
    from src.api.routes_approvals import approve_action

    run_obj = _make_run(run_id="run_002", status="running", plan_id="plan_001")
    # No run_id on approval, but execution_id links to the run
    approval = _make_approval(
        run_id=None,
        execution_id="run_002",
        step_id=None,
    )

    run_after_fail = _make_run(run_id="run_002", status="running")

    call_count = 0

    async def fake_execute(stmt):
        nonlocal call_count
        call_count += 1
        if call_count == 1:
            return _FakeResult(run_obj)
        elif call_count == 2:
            # re-fetch run after execute_run failure
            return _FakeResult(run_after_fail)
        return _FakeResult(None)

    db = MagicMock()
    db.execute = AsyncMock(side_effect=fake_execute)
    db.commit = AsyncMock()
    db.flush = AsyncMock()
    db.rollback = AsyncMock()
    db.add = MagicMock()

    settings = make_mock_settings(qdrant_url="", redis_url="redis://localhost:6379/0")

    mock_executor = MagicMock()
    mock_executor.execute_run = AsyncMock(side_effect=RuntimeError("executor crashed"))

    with (
        patch(
            "src.api.routes_approvals._get_approval",
            new_callable=AsyncMock,
            return_value=approval,
        ),
        patch("src.api.routes_approvals.AuditService") as mock_audit_cls,
        patch(
            "src.services.graph_executor.create_graph_executor",
            new_callable=AsyncMock,
            return_value=mock_executor,
        ),
        patch("src.services.execution_state.transition_run") as mock_transition_run,
        patch(
            "src.services.risk_assessor.record_approval_decision",
            new_callable=AsyncMock,
        ),
    ):
        mock_audit_cls.return_value.log = AsyncMock()

        result = await approve_action(
            approval_id="apr_002",
            req=None,
            user_id=TEST_USER_ID,
            workspace_id=TEST_WORKSPACE_ID,
            db=db,
            settings=settings,
        )

    # The endpoint still returns 200 (approval succeeded, resume failed)
    assert result.status == "approved"

    # The run should have been marked as failed
    assert run_after_fail.error == {"resume_failed": "executor crashed"}
    assert run_after_fail.completed_at is not None
    mock_transition_run.assert_called_once_with(run_after_fail, "failed")
    assert db.rollback.called


@pytest.mark.asyncio
async def test_tool_level_execute_failure_marks_run_failed():
    """When execute_run raises for a tool-level approval resume, the bg_run
    should be transitioned to 'failed'."""
    from src.api.routes_approvals import approve_action

    approval = _make_approval(
        run_id=None,
        execution_id=None,
        step_id=None,
        artifact_refs={"tool_name": "email_send", "tool_params": {"to": "a@b.com"}},
    )

    # The run returned when we re-fetch after failure
    run_after_fail = _make_run(run_id="run_bg", status="running")

    call_count = 0

    async def fake_execute(stmt):
        nonlocal call_count
        call_count += 1
        if call_count == 1:
            # select(TaskRun) for effective_run_id (None → no run)
            return _FakeResult(None)
        # Later calls during tool-level flow are for re-fetch after failure
        return _FakeResult(run_after_fail)

    db = MagicMock()
    db.execute = AsyncMock(side_effect=fake_execute)
    db.commit = AsyncMock()
    db.flush = AsyncMock()
    db.rollback = AsyncMock()
    db.add = MagicMock()

    settings = make_mock_settings(qdrant_url="", redis_url="redis://localhost:6379/0")

    mock_executor = MagicMock()
    mock_executor.populate_run_steps = AsyncMock()
    mock_executor.execute_run = AsyncMock(side_effect=RuntimeError("tool exec failed"))

    with (
        patch(
            "src.api.routes_approvals._get_approval",
            new_callable=AsyncMock,
            return_value=approval,
        ),
        patch("src.api.routes_approvals.AuditService") as mock_audit_cls,
        patch(
            "src.services.graph_executor.create_graph_executor",
            new_callable=AsyncMock,
            return_value=mock_executor,
        ),
        patch("src.services.execution_state.transition_run") as mock_transition_run,
        patch(
            "src.services.risk_assessor.record_approval_decision",
            new_callable=AsyncMock,
        ),
    ):
        mock_audit_cls.return_value.log = AsyncMock()

        result = await approve_action(
            approval_id="apr_003",
            req=None,
            user_id=TEST_USER_ID,
            workspace_id=TEST_WORKSPACE_ID,
            db=db,
            settings=settings,
        )

    # The endpoint still returns 200
    assert result.status == "approved"

    # The bg_run should have been marked as failed
    assert run_after_fail.error == {"resume_failed": "tool exec failed"}
    assert run_after_fail.completed_at is not None
    mock_transition_run.assert_called_once_with(run_after_fail, "failed")
    assert db.rollback.called


@pytest.mark.asyncio
async def test_resume_failure_does_not_crash_when_run_already_terminal():
    """When resume_run fails but the run is already in a terminal state,
    the handler should NOT attempt to transition it — just log and continue."""
    from src.api.routes_approvals import approve_action

    approval = _make_approval(run_id="run_004", step_id="step_004")
    step = _make_step(step_id="step_004", run_id="run_004")
    # Run already completed by the time we re-fetch
    run_already_done = _make_run(run_id="run_004", status="completed")

    call_count = 0
    run_obj = _make_run(run_id="run_004", status="awaiting_approval")

    async def fake_execute(stmt):
        nonlocal call_count
        call_count += 1
        if call_count == 1:
            return _FakeResult(run_obj)
        elif call_count == 2:
            return _FakeResult(step)
        elif call_count == 3:
            # re-fetch: run already completed
            return _FakeResult(run_already_done)
        return _FakeResult(None)

    db = MagicMock()
    db.execute = AsyncMock(side_effect=fake_execute)
    db.commit = AsyncMock()
    db.flush = AsyncMock()
    db.rollback = AsyncMock()
    db.add = MagicMock()

    settings = make_mock_settings(qdrant_url="", redis_url="redis://localhost:6379/0")

    mock_executor = MagicMock()
    mock_executor.resume_run = AsyncMock(side_effect=RuntimeError("boom"))

    with (
        patch(
            "src.api.routes_approvals._get_approval",
            new_callable=AsyncMock,
            return_value=approval,
        ),
        patch("src.api.routes_approvals.AuditService") as mock_audit_cls,
        patch(
            "src.services.graph_executor.create_graph_executor",
            new_callable=AsyncMock,
            return_value=mock_executor,
        ),
        patch("src.services.execution_state.transition_run") as mock_transition_run,
        patch("src.services.execution_state.transition_step"),
        patch(
            "src.services.risk_assessor.record_approval_decision",
            new_callable=AsyncMock,
        ),
    ):
        mock_audit_cls.return_value.log = AsyncMock()

        result = await approve_action(
            approval_id="apr_004",
            req=None,
            user_id=TEST_USER_ID,
            workspace_id=TEST_WORKSPACE_ID,
            db=db,
            settings=settings,
        )

    # Still returns 200
    assert result.status == "approved"
    # transition_run should NOT have been called (run already terminal)
    mock_transition_run.assert_not_called()
    # Error and completed_at should NOT have been set on the already-completed run
    assert run_already_done.error is None
    assert run_already_done.completed_at is None


@pytest.mark.asyncio
async def test_resume_failure_recovery_itself_fails_gracefully():
    """When both resume_run AND the recovery (marking as failed) both fail,
    the endpoint should still return 200 — no unhandled exception."""
    from src.api.routes_approvals import approve_action

    approval = _make_approval(run_id="run_005", step_id="step_005")
    step = _make_step(step_id="step_005", run_id="run_005")
    run_obj = _make_run(run_id="run_005", status="awaiting_approval")

    call_count = 0

    async def fake_execute(stmt):
        nonlocal call_count
        call_count += 1
        if call_count == 1:
            return _FakeResult(run_obj)
        elif call_count == 2:
            return _FakeResult(step)
        elif call_count == 3:
            # Recovery re-fetch also fails
            raise RuntimeError("DB connection lost")
        return _FakeResult(None)

    db = MagicMock()
    db.execute = AsyncMock(side_effect=fake_execute)
    db.commit = AsyncMock()
    db.flush = AsyncMock()
    db.rollback = AsyncMock()
    db.add = MagicMock()

    settings = make_mock_settings(qdrant_url="", redis_url="redis://localhost:6379/0")

    mock_executor = MagicMock()
    mock_executor.resume_run = AsyncMock(side_effect=RuntimeError("resume boom"))

    with (
        patch(
            "src.api.routes_approvals._get_approval",
            new_callable=AsyncMock,
            return_value=approval,
        ),
        patch("src.api.routes_approvals.AuditService") as mock_audit_cls,
        patch(
            "src.services.graph_executor.create_graph_executor",
            new_callable=AsyncMock,
            return_value=mock_executor,
        ),
        patch("src.services.execution_state.transition_step"),
        patch(
            "src.services.risk_assessor.record_approval_decision",
            new_callable=AsyncMock,
        ),
    ):
        mock_audit_cls.return_value.log = AsyncMock()

        # Should not raise — inner exception handler catches the DB failure
        result = await approve_action(
            approval_id="apr_005",
            req=None,
            user_id=TEST_USER_ID,
            workspace_id=TEST_WORKSPACE_ID,
            db=db,
            settings=settings,
        )

    assert result.status == "approved"
