"""The ledger lookup must be workspace-scoped (no cross-tenant identity match).
Compiled-SQL assertion against the production statement builder (no DB)."""

from sqlalchemy.dialects import postgresql

from src.services.idempotency.ledger import _ledger_lookup_stmt


def _compile(stmt) -> str:
    return str(
        stmt.compile(dialect=postgresql.dialect(), compile_kwargs={"literal_binds": True})
    ).lower()


def test_lookup_is_workspace_and_identity_scoped():
    sql = _compile(_ledger_lookup_stmt("ws_A", "run_1:step_1:email.send:sem:abc"))
    assert "idempotency_ledger.workspace_id = 'ws_a'" in sql
    assert "idempotency_ledger.identity_key = 'run_1:step_1:email.send:sem:abc'" in sql
