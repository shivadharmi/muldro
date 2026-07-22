"""Cross-tenant isolation: the find_entity alias subquery must be workspace-scoped.
Compiled-SQL assertion against the production statement builder (no real DB needed;
Postgres-only column types block SQLite create_all)."""

from sqlalchemy.dialects import postgresql

from src.services.world_model import _find_by_alias_stmt, _find_entity_stmt


def _compile(stmt) -> str:
    return str(
        stmt.compile(dialect=postgresql.dialect(), compile_kwargs={"literal_binds": True})
    ).lower()


def test_find_entity_alias_subquery_is_workspace_scoped():
    sql = _compile(_find_entity_stmt("usr_1", "acme", "ws_A"))
    assert "entity_aliases.workspace_id = 'ws_a'" in sql, (
        "find_entity alias subquery is NOT workspace-scoped — cross-tenant leak"
    )


def test_find_by_alias_subquery_is_workspace_scoped():
    sql = _compile(_find_by_alias_stmt("usr_1", "acme@corp.com", "ws_A"))
    assert "entity_aliases.workspace_id = 'ws_a'" in sql, (
        "_find_by_name_or_alias alias subquery is NOT workspace-scoped — cross-tenant leak"
    )
