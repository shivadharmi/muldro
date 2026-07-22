"""TaskRunDetail is the 1:1 side table (run_id PK/FK CASCADE) owning the extracted
policy_decision (durable) + context_pack/context_pack_expires_at (TTL'd). Step 5, D-C1."""

from src.models.task_graph import TaskRunDetail


def test_table_name():
    assert TaskRunDetail.__tablename__ == "task_run_details"


def test_columns_exist():
    cols = set(TaskRunDetail.__table__.c.keys())
    expected = {
        "run_id",
        "workspace_id",
        "policy_decision",
        "context_pack",
        "context_pack_expires_at",
        "created_at",
        "updated_at",
    }
    assert expected <= cols, f"missing: {expected - cols}"


def test_run_id_is_primary_key():
    pk = [c.name for c in TaskRunDetail.__table__.primary_key.columns]
    assert pk == ["run_id"]


def test_context_pack_nullable_expiry_nullable():
    c = TaskRunDetail.__table__.c
    assert c.context_pack.nullable is True
    assert c.context_pack_expires_at.nullable is True
