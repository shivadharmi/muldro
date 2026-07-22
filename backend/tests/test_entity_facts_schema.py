"""The entity_facts model must declare its columns + indexes so the migration and
ORM agree (alembic-check clean). Inspected off table metadata — no DB needed."""

from src.models.entities import EntityFact


def test_entity_facts_table_name():
    assert EntityFact.__tablename__ == "entity_facts"


def test_entity_facts_columns_exist():
    cols = set(EntityFact.__table__.c.keys())
    expected = {
        "fact_id",
        "entity_id",
        "workspace_id",
        "user_id",
        "attr_key",
        "attr_value",
        "confidence",
        "corroboration_count",
        "provenance",
        "valid_from",
        "valid_to",
        "superseded_by",
        "created_at",
        "updated_at",
    }
    assert expected <= cols, f"missing: {expected - cols}"


def test_entity_facts_lookup_index_declared():
    idx = {i.name: [c.name for c in i.columns] for i in EntityFact.__table__.indexes}
    assert idx.get("ix_entity_facts_lookup") == ["entity_id", "attr_key", "valid_to"]
    assert "ix_entity_facts_ws" in idx


def test_valid_to_is_nullable_and_valid_from_not():
    c = EntityFact.__table__.c
    assert c.valid_to.nullable is True
    assert c.valid_from.nullable is False
