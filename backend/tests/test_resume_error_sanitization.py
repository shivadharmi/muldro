"""Regression tests for the resume-failure error leak found in adversarial review.

`run.error` is served verbatim by the history API, so the failed-resume paths
must store only a safe message + code — never the raw exception string.
"""

from unittest.mock import AsyncMock, MagicMock

from src.api.routes_approvals import _mark_run_failed_after_resume
from src.models.task_graph import TaskRun

SECRET = "postgres://admin:hunter2@db.internal:5432/jarvis"


async def test_mark_run_failed_after_resume_does_not_leak_exception():
    run = TaskRun(
        run_id="run_test",
        user_id="user_test",
        workspace_id="ws_test",
        status="running",
        source="plan",
    )
    db = AsyncMock()
    result = MagicMock()
    result.scalar_one_or_none.return_value = run
    db.execute.return_value = result

    exc = ValueError(f"connection to {SECRET} failed")
    await _mark_run_failed_after_resume(db, "run_test", exc)

    assert run.status == "failed"
    # The raw exception / secret must NOT be in the client-served run.error.
    assert SECRET not in str(run.error)
    assert "connection to" not in str(run.error)
    # It must carry the safe envelope fields.
    assert run.error["error_code"]
    assert run.error["correlation_id"]
    assert run.error["resume_failed"]  # safe message
    db.commit.assert_awaited()
