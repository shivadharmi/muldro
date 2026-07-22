"""The entity Qdrant payload must include workspace_id so workspace-scoped vector
search (find_similar/search filters) can match. Pure — no Qdrant, no DB."""

from src.services.world_model import _entity_vector_payload


def test_payload_includes_workspace_id():
    p = _entity_vector_payload("person", "Bob", "usr_1", "ws_A")
    assert p == {
        "entity_type": "person",
        "canonical_name": "Bob",
        "user_id": "usr_1",
        "workspace_id": "ws_A",
    }


def test_empty_workspace_id_is_omitted():
    p = _entity_vector_payload("person", "Bob", "usr_1", "")
    assert "workspace_id" not in p
    assert p == {"entity_type": "person", "canonical_name": "Bob", "user_id": "usr_1"}
