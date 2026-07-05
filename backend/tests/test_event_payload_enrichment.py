"""The step_completed and run_completed events carry the resulting status in their
payload so a projection can be rebuilt from the event log alone (Step 5 §4.8, D-B1).
Verified by inspecting the emit call sites' source (no live run needed)."""

import inspect

import src.services.dag_runner as dr


def test_run_completed_emit_includes_status_and_durable():
    src = inspect.getsource(dr)
    # The run_completed emit must add "status": run.status and durable=True.
    # Comma-anchored: matches the emit's first arg, NOT any earlier occurrence.
    idx = src.index('"run_completed",')
    window = src[idx : idx + 400]
    assert '"status": run.status' in window
    assert "durable=True" in window


def test_step_completed_emit_includes_status_and_durable():
    src = inspect.getsource(dr)
    # Comma-anchored: matches the emit's first arg, NOT the earlier checkpoint
    # call self._store.checkpoint(run, step.step_id, "step_completed").
    idx = src.index('"step_completed",')
    window = src[idx : idx + 400]
    assert '"status": status' in window
    assert "durable=True" in window
