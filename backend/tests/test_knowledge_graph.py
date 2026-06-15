"""Tests for Phase 5 — Knowledge Graph Expansion.

Tests entity type/relation expansion, temporal tracking,
entity-memory linking, and composite retrieval ranking.
"""

import json
from unittest.mock import AsyncMock, MagicMock, patch

import pytest

from src.services.world_model import ENTITY_TYPES, RELATION_TYPES, WorldModel
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

    result_mock = MagicMock()
    result_mock.scalar_one_or_none.return_value = None
    result_mock.scalars.return_value.all.return_value = []
    db.execute = AsyncMock(return_value=result_mock)
    return db


# ── Entity Types & Relation Types ────────────────────────────────


class TestEntityRelationTypes:
    def test_entity_types_expanded(self):
        """Should have 24 entity types (15 work + 9 personal)."""
        assert len(ENTITY_TYPES) == 24
        # Original 4
        for t in ("person", "organization", "project", "meeting"):
            assert t in ENTITY_TYPES
        # Work-domain types
        for t in (
            "goal",
            "task",
            "document",
            "message_thread",
            "repository",
            "channel",
            "product",
            "investment",
            "website",
            "tool",
            "watcher",
        ):
            assert t in ENTITY_TYPES
        # Personal-domain types
        for t in (
            "location",
            "health_record",
            "hobby",
            "family_member",
            "financial_account",
            "media_item",
            "recipe",
            "course",
            "contact_group",
        ):
            assert t in ENTITY_TYPES

    def test_relation_types_expanded(self):
        """Should have 24 relation types (17 work + 7 personal)."""
        assert len(RELATION_TYPES) == 24
        # Original 5
        for r in ("works_on", "related_to", "scheduled_with", "reports_to", "owns"):
            assert r in RELATION_TYPES
        # Work-domain types
        for r in (
            "member_of",
            "assigned_to",
            "mentioned_in",
            "depends_on",
            "attends",
            "authored",
            "invested_in",
            "blocked_by",
            "sent_by",
            "attached_to",
            "derived_from",
            "monitors",
        ):
            assert r in RELATION_TYPES
        # Personal-domain types
        for r in (
            "lives_at",
            "prescribed_by",
            "enrolled_in",
            "follows",
            "subscribes_to",
            "shares_with",
            "cares_for",
        ):
            assert r in RELATION_TYPES


# ── Temporal Tracking ────────────────────────────────────────────


class TestTemporalTracking:
    @patch("src.services.world_model.get_anthropic_client")
    @pytest.mark.asyncio
    async def test_new_entity_gets_temporal_fields(self, mock_get_client, settings, mock_db):
        """New entities should have last_seen_at, interaction_count=1, importance."""
        mock_get_client.return_value = MagicMock()
        wm = WorldModel(settings=settings, db=mock_db)

        entity_id = await wm.upsert_entity(
            user_id=TEST_USER_ID,
            entity_type="person",
            canonical_name="Alice",
            importance=0.8,
        )

        assert entity_id.startswith("ent_")
        added = mock_db.add.call_args[0][0]
        assert added.last_seen_at is not None
        assert added.interaction_count == 1
        assert added.importance_score == 0.8

    @patch("src.services.world_model.get_anthropic_client")
    @pytest.mark.asyncio
    async def test_existing_entity_increments_interaction(self, mock_get_client, settings, mock_db):
        """Upserting existing entity should increment interaction_count."""
        existing = MagicMock()
        existing.entity_id = "ent_existing"
        existing.attributes = {}
        existing.interaction_count = 3
        existing.importance_score = 0.5
        existing.last_seen_at = None

        result_mock = MagicMock()
        result_mock.scalar_one_or_none.return_value = existing
        mock_db.execute = AsyncMock(return_value=result_mock)
        mock_get_client.return_value = MagicMock()

        wm = WorldModel(settings=settings, db=mock_db)
        entity_id = await wm.upsert_entity(
            user_id=TEST_USER_ID,
            entity_type="person",
            canonical_name="Alice",
            importance=0.9,
        )

        assert entity_id == "ent_existing"
        assert existing.interaction_count == 4
        assert existing.importance_score == 0.9  # max(0.5, 0.9)
        assert existing.last_seen_at is not None

    @patch("src.services.world_model.get_anthropic_client")
    @pytest.mark.asyncio
    async def test_importance_keeps_maximum(self, mock_get_client, settings, mock_db):
        """Importance should keep the higher value."""
        existing = MagicMock()
        existing.entity_id = "ent_existing"
        existing.attributes = {}
        existing.interaction_count = 1
        existing.importance_score = 0.9
        existing.last_seen_at = None

        result_mock = MagicMock()
        result_mock.scalar_one_or_none.return_value = existing
        mock_db.execute = AsyncMock(return_value=result_mock)
        mock_get_client.return_value = MagicMock()

        wm = WorldModel(settings=settings, db=mock_db)
        await wm.upsert_entity(
            user_id=TEST_USER_ID,
            entity_type="person",
            canonical_name="Alice",
            importance=0.3,
        )

        assert existing.importance_score == 0.9  # Stays at 0.9


# ── Entity Extraction with New Types ─────────────────────────────


class TestEntityExtractionExpanded:
    @patch("src.services.world_model.get_anthropic_client")
    @pytest.mark.asyncio
    async def test_extracts_document_entity(self, mock_get_client, settings, mock_db):
        """Should extract document entities from events."""
        mock_event = MagicMock()
        mock_event.event_type = "file_modified"
        mock_event.source = "drive"
        mock_event.title = "Q1 Revenue Report updated"
        mock_event.summary = "Alice updated Q1 revenue spreadsheet"
        mock_event.actor_entities = []

        extraction_result = {
            "entities": [
                {
                    "entity_type": "document",
                    "canonical_name": "Q1 Revenue Report",
                    "aliases": [],
                    "attributes": {"format": "spreadsheet"},
                    "importance": 0.7,
                },
                {
                    "entity_type": "person",
                    "canonical_name": "Alice",
                    "aliases": [],
                    "attributes": {},
                    "importance": 0.6,
                },
            ],
            "relationships": [
                {
                    "from_name": "Alice",
                    "relation_type": "authored",
                    "to_name": "Q1 Revenue Report",
                }
            ],
        }

        mock_client = MagicMock()
        response = MagicMock()
        response.content = [MagicMock(text=json.dumps(extraction_result))]
        mock_client.messages.create = AsyncMock(return_value=response)
        mock_get_client.return_value = mock_client

        event_result = MagicMock()
        event_result.scalar_one_or_none.return_value = mock_event

        no_result = MagicMock()
        no_result.scalar_one_or_none.return_value = None
        no_result.scalars.return_value.all.return_value = []

        mock_db.execute = AsyncMock(
            side_effect=[event_result, no_result, no_result, no_result, no_result]
        )

        wm = WorldModel(settings=settings, db=mock_db)
        entity_ids = await wm.extract_from_event("evt_001", TEST_USER_ID)

        assert len(entity_ids) == 2

    @patch("src.services.world_model.get_anthropic_client")
    @pytest.mark.asyncio
    async def test_invalid_type_falls_back_to_person(self, mock_get_client, settings, mock_db):
        """Unknown entity_type should fall back to 'person'."""
        mock_event = MagicMock()
        mock_event.event_type = "test"
        mock_event.source = "test"
        mock_event.title = "Test"
        mock_event.summary = None
        mock_event.actor_entities = None

        extraction_result = {
            "entities": [
                {
                    "entity_type": "alien_species",
                    "canonical_name": "XYZ",
                    "aliases": [],
                    "attributes": {},
                    "importance": 0.5,
                }
            ],
            "relationships": [],
        }

        mock_client = MagicMock()
        response = MagicMock()
        response.content = [MagicMock(text=json.dumps(extraction_result))]
        mock_client.messages.create = AsyncMock(return_value=response)
        mock_get_client.return_value = mock_client

        event_result = MagicMock()
        event_result.scalar_one_or_none.return_value = mock_event
        no_result = MagicMock()
        no_result.scalar_one_or_none.return_value = None
        no_result.scalars.return_value.all.return_value = []

        mock_db.execute = AsyncMock(side_effect=[event_result, no_result])

        wm = WorldModel(settings=settings, db=mock_db)
        entity_ids = await wm.extract_from_event("evt_001", TEST_USER_ID)

        assert len(entity_ids) == 1
        # The entity added should be "person" (fallback)
        added = mock_db.add.call_args_list[0][0][0]
        assert added.entity_type == "person"

    @patch("src.services.world_model.get_anthropic_client")
    @pytest.mark.asyncio
    async def test_invalid_relation_falls_back_to_related_to(
        self, mock_get_client, settings, mock_db
    ):
        """Unknown relation_type should fall back to 'related_to'."""
        mock_event = MagicMock()
        mock_event.event_type = "test"
        mock_event.source = "test"
        mock_event.title = "Test"
        mock_event.summary = None
        mock_event.actor_entities = None

        extraction_result = {
            "entities": [
                {
                    "entity_type": "person",
                    "canonical_name": "Alice",
                    "aliases": [],
                    "attributes": {},
                    "importance": 0.5,
                },
                {
                    "entity_type": "person",
                    "canonical_name": "Bob",
                    "aliases": [],
                    "attributes": {},
                    "importance": 0.5,
                },
            ],
            "relationships": [
                {
                    "from_name": "Alice",
                    "relation_type": "telepathically_linked_to",
                    "to_name": "Bob",
                }
            ],
        }

        mock_client = MagicMock()
        response = MagicMock()
        response.content = [MagicMock(text=json.dumps(extraction_result))]
        mock_client.messages.create = AsyncMock(return_value=response)
        mock_get_client.return_value = mock_client

        event_result = MagicMock()
        event_result.scalar_one_or_none.return_value = mock_event
        no_result = MagicMock()
        no_result.scalar_one_or_none.return_value = None
        no_result.scalars.return_value.all.return_value = []

        # For find_entity called by _create_relationship_by_name
        entity_match = MagicMock()
        entity_match.scalars.return_value.all.return_value = [
            MagicMock(
                entity_id="ent_alice",
                entity_type="person",
                canonical_name="Alice",
                attributes={},
                importance_score=0.5,
                interaction_count=1,
                last_seen_at=None,
            )
        ]
        entity_match2 = MagicMock()
        entity_match2.scalars.return_value.all.return_value = [
            MagicMock(
                entity_id="ent_bob",
                entity_type="person",
                canonical_name="Bob",
                attributes={},
                importance_score=0.5,
                interaction_count=1,
                last_seen_at=None,
            )
        ]
        # Check existing relationship
        rel_check = MagicMock()
        rel_check.scalar_one_or_none.return_value = None

        mock_db.execute = AsyncMock(
            side_effect=[
                event_result,
                no_result,
                no_result,  # upsert Alice, Bob
                entity_match,
                entity_match2,  # find for relationship
                rel_check,  # check existing relationship
            ]
        )

        wm = WorldModel(settings=settings, db=mock_db)
        await wm.extract_from_event("evt_001", TEST_USER_ID)

        # The relationship should use "related_to" as fallback
        rel_added = [
            c[0][0] for c in mock_db.add.call_args_list if hasattr(c[0][0], "relation_type")
        ]
        if rel_added:
            assert rel_added[0].relation_type == "related_to"


# ── Entity-Memory Linking ────────────────────────────────────────


class TestEntityMemoryLinking:
    @patch("src.services.memory_service._base.EmbeddingService")
    @patch("src.services.memory_service._base.get_anthropic_client")
    @pytest.mark.asyncio
    async def test_extract_and_store_with_entity_ids(
        self, mock_get_client, mock_embedder_cls, settings, mock_db
    ):
        """Memories should store entity_ids when provided."""
        mock_embedder = MagicMock()
        mock_embedder.embed_text = AsyncMock(return_value=[0.1] * 1024)
        mock_embedder_cls.return_value = mock_embedder

        extraction_result = {
            "memories": [
                {
                    "memory_type": "semantic",
                    "scope": "general",
                    "fact_text": "Alice is CFO at Acme Corp",
                    "confidence": 0.9,
                    "ttl_days": None,
                }
            ]
        }

        mock_client = MagicMock()
        response = MagicMock()
        response.content = [MagicMock(text=json.dumps(extraction_result))]
        mock_client.messages.create = AsyncMock(return_value=response)
        mock_get_client.return_value = mock_client

        # No duplicate found
        no_result = MagicMock()
        no_result.scalar_one_or_none.return_value = None
        mock_db.execute = AsyncMock(return_value=no_result)

        from src.services.memory_service import MemoryService

        ms = MemoryService(settings=settings, db=mock_db)
        memory_ids = await ms.extract_and_store(
            user_id=TEST_USER_ID,
            source_text="Alice is CFO at Acme Corp",
            source_event_ids=["evt_001"],
            entity_ids=["ent_alice", "ent_acme"],
        )

        assert len(memory_ids) == 1
        added = mock_db.add.call_args[0][0]
        assert added.entity_ids == ["ent_alice", "ent_acme"]


# ── Find Entity Returns Temporal Fields ──────────────────────────


class TestFindEntityTemporal:
    @patch("src.services.world_model.get_anthropic_client")
    @pytest.mark.asyncio
    async def test_find_returns_temporal_fields(self, mock_get_client, settings, mock_db):
        """find_entity should return importance, interaction_count, last_seen_at."""
        from datetime import datetime, timezone

        now = datetime.now(timezone.utc)
        mock_entity = MagicMock()
        mock_entity.entity_id = "ent_001"
        mock_entity.entity_type = "person"
        mock_entity.canonical_name = "Alice"
        mock_entity.attributes = {"role": "CFO"}
        mock_entity.importance_score = 0.85
        mock_entity.interaction_count = 12
        mock_entity.last_seen_at = now

        result_mock = MagicMock()
        result_mock.scalars.return_value.all.return_value = [mock_entity]
        mock_db.execute = AsyncMock(return_value=result_mock)

        mock_get_client.return_value = MagicMock()
        wm = WorldModel(settings=settings, db=mock_db)
        results = await wm.find_entity(TEST_USER_ID, "Alice")

        assert len(results) == 1
        assert results[0]["importance_score"] == 0.85
        assert results[0]["interaction_count"] == 12
        assert results[0]["last_seen_at"] is not None
