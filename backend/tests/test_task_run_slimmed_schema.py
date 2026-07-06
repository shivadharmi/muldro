"""Contract phase (Step 5, D-C4): context_pack_json + policy_decision are removed from
the TaskRun row; the cost rollup stays (D-C5)."""

from src.models.task_graph import TaskRun


def test_old_columns_dropped():
    cols = set(TaskRun.__table__.c.keys())
    assert "context_pack_json" not in cols
    assert "policy_decision" not in cols


def test_cost_rollup_retained():
    cols = set(TaskRun.__table__.c.keys())
    assert {"input_tokens", "output_tokens", "cost_usd"} <= cols
