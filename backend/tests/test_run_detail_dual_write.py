"""Post-contract writes (Step 5, D-C4): the old task_runs columns are dropped, so the
write sites persist ONLY to RunDetailStore. Verified by source inspection of the three
write sites — the store call remains, the old-column assignment is gone."""

import inspect

import src.services.governor as gov
import src.services.graph_executor as ge
import src.services.step_graph_store as sgs


def test_governor_writes_policy_decision_to_store_only():
    src = inspect.getsource(gov)
    assert "RunDetailStore" in src
    assert "upsert_policy_decision" in src
    assert "policy_decision={" not in src  # old column no longer written


def test_step_graph_store_writes_context_pack_to_store_only():
    src = inspect.getsource(sgs)
    assert "RunDetailStore" in src
    assert "upsert_context_pack" in src
    assert "run.context_pack_json" not in src  # old column no longer written


def test_graph_executor_refresh_writes_context_pack_to_store_only():
    src = inspect.getsource(ge)
    assert "upsert_context_pack" in src
    assert "run.context_pack_json" not in src  # old column no longer written
