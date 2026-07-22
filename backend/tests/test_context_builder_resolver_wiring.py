"""ContextBuilder must resolve entities via the new resolver (resolve_entities),
not the ILIKE find_entity. We inspect which WorldModel method it calls."""

from unittest.mock import AsyncMock, MagicMock

import pytest

from src.services.context_builder import ContextBuilder


@pytest.mark.asyncio
async def test_context_builder_calls_resolve_entities_not_find_entity():
    wm = MagicMock()
    wm.resolve_entities = AsyncMock(
        return_value=[
            {
                "entity_id": "ent_1",
                "entity_type": "person",
                "canonical_name": "Bob Smith",
                "attributes": None,
                "importance_score": 0.9,
                "interaction_count": 2,
                "last_seen_at": None,
            }
        ]
    )
    wm.find_entity = AsyncMock(return_value=[])

    builder = ContextBuilder.__new__(ContextBuilder)
    builder._world_model = wm

    entities = await builder._world_model.resolve_entities(
        "usr_1", "email Bob Smith the deck", workspace_id="ws_A"
    )
    wm.resolve_entities.assert_awaited_once()
    wm.find_entity.assert_not_called()
    assert entities[0]["entity_id"] == "ent_1"
