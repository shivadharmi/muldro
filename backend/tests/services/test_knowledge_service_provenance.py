from types import SimpleNamespace

from src.services.knowledge_service import _entity_sources


def test_entity_sources_resolves_from_populated_source_refs():
    entity = SimpleNamespace(
        source_refs=[
            {"source": "gmail", "event_id": "evt_1"},
            {"source": "gmail", "event_id": "evt_2"},
            {"source": "slack", "event_id": "evt_3"},
        ]
    )
    assert _entity_sources(entity) == ["gmail", "slack"]  # dedup, order-preserving


def test_entity_sources_empty_when_null():
    assert _entity_sources(SimpleNamespace(source_refs=None)) == []
