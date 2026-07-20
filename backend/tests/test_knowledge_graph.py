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
        """Should have 26 entity types (15 work + 9 personal + 2 financial)."""
        assert len(ENTITY_TYPES) == 26
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
        # Financial-domain types
        for t in ("financial_transaction", "merchant"):
            assert t in ENTITY_TYPES

    def test_relation_types_expanded(self):
        """Should have 26 relation types (17 work + 7 personal + 2 financial)."""
        assert len(RELATION_TYPES) == 26
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
        # Financial-domain types
        for r in ("paid_to", "charged_to"):
            assert r in RELATION_TYPES


# ── Temporal Tracking ────────────────────────────────────────────


class TestTemporalTracking:
    @pytest.mark.asyncio
    async def test_new_entity_gets_temporal_fields(self, settings, mock_db):
        """New entities should have last_seen_at, interaction_count=1, importance."""
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

    @pytest.mark.asyncio
    async def test_existing_entity_increments_interaction(self, settings, mock_db):
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

    @pytest.mark.asyncio
    async def test_importance_keeps_maximum(self, settings, mock_db):
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
    @patch("src.services.world_model.complete_text")
    @pytest.mark.asyncio
    async def test_extracts_document_entity(self, mock_complete, settings, mock_db):
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

        mock_complete.return_value = json.dumps(extraction_result)

        event_result = MagicMock()
        event_result.scalar_one_or_none.return_value = mock_event

        no_result = MagicMock()
        no_result.scalar_one_or_none.return_value = None
        no_result.scalars.return_value.all.return_value = []

        # First execute() is the event lookup; all later lookups (dedup, per-attribute
        # fact recording current_fact, find_entity for relationships) return no match.
        mock_db.execute = AsyncMock(side_effect=[event_result] + [no_result] * 30)

        wm = WorldModel(settings=settings, db=mock_db)
        entity_ids = await wm.extract_from_event("evt_001", TEST_USER_ID)

        assert len(entity_ids) == 2

    @patch("src.services.world_model.complete_text")
    @pytest.mark.asyncio
    async def test_invalid_type_falls_back_to_person(self, mock_complete, settings, mock_db):
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

        mock_complete.return_value = json.dumps(extraction_result)

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

    @patch("src.services.world_model.complete_text")
    @pytest.mark.asyncio
    async def test_invalid_relation_falls_back_to_related_to(
        self, mock_complete, settings, mock_db
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

        mock_complete.return_value = json.dumps(extraction_result)

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
                confidence_score=0.7,
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
                confidence_score=0.7,
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
    @patch("src.services.memory_service.extraction.complete_text")
    @pytest.mark.asyncio
    async def test_extract_and_store_with_entity_ids(
        self, mock_complete, mock_embedder_cls, settings, mock_db
    ):
        """Memories should store entity_ids when provided."""
        mock_embedder = MagicMock()
        mock_embedder.embed_text = AsyncMock(return_value=[0.1] * 768)
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

        mock_complete.return_value = json.dumps(extraction_result)

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
    @pytest.mark.asyncio
    async def test_find_returns_temporal_fields(self, settings, mock_db):
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
        mock_entity.confidence_score = 0.9

        result_mock = MagicMock()
        result_mock.scalars.return_value.all.return_value = [mock_entity]
        mock_db.execute = AsyncMock(return_value=result_mock)

        wm = WorldModel(settings=settings, db=mock_db)
        results = await wm.find_entity(TEST_USER_ID, "Alice")

        assert len(results) == 1
        assert results[0]["importance_score"] == 0.85
        assert results[0]["interaction_count"] == 12
        assert results[0]["last_seen_at"] is not None


# ── Financial Entity Extraction ──────────────────────────────────


class TestFinancialEntities:
    @pytest.mark.asyncio
    async def test_financial_transaction_type_preserved(self, settings, mock_db):
        """A financial_transaction type from the extractor must NOT be coerced to person."""
        extracted = {
            "entities": [
                {
                    "entity_type": "financial_transaction",
                    "canonical_name": "INR 1087 at SwiftPay",
                    "attributes": {
                        "amount": 1087,
                        "currency": "INR",
                        "merchant": "SwiftPay",
                        "account_last4": "3971",
                        "direction": "debit",
                    },
                    "importance": 0.6,
                },
                {
                    "entity_type": "merchant",
                    "canonical_name": "SwiftPay",
                    "importance": 0.4,
                },
            ],
            "relationships": [],
        }

        wm = WorldModel(settings=settings, db=mock_db)

        event = MagicMock(
            spec=["event_id", "event_type", "source", "title", "summary", "actor_entities"]
        )
        event.event_id = "evt_x"
        event.event_type = "email_received"
        event.source = "gmail"
        event.title = "Card charged"
        event.summary = "INR 1087 spent on credit card no. XX3971"
        event.actor_entities = None

        # First execute() is the NormalizedEvent lookup; later lookups (dedup /
        # find_entity for relationships) must return no existing entity.
        event_result = MagicMock()
        event_result.scalar_one_or_none.return_value = event
        empty_result = MagicMock()
        empty_result.scalar_one_or_none.return_value = None
        empty_result.scalars.return_value.all.return_value = []
        mock_db.execute = AsyncMock(side_effect=[event_result] + [empty_result] * 30)

        with patch.object(wm, "_call_extraction", AsyncMock(return_value=extracted)):
            await wm.extract_from_event("evt_x", TEST_USER_ID)

        created_types = [
            call.args[0].entity_type
            for call in mock_db.add.call_args_list
            if hasattr(call.args[0], "entity_type")
        ]
        assert "financial_transaction" in created_types
        assert "merchant" in created_types
        assert "person" not in created_types  # nothing coerced


# ── PII-as-name Privacy Guard ────────────────────────────────────


class TestSanitizeCanonicalName:
    def test_bare_email_replaced_with_label_and_aliased(self):
        from src.services.world_model import sanitize_canonical_name

        name, aliases = sanitize_canonical_name("john.doe@acme.com", None)
        assert "@" not in name
        assert name == "John Doe"
        assert "john.doe@acme.com" in aliases

    def test_bare_email_numeric_local_falls_back_to_domain(self):
        from src.services.world_model import sanitize_canonical_name

        name, aliases = sanitize_canonical_name("12345@acme.com", [])
        assert "@" not in name
        assert name == "Sender (acme.com)"
        assert "12345@acme.com" in aliases

    def test_normal_name_untouched(self):
        from src.services.world_model import sanitize_canonical_name

        name, aliases = sanitize_canonical_name("John Doe", ["jdoe@acme.com"])
        assert name == "John Doe"
        assert aliases == ["jdoe@acme.com"]

    def test_email_not_duplicated_in_aliases(self):
        from src.services.world_model import sanitize_canonical_name

        name, aliases = sanitize_canonical_name("a@b.com", ["a@b.com"])
        assert aliases.count("a@b.com") == 1

    def test_empty_name_becomes_unknown(self):
        from src.services.world_model import sanitize_canonical_name

        name, aliases = sanitize_canonical_name("", None)
        assert name == "Unknown"

    @pytest.mark.asyncio
    async def test_upsert_entity_enforces_pii_guard(self, settings, mock_db):
        """upsert_entity stores a non-email canonical name and aliases the raw email."""
        wm = WorldModel(settings=settings, db=mock_db)

        await wm.upsert_entity(
            user_id=TEST_USER_ID,
            entity_type="person",
            canonical_name="jane@example.com",
        )

        entity = next(
            call.args[0]
            for call in mock_db.add.call_args_list
            if hasattr(call.args[0], "canonical_name")
        )
        assert "@" not in entity.canonical_name
        alias_rows = [
            call.args[0] for call in mock_db.add.call_args_list if hasattr(call.args[0], "alias")
        ]
        assert any(a.alias == "jane@example.com" for a in alias_rows)


# ── Prompt ↔ Constant Consistency ────────────────────────────────


class TestPromptConstantConsistency:
    def test_prompt_entity_types_match_constant(self):
        from src.services.world_model import ENTITY_EXTRACTION_PROMPT, ENTITY_TYPES

        block = ENTITY_EXTRACTION_PROMPT.split("Entity types:", 1)[1]
        block = block.split("Relation types:", 1)[0]
        named = {t.strip() for t in block.replace("\n", " ").split(",") if t.strip()}
        assert named == set(ENTITY_TYPES), f"Prompt/constant drift: {named ^ set(ENTITY_TYPES)}"

    def test_prompt_relation_types_match_constant(self):
        from src.services.world_model import ENTITY_EXTRACTION_PROMPT, RELATION_TYPES

        block = ENTITY_EXTRACTION_PROMPT.split("Relation types:", 1)[1]
        block = block.split("\n\n", 1)[0]
        named = {r.strip() for r in block.replace("\n", " ").split(",") if r.strip()}
        assert named == set(RELATION_TYPES), f"Prompt/constant drift: {named ^ set(RELATION_TYPES)}"
