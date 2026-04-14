"""Tests for approval resume flow — verifies that the approval handler queues
runs for scheduler pickup instead of executing synchronously, and that the
scheduler + GraphExecutor correctly resume approved runs."""

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


def _make_run(run_id="run_001", status="running", plan_id=None, checkpoint=None, source=None):
    """Factory for mock TaskRun objects."""
    run = MagicMock()
    run.run_id = run_id
    run.status = status
    run.plan_id = plan_id
    run.checkpoint = checkpoint or {}
    run.error = None
    run.completed_at = None
    run.source = source or "background"
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


# ── Step-level approval: queues for scheduler ────────────────────────


@pytest.mark.asyncio
async def test_step_level_approval_queues_for_scheduler():
    """After step-level approval, the handler should tag the run with
    source='approval_resume' and transition step to 'running' — NOT
    call resume_run synchronously."""
    from src.api.routes_approvals import approve_action

    approval = _make_approval(run_id="run_001", step_id="step_001")
    step = _make_step()
    run_obj = _make_run(run_id="run_001", status="awaiting_approval")

    call_count = 0

    async def fake_execute(stmt):
        nonlocal call_count
        call_count += 1
        if call_count == 1:
            # select(TaskRun) for effective_run_id
            return _FakeResult(run_obj)
        elif call_count == 2:
            # select(TaskStep) for step transition
            return _FakeResult(step)
        elif call_count == 3:
            # re-fetch run for source update
            return _FakeResult(run_obj)
        return _FakeResult(None)

    db = MagicMock()
    db.execute = AsyncMock(side_effect=fake_execute)
    db.commit = AsyncMock()
    db.flush = AsyncMock()
    db.rollback = AsyncMock()
    db.add = MagicMock()

    settings = make_mock_settings(qdrant_url="", redis_url="redis://localhost:6379/0")

    with (
        patch(
            "src.api.routes_approvals._get_approval",
            new_callable=AsyncMock,
            return_value=approval,
        ),
        patch("src.api.routes_approvals.AuditService") as mock_audit_cls,
        patch("src.api.routes_approvals.transition_step") as mock_transition_step,
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

    assert result.status == "approved"
    # Step should have been transitioned to running
    mock_transition_step.assert_called_once_with(step, "running")
    # Run should be tagged for scheduler pickup
    assert run_obj.source == "approval_resume"
    # DB should have been committed (persisting the source change)
    assert db.commit.call_count >= 2  # initial approval commit + scheduler queue commit


@pytest.mark.asyncio
async def test_step_level_approval_does_not_call_resume_run():
    """The approval handler must NOT create a GraphExecutor or call resume_run
    for step-level approvals — the scheduler handles execution."""
    from src.api.routes_approvals import approve_action

    approval = _make_approval(run_id="run_001", step_id="step_001")
    step = _make_step()
    run_obj = _make_run(run_id="run_001", status="awaiting_approval")

    call_count = 0

    async def fake_execute(stmt):
        nonlocal call_count
        call_count += 1
        if call_count == 1:
            return _FakeResult(run_obj)
        elif call_count == 2:
            return _FakeResult(step)
        elif call_count == 3:
            return _FakeResult(run_obj)
        return _FakeResult(None)

    db = MagicMock()
    db.execute = AsyncMock(side_effect=fake_execute)
    db.commit = AsyncMock()
    db.flush = AsyncMock()
    db.rollback = AsyncMock()
    db.add = MagicMock()

    settings = make_mock_settings(qdrant_url="", redis_url="redis://localhost:6379/0")

    with (
        patch(
            "src.api.routes_approvals._get_approval",
            new_callable=AsyncMock,
            return_value=approval,
        ),
        patch("src.api.routes_approvals.AuditService") as mock_audit_cls,
        patch(
            "src.api.routes_approvals.create_graph_executor",
            new_callable=AsyncMock,
        ) as mock_create_executor,
        patch("src.api.routes_approvals.transition_step"),
        patch(
            "src.services.risk_assessor.record_approval_decision",
            new_callable=AsyncMock,
        ),
    ):
        mock_audit_cls.return_value.log = AsyncMock()

        await approve_action(
            approval_id="apr_001",
            req=None,
            user_id=TEST_USER_ID,
            workspace_id=TEST_WORKSPACE_ID,
            db=db,
            settings=settings,
        )

    # create_graph_executor should NOT be called for step-level approval
    mock_create_executor.assert_not_called()


# ── Plan-level approval: queues for scheduler ────────────────────────


@pytest.mark.asyncio
async def test_plan_level_approval_queues_for_scheduler():
    """Plan-level approval should tag the run with source='approval_resume'
    without calling execute_run synchronously."""
    from src.api.routes_approvals import approve_action

    run_obj = _make_run(run_id="run_002", status="awaiting_approval", plan_id="plan_001")
    approval = _make_approval(run_id=None, execution_id="run_002", step_id=None)

    call_count = 0

    async def fake_execute(stmt):
        nonlocal call_count
        call_count += 1
        if call_count == 1:
            # select(TaskRun) for effective_run_id
            return _FakeResult(run_obj)
        elif call_count == 2:
            # re-fetch run for source update
            return _FakeResult(run_obj)
        return _FakeResult(None)

    db = MagicMock()
    db.execute = AsyncMock(side_effect=fake_execute)
    db.commit = AsyncMock()
    db.flush = AsyncMock()
    db.rollback = AsyncMock()
    db.add = MagicMock()

    settings = make_mock_settings(qdrant_url="", redis_url="redis://localhost:6379/0")

    with (
        patch(
            "src.api.routes_approvals._get_approval",
            new_callable=AsyncMock,
            return_value=approval,
        ),
        patch("src.api.routes_approvals.AuditService") as mock_audit_cls,
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

    assert result.status == "approved"
    assert run_obj.source == "approval_resume"


# ── Tool-level approval: creates bg_run without executing ────────────


@pytest.mark.asyncio
async def test_tool_level_approval_creates_bg_run_without_executing():
    """Tool-level approval should create a background TaskRun and populate
    steps, but NOT call execute_run — the scheduler handles execution."""
    from src.api.routes_approvals import approve_action

    approval = _make_approval(
        run_id=None,
        execution_id=None,
        step_id=None,
        artifact_refs={"tool_name": "email_send", "tool_params": {"to": "a@b.com"}},
    )

    call_count = 0

    async def fake_execute(stmt):
        nonlocal call_count
        call_count += 1
        if call_count == 1:
            return _FakeResult(None)
        return _FakeResult(None)

    db = MagicMock()
    db.execute = AsyncMock(side_effect=fake_execute)
    db.commit = AsyncMock()
    db.flush = AsyncMock()
    db.rollback = AsyncMock()
    db.add = MagicMock()

    settings = make_mock_settings(qdrant_url="", redis_url="redis://localhost:6379/0")

    mock_executor = MagicMock()
    mock_executor.populate_run_steps = AsyncMock()
    # execute_run should NOT be called
    mock_executor.execute_run = AsyncMock()

    with (
        patch(
            "src.api.routes_approvals._get_approval",
            new_callable=AsyncMock,
            return_value=approval,
        ),
        patch("src.api.routes_approvals.AuditService") as mock_audit_cls,
        patch(
            "src.api.routes_approvals.create_graph_executor",
            new_callable=AsyncMock,
            return_value=mock_executor,
        ),
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

    assert result.status == "approved"
    # populate_run_steps should be called (steps need to exist for scheduler)
    mock_executor.populate_run_steps.assert_called_once()
    # execute_run should NOT be called — scheduler handles it
    mock_executor.execute_run.assert_not_called()


# ── Failure handling in scheduler-queue path ──────────────────────────


@pytest.mark.asyncio
async def test_step_level_queue_failure_marks_run_failed():
    """When the scheduler-queueing itself fails (e.g., DB error during
    source update), the run should be marked as failed."""
    from src.api.routes_approvals import approve_action

    approval = _make_approval(run_id="run_004", step_id="step_004")
    step = _make_step(step_id="step_004", run_id="run_004")
    run_obj = _make_run(run_id="run_004", status="awaiting_approval")
    run_after_fail = _make_run(run_id="run_004", status="awaiting_approval")

    call_count = 0

    async def fake_execute(stmt):
        nonlocal call_count
        call_count += 1
        if call_count == 1:
            return _FakeResult(run_obj)
        elif call_count == 2:
            return _FakeResult(step)
        elif call_count == 3:
            # re-fetch run fails
            raise RuntimeError("DB connection lost")
        elif call_count == 4:
            # _mark_run_failed_after_resume re-fetch
            return _FakeResult(run_after_fail)
        return _FakeResult(None)

    db = MagicMock()
    db.execute = AsyncMock(side_effect=fake_execute)
    db.commit = AsyncMock()
    db.flush = AsyncMock()
    db.rollback = AsyncMock()
    db.add = MagicMock()

    settings = make_mock_settings(qdrant_url="", redis_url="redis://localhost:6379/0")

    with (
        patch(
            "src.api.routes_approvals._get_approval",
            new_callable=AsyncMock,
            return_value=approval,
        ),
        patch("src.api.routes_approvals.AuditService") as mock_audit_cls,
        patch("src.api.routes_approvals.transition_run") as mock_transition_run,
        patch("src.api.routes_approvals.transition_step"),
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

    assert result.status == "approved"
    mock_transition_run.assert_called_once_with(run_after_fail, "failed")


# ── GraphExecutor: _get_ready_steps includes running steps ───────────


@pytest.mark.asyncio
async def test_get_ready_steps_includes_running_steps():
    """_get_ready_steps should return steps with status='running' so that
    resumed-from-approval steps actually get executed."""
    from src.services.graph_executor import GraphExecutor

    with patch("src.services.graph_executor.get_anthropic_client"):
        settings = make_mock_settings()
        db = AsyncMock()
        executor = GraphExecutor(settings, db)

    # Simulate three steps: one completed, one running (approved), one pending
    step_completed = MagicMock()
    step_completed.step_id = "s1"
    step_completed.status = "completed"
    step_completed.depends_on = []
    step_completed.created_at = datetime(2026, 4, 13, 1, 0, tzinfo=timezone.utc)

    step_running = MagicMock()
    step_running.step_id = "s2"
    step_running.status = "running"
    step_running.depends_on = []
    step_running.created_at = datetime(2026, 4, 13, 1, 1, tzinfo=timezone.utc)

    step_pending = MagicMock()
    step_pending.step_id = "s3"
    step_pending.status = "pending"
    step_pending.depends_on = ["s2"]
    step_pending.created_at = datetime(2026, 4, 13, 1, 2, tzinfo=timezone.utc)

    mock_result = MagicMock()
    mock_result.scalars.return_value.all.return_value = [
        step_completed,
        step_running,
        step_pending,
    ]
    db.execute = AsyncMock(return_value=mock_result)

    ready = await executor._get_ready_steps("run_001")

    # The running step should be returned (for execution after approval)
    assert step_running in ready
    # The pending step should NOT be returned (its dep s2 is not completed)
    assert step_pending not in ready
    # The completed step should NOT be returned
    assert step_completed not in ready


@pytest.mark.asyncio
async def test_get_ready_steps_promotes_pending_after_running_completes():
    """When a running step's dependent step is pending and the running step
    is already completed, the pending step should be promoted to ready."""
    from src.services.graph_executor import GraphExecutor

    with patch("src.services.graph_executor.get_anthropic_client"):
        settings = make_mock_settings()
        db = AsyncMock()
        executor = GraphExecutor(settings, db)

    step_completed = MagicMock()
    step_completed.step_id = "s1"
    step_completed.status = "completed"
    step_completed.depends_on = []
    step_completed.created_at = datetime(2026, 4, 13, 1, 0, tzinfo=timezone.utc)

    step_pending = MagicMock()
    step_pending.step_id = "s2"
    step_pending.status = "pending"
    step_pending.depends_on = ["s1"]
    step_pending.created_at = datetime(2026, 4, 13, 1, 1, tzinfo=timezone.utc)

    mock_result = MagicMock()
    mock_result.scalars.return_value.all.return_value = [step_completed, step_pending]
    db.execute = AsyncMock(return_value=mock_result)
    db.flush = AsyncMock()

    ready = await executor._get_ready_steps("run_001")

    assert step_pending in ready
    assert step_pending.status == "ready"
