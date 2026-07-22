"""EntityResolver: workspace-scoped statement builders (compiled-SQL, no DB) plus
merge/hydrate logic (mocked session + patched FTS, no DB, no network)."""

from types import SimpleNamespace
from unittest.mock import AsyncMock, MagicMock, patch

from sqlalchemy.dialects import postgresql

from src.services.entity_resolver import (
    EntityResolver,
    _exact_match_stmt,
    _hydrate_entities_stmt,
)


def _compile(stmt) -> str:
    return str(
        stmt.compile(dialect=postgresql.dialect(), compile_kwargs={"literal_binds": True})
    ).lower()


def test_exact_match_stmt_is_workspace_scoped():
    sql = _compile(_exact_match_stmt("usr_1", "Acme", "ws_A"))
    assert "entities.workspace_id = 'ws_a'" in sql
    assert "entity_aliases.workspace_id = 'ws_a'" in sql


def test_hydrate_stmt_is_workspace_scoped():
    sql = _compile(_hydrate_entities_stmt("usr_1", ["ent_1"], "ws_A"))
    assert "entities.workspace_id = 'ws_a'" in sql
    assert "entities.user_id = 'usr_1'" in sql


def _entity(**kw):
    base = dict(
        entity_id="ent_1",
        entity_type="person",
        canonical_name="Acme",
        attributes=None,
        importance_score=0.9,
        interaction_count=3,
        last_seen_at=None,
        confidence_score=1.0,
    )
    base.update(kw)
    return SimpleNamespace(**base)


def _result_with(rows):
    res = MagicMock()
    res.scalars.return_value.all.return_value = rows
    return res


async def test_exact_hit_is_hydrated_workspace_scoped():
    # "Acme" -> exactly one span; execute called twice: exact then hydrate.
    exact = _result_with(["ent_1"])
    hydrate = _result_with([_entity()])
    db = MagicMock()
    db.execute = AsyncMock(side_effect=[exact, hydrate])
    with patch("src.services.entity_resolver.FTSService") as fts_cls:
        fts_cls.return_value.search_table = AsyncMock(return_value=[])
        resolver = EntityResolver(db, "ws_A", embedding_service=None, vector_store=None)
        out = await resolver.resolve("usr_1", "Acme", limit=10)
    assert db.execute.await_count == 2
    assert [e["entity_id"] for e in out] == ["ent_1"]
    assert out[0]["canonical_name"] == "Acme"  # same dict shape as find_entity


async def test_fts_candidate_missed_by_exact_is_still_hydrated():
    exact = _result_with([])  # exact miss
    hydrate = _result_with([_entity(entity_id="ent_2", canonical_name="Phoenix")])
    db = MagicMock()
    db.execute = AsyncMock(side_effect=[exact, hydrate])
    with patch("src.services.entity_resolver.FTSService") as fts_cls:
        fts_cls.return_value.search_table = AsyncMock(return_value=[{"id": "ent_2", "score": 0.3}])
        resolver = EntityResolver(db, "ws_A", embedding_service=None, vector_store=None)
        out = await resolver.resolve("usr_1", "Phoenix", limit=10)
    assert [e["entity_id"] for e in out] == ["ent_2"]


async def test_vector_candidate_is_merged_when_services_present():
    exact = _result_with([])
    hydrate = _result_with([_entity(entity_id="ent_3", canonical_name="Zeta")])
    db = MagicMock()
    db.execute = AsyncMock(side_effect=[exact, hydrate])
    embed = AsyncMock()
    embed.embed_text = AsyncMock(return_value=[0.1] * 768)
    vec = AsyncMock()
    vec.search = AsyncMock(return_value=[{"id": "ent_3", "score": 0.95, "payload": {}}])
    with patch("src.services.entity_resolver.FTSService") as fts_cls:
        fts_cls.return_value.search_table = AsyncMock(return_value=[])
        resolver = EntityResolver(db, "ws_A", embedding_service=embed, vector_store=vec)
        out = await resolver.resolve("usr_1", "Zeta", limit=10)
    embed.embed_text.assert_awaited()
    vec.search.assert_awaited()
    assert [e["entity_id"] for e in out] == ["ent_3"]


async def test_no_candidates_returns_empty_without_hydrating():
    exact = _result_with([])
    db = MagicMock()
    db.execute = AsyncMock(side_effect=[exact])  # only the exact lookup, no hydrate
    with patch("src.services.entity_resolver.FTSService") as fts_cls:
        fts_cls.return_value.search_table = AsyncMock(return_value=[])
        resolver = EntityResolver(db, "ws_A", embedding_service=None, vector_store=None)
        out = await resolver.resolve("usr_1", "Acme", limit=10)
    assert out == []
    assert db.execute.await_count == 1  # never hydrated
