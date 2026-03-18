"""Tests for Phase 3B: Entity fuzzy dedup via embeddings."""

from unittest.mock import AsyncMock, MagicMock, patch

from tests.conftest import make_mock_settings


def _make_world_model(db=None, embedding_service=None):
    from src.services.world_model import WorldModel

    settings = make_mock_settings()
    db = db or AsyncMock()

    with patch("src.services.world_model.get_anthropic_client"):
        wm = WorldModel(
            settings=settings,
            db=db,
            embedding_service=embedding_service,
        )
    return wm


def _make_entity(entity_id="ent_001", canonical_name="John Doe", entity_type="person"):
    entity = MagicMock()
    entity.entity_id = entity_id
    entity.canonical_name = canonical_name
    entity.entity_type = entity_type
    entity.user_id = "usr_1"
    entity.attributes = {}
    entity.last_seen_at = None
    entity.interaction_count = 0
    entity.importance_score = 0.5
    return entity


class TestEntityFuzzyDedup:
    async def test_exact_name_match_returns_entity(self):
        """Exact canonical_name match returns entity without embedding."""
        entity = _make_entity()
        db = AsyncMock()
        mock_result = MagicMock()
        mock_result.scalar_one_or_none.return_value = entity
        db.execute = AsyncMock(return_value=mock_result)

        wm = _make_world_model(db=db)
        found = await wm._find_by_name_or_alias("usr_1", "John Doe", None)

        assert found is entity

    async def test_alias_match_returns_entity(self):
        """Alias match returns entity."""
        entity = _make_entity()
        db = AsyncMock()

        # First call (canonical_name) returns None
        # Second call (alias) returns entity
        no_match = MagicMock()
        no_match.scalar_one_or_none.return_value = None
        match = MagicMock()
        match.scalar_one_or_none.return_value = entity

        db.execute = AsyncMock(side_effect=[no_match, match])

        wm = _make_world_model(db=db)
        found = await wm._find_by_name_or_alias("usr_1", "Johnny", ["john@co.com"])

        assert found is entity

    async def test_fuzzy_embedding_match(self):
        """Embedding similarity > 0.92 returns matching entity."""
        entity = _make_entity()
        db = AsyncMock()

        # Canonical name: no match
        no_match = MagicMock()
        no_match.scalar_one_or_none.return_value = None

        # Embedding query: returns entity_id
        embedding_result = MagicMock()
        embedding_result.scalar_one_or_none.return_value = "ent_001"

        # Fetch entity by id
        entity_result = MagicMock()
        entity_result.scalar_one_or_none.return_value = entity

        db.execute = AsyncMock(side_effect=[no_match, embedding_result, entity_result])

        embed_svc = AsyncMock()
        embed_svc.embed = AsyncMock(return_value=[0.1] * 1024)

        wm = _make_world_model(db=db, embedding_service=embed_svc)
        found = await wm._find_by_name_or_alias("usr_1", "Jon Doe", None)

        assert found is entity
        embed_svc.embed.assert_called_once_with("Jon Doe")

    async def test_no_embedding_service_skips_fuzzy(self):
        """Without embedding_service, fuzzy match is skipped."""
        db = AsyncMock()
        no_match = MagicMock()
        no_match.scalar_one_or_none.return_value = None
        db.execute = AsyncMock(return_value=no_match)

        wm = _make_world_model(db=db, embedding_service=None)
        found = await wm._find_by_name_or_alias("usr_1", "Unknown Person", None)

        assert found is None

    async def test_embedding_failure_falls_back_to_none(self):
        """If embedding fails, returns None gracefully."""
        db = AsyncMock()
        no_match = MagicMock()
        no_match.scalar_one_or_none.return_value = None
        db.execute = AsyncMock(return_value=no_match)

        embed_svc = AsyncMock()
        embed_svc.embed = AsyncMock(side_effect=RuntimeError("Bedrock error"))

        wm = _make_world_model(db=db, embedding_service=embed_svc)
        found = await wm._find_by_name_or_alias("usr_1", "Some Name", None)

        assert found is None

    async def test_embedding_no_match_returns_none(self):
        """Embedding search returns no match → None."""
        db = AsyncMock()

        no_match = MagicMock()
        no_match.scalar_one_or_none.return_value = None
        db.execute = AsyncMock(return_value=no_match)

        embed_svc = AsyncMock()
        embed_svc.embed = AsyncMock(return_value=[0.1] * 1024)

        wm = _make_world_model(db=db, embedding_service=embed_svc)
        found = await wm._find_by_name_or_alias("usr_1", "Totally New Person", None)

        assert found is None


class TestEntityEmbeddingOnUpsert:
    async def test_upsert_stores_embedding_on_create(self):
        """New entity gets embedding stored."""
        db = AsyncMock()
        db.add = MagicMock()
        db.commit = AsyncMock()

        # _find_by_name_or_alias returns None (no existing entity)
        no_match = MagicMock()
        no_match.scalar_one_or_none.return_value = None
        db.execute = AsyncMock(return_value=no_match)

        embed_svc = AsyncMock()
        embed_svc.embed = AsyncMock(return_value=[0.5] * 1024)

        wm = _make_world_model(db=db, embedding_service=embed_svc)

        with patch.object(wm, "_emit_event", new_callable=AsyncMock):
            entity_id = await wm.upsert_entity(
                user_id="usr_1",
                entity_type="person",
                canonical_name="Jane Smith",
            )

        assert entity_id.startswith("ent_")
        # embed called for both fuzzy dedup lookup and entity creation
        assert embed_svc.embed.call_count >= 1
        # Check that Entity was added with embedding
        added_entity = db.add.call_args[0][0]
        assert added_entity.embedding == [0.5] * 1024

    async def test_upsert_without_embedding_service(self):
        """Upsert works fine without embedding_service."""
        db = AsyncMock()
        db.add = MagicMock()
        db.commit = AsyncMock()

        no_match = MagicMock()
        no_match.scalar_one_or_none.return_value = None
        db.execute = AsyncMock(return_value=no_match)

        wm = _make_world_model(db=db, embedding_service=None)

        with patch.object(wm, "_emit_event", new_callable=AsyncMock):
            entity_id = await wm.upsert_entity(
                user_id="usr_1",
                entity_type="person",
                canonical_name="Bob",
            )

        assert entity_id.startswith("ent_")
        added_entity = db.add.call_args[0][0]
        assert added_entity.embedding is None
