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


def test_task_run_idempotency_index_is_workspace_scoped():
    """The task-run idempotency unique index must be composite
    (workspace_id, idempotency_key), not global on idempotency_key alone.

    TaskRun keys carry no workspace component, so a global unique index would
    let one workspace's run block another's on a shared key. Scoping the index
    to the workspace prevents cross-tenant collisions before any code begins
    populating the key. Mirrors the NormalizedEvent fix.
    """
    idx_cols = {idx.name: [c.name for c in idx.columns] for idx in TaskRun.__table__.indexes}
    assert idx_cols.get("ix_task_runs_idempotency") == ["workspace_id", "idempotency_key"]
