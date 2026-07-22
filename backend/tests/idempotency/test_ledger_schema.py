"""Schema invariants for the idempotency ledger — inspected off the SQLAlchemy
table metadata (no live DB needed)."""

from src.models.idempotency_ledger import IdempotencyLedgerEntry


def test_table_name():
    assert IdempotencyLedgerEntry.__tablename__ == "idempotency_ledger"


def test_workspace_scoped_unique_index_on_identity_key():
    idx = {i.name: i for i in IdempotencyLedgerEntry.__table__.indexes}
    uq = idx.get("ix_idempotency_ledger_ws_key")
    assert uq is not None, "missing (workspace_id, identity_key) index"
    assert uq.unique is True, "identity_key index must be UNIQUE (exactly-once gate)"
    cols = [c.name for c in uq.columns]
    assert cols == ["workspace_id", "identity_key"], f"wrong columns: {cols}"


def test_workspace_id_is_not_nullable():
    col = IdempotencyLedgerEntry.__table__.c.workspace_id
    assert col.nullable is False


def test_has_status_and_result_columns():
    cols = set(IdempotencyLedgerEntry.__table__.c.keys())
    assert {"status", "result_json", "capability", "identity_key", "provider_token"} <= cols
