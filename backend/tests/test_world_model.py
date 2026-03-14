"""Tests for WorldModel — entity extraction and upsert."""

import json
from unittest.mock import AsyncMock, MagicMock, patch

import pytest

from src.services.world_model import WorldModel
from tests.conftest import make_mock_settings


@pytest.fixture
def settings():
    return make_mock_settings()


@pytest.fixture
def mock_db():
    db = MagicMock()
    db.add = MagicMock()
    db.commit = AsyncMock()

    # Default: entity not found (for upsert)
    result_mock = MagicMock()
    result_mock.scalar_one_or_none.return_value = None
    result_mock.scalars.return_value.all.return_value = []
    db.execute = AsyncMock(return_value=result_mock)
    return db


@patch("src.services.world_model.get_anthropic_client")
@pytest.mark.asyncio
async def test_upsert_creates_new_entity(mock_get_client, settings, mock_db):
    """Upserting a new entity should create it with an ent_ ID."""
    mock_get_client.return_value = MagicMock()

    wm = WorldModel(settings=settings, db=mock_db)
    entity_id = await wm.upsert_entity(
        user_id="usr_default",
        entity_type="person",
        canonical_name="John Doe",
        aliases=["john@fund.com"],
    )

    assert entity_id.startswith("ent_")
    # Should add entity + alias
    assert mock_db.add.call_count == 2
    mock_db.commit.assert_called_once()


@patch("src.services.world_model.get_anthropic_client")
@pytest.mark.asyncio
async def test_upsert_updates_existing_entity(mock_get_client, settings, mock_db):
    """Upserting an existing entity should merge attributes."""
    # Simulate existing entity found
    existing_entity = MagicMock()
    existing_entity.entity_id = "ent_existing"
    existing_entity.attributes = {"role": "investor"}

    result_mock = MagicMock()
    result_mock.scalar_one_or_none.return_value = existing_entity
    # For _add_aliases: return empty existing aliases
    alias_result = MagicMock()
    alias_result.scalars.return_value.all.return_value = []
    mock_db.execute = AsyncMock(side_effect=[result_mock, alias_result])

    mock_get_client.return_value = MagicMock()

    wm = WorldModel(settings=settings, db=mock_db)
    entity_id = await wm.upsert_entity(
        user_id="usr_default",
        entity_type="person",
        canonical_name="John Doe",
        attributes={"company": "BigFund"},
        aliases=["john@fund.com"],
    )

    assert entity_id == "ent_existing"
    # Attributes should be merged
    assert existing_entity.attributes == {"role": "investor", "company": "BigFund"}


@patch("src.services.world_model.get_anthropic_client")
@pytest.mark.asyncio
async def test_extract_from_event_calls_claude(mock_get_client, settings, mock_db):
    """extract_from_event should call Claude and create entities."""
    # Mock the event fetch
    mock_event = MagicMock()
    mock_event.event_type = "email_received"
    mock_event.source = "gmail"
    mock_event.title = "Investor follow-up"
    mock_event.summary = "John from BigFund wants to discuss the deck"
    mock_event.actor_entities = [{"type": "person", "email": "john@fund.com"}]

    extraction_result = {
        "entities": [
            {
                "entity_type": "person",
                "canonical_name": "John Doe",
                "aliases": ["john@fund.com"],
                "attributes": {"company": "BigFund"},
            }
        ],
        "relationships": [],
    }

    mock_client = MagicMock()
    response = MagicMock()
    response.content = [MagicMock(text=json.dumps(extraction_result))]
    mock_client.messages.create = AsyncMock(return_value=response)
    mock_get_client.return_value = mock_client

    # First execute returns the event, subsequent return no-entity-found for upsert
    event_result = MagicMock()
    event_result.scalar_one_or_none.return_value = mock_event

    no_result = MagicMock()
    no_result.scalar_one_or_none.return_value = None
    no_result.scalars.return_value.all.return_value = []

    mock_db.execute = AsyncMock(side_effect=[event_result, no_result, no_result])

    wm = WorldModel(settings=settings, db=mock_db)
    entity_ids = await wm.extract_from_event("evt_001", "usr_default")

    assert len(entity_ids) == 1
    assert entity_ids[0].startswith("ent_")
    mock_client.messages.create.assert_called_once()


def test_guess_alias_type():
    """Should correctly identify email, handle, and name aliases."""
    assert WorldModel._guess_alias_type("john@fund.com") == "email"
    assert WorldModel._guess_alias_type("@johndoe") == "handle"
    assert WorldModel._guess_alias_type("John Doe") == "name"
