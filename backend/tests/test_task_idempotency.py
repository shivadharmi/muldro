"""Tests for TaskRun idempotency."""

from src.models.task_graph import TaskRun


def test_task_run_has_idempotency_key_field():
    """TaskRun model must have an idempotency_key column."""
    run = TaskRun(
        run_id="run_test",
        user_id="usr_test",
        workspace_id="ws_test",
        status="pending",
        idempotency_key="plan_abc:create_task",
    )
    assert run.idempotency_key == "plan_abc:create_task"


def test_task_run_idempotency_key_nullable():
    """idempotency_key should be nullable for backward compatibility."""
    run = TaskRun(
        run_id="run_test2",
        user_id="usr_test",
        workspace_id="ws_test",
        status="pending",
    )
    assert run.idempotency_key is None
