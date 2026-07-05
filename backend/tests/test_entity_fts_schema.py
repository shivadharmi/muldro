"""The entities.search_vector GIN index must be declared on the model so the
migration and ORM agree (alembic-check clean). Inspected off table metadata."""

from src.models.entities import Entity


def test_search_vector_column_exists():
    assert "search_vector" in Entity.__table__.c.keys()


def test_gin_index_on_search_vector_is_declared():
    idx = {i.name: i for i in Entity.__table__.indexes}
    gin = idx.get("ix_entities_search_vector")
    assert gin is not None, "missing GIN index declaration on Entity.search_vector"
    cols = [c.name for c in gin.columns]
    assert cols == ["search_vector"], f"wrong columns: {cols}"
    assert gin.dialect_options["postgresql"]["using"] == "gin"
