"""Writers dual-write: the old column is still set (rollback safety) AND RunDetailStore
is called (Step 5, D-C4). Verified by source inspection of the three write sites."""

import inspect

import src.services.governor as gov
import src.services.graph_executor as ge
import src.services.step_graph_store as sgs


def test_governor_dual_writes_policy_decision():
    src = inspect.getsource(gov)
    assert "RunDetailStore" in src
    assert "upsert_policy_decision" in src
    assert "policy_decision={" in src  # old column still written (transitional)


def test_step_graph_store_dual_writes_context_pack():
    src = inspect.getsource(sgs)
    assert "RunDetailStore" in src
    assert "upsert_context_pack" in src
    assert "run.context_pack_json = pack.model_dump()" in src  # old column still written


def test_graph_executor_refresh_writes_context_pack():
    src = inspect.getsource(ge)
    assert "upsert_context_pack" in src
