"""Tests for WorldModel — entity extraction and upsert."""

import json
from unittest.mock import AsyncMock, MagicMock, patch

import pytest

from src.services.world_model import WorldModel
from tests.conftest import TEST_USER_ID, make_mock_settings


@pytest.fixture
def settings():
    return make_mock_settings()


@pytest.fixture
def mock_db():
    db = MagicMock()
    db.add = MagicMock()
    db.commit = AsyncMock()
    db.flush = AsyncMock()

    # Default: entity not found (for upsert)
    result_mock = MagicMock()
    result_mock.scalar_one_or_none.return_value = None
    result_mock.scalars.return_value.all.return_value = []
    db.execute = AsyncMock(return_value=result_mock)
    return db


@pytest.mark.asyncio
async def test_upsert_creates_new_entity(settings, mock_db):
    """Upserting a new entity should create it with an ent_ ID."""
    wm = WorldModel(settings=settings, db=mock_db)
    entity_id = await wm.upsert_entity(
        user_id=TEST_USER_ID,
        entity_type="person",
        canonical_name="John Doe",
        aliases=["john@fund.com"],
    )

    assert entity_id.startswith("ent_")
    # Should add entity + alias
    assert mock_db.add.call_count == 2
    mock_db.commit.assert_called_once()


@pytest.mark.asyncio
async def test_upsert_updates_existing_entity(settings, mock_db):
    """Upserting an existing entity should merge attributes."""
    # Simulate existing entity found
    existing_entity = MagicMock()
    existing_entity.entity_id = "ent_existing"
    existing_entity.attributes = {"role": "investor"}
    existing_entity.interaction_count = 1
    existing_entity.importance_score = 0.5

    result_mock = MagicMock()
    result_mock.scalar_one_or_none.return_value = existing_entity
    # For EntityFactStore.current_fact (per-attribute fact recording): no current fact
    no_fact = MagicMock()
    no_fact.scalar_one_or_none.return_value = None
    # For _add_aliases: return empty existing aliases
    alias_result = MagicMock()
    alias_result.scalars.return_value.all.return_value = []
    mock_db.execute = AsyncMock(side_effect=[result_mock, no_fact, alias_result])

    wm = WorldModel(settings=settings, db=mock_db)
    entity_id = await wm.upsert_entity(
        user_id=TEST_USER_ID,
        entity_type="person",
        canonical_name="John Doe",
        attributes={"company": "BigFund"},
        aliases=["john@fund.com"],
    )

    assert entity_id == "ent_existing"
    # Attributes should be merged
    assert existing_entity.attributes == {"role": "investor", "company": "BigFund"}


@patch("src.services.world_model.complete_text")
@pytest.mark.asyncio
async def test_extract_from_event_calls_claude(mock_complete, settings, mock_db):
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

    mock_complete.return_value = json.dumps(extraction_result)

    # First execute returns the event, subsequent return no-entity-found for upsert
    event_result = MagicMock()
    event_result.scalar_one_or_none.return_value = mock_event

    no_result = MagicMock()
    no_result.scalar_one_or_none.return_value = None
    no_result.scalars.return_value.all.return_value = []

    mock_db.execute = AsyncMock(side_effect=[event_result, no_result, no_result])

    wm = WorldModel(settings=settings, db=mock_db)
    entity_ids = await wm.extract_from_event("evt_001", TEST_USER_ID)

    assert len(entity_ids) == 1
    assert entity_ids[0].startswith("ent_")
    mock_complete.assert_awaited_once()


@patch("src.services.world_model.complete_text")
@pytest.mark.asyncio
async def test_extract_from_event_tolerates_bare_list(mock_complete, settings, mock_db):
    """The LLM sometimes returns a bare entities array instead of
    {"entities": [...]}; extraction must coerce it, not crash on .get()."""
    mock_event = MagicMock()
    mock_event.event_type = "email_received"
    mock_event.source = "gmail"
    mock_event.title = "Investor follow-up"
    mock_event.summary = "John from BigFund"
    mock_event.actor_entities = []

    # Bare array — the exact shape that raised AttributeError: 'list' has no 'get'.
    bare_list = [{"entity_type": "person", "canonical_name": "John Doe"}]
    mock_complete.return_value = json.dumps(bare_list)

    event_result = MagicMock()
    event_result.scalar_one_or_none.return_value = mock_event
    no_result = MagicMock()
    no_result.scalar_one_or_none.return_value = None
    no_result.scalars.return_value.all.return_value = []
    mock_db.execute = AsyncMock(side_effect=[event_result, no_result, no_result])

    wm = WorldModel(settings=settings, db=mock_db)
    entity_ids = await wm.extract_from_event("evt_001", TEST_USER_ID)

    assert len(entity_ids) == 1  # the bare-list entity was processed, no crash


def test_guess_alias_type():
    """Should correctly identify email, handle, and name aliases."""
    assert WorldModel._guess_alias_type("john@fund.com") == "email"
    assert WorldModel._guess_alias_type("@johndoe") == "handle"
    assert WorldModel._guess_alias_type("John Doe") == "name"
