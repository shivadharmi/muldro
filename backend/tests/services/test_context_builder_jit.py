"""Step 8 characterization + JIT tests for ContextBuilder."""

from unittest.mock import AsyncMock, MagicMock

from src.services.context_builder import ContextBuilder, ContextPack


def test_to_prompt_renders_stable_sections_for_full_pack():
    pack = ContextPack(
        task_summary="ship the thing",
        goals=[{"title": "launch", "priority": "critical"}],
        entities=[{"canonical_name": "Acme", "entity_type": "org", "importance_score": 0.9}],
        preferences=[{"fact_text": "prefers concise replies"}],
        recent_events=[{"fact_text": "signed contract"}],
    )
    out = ContextBuilder.to_prompt(pack)
    assert "## Task\nship the thing" in out
    assert "## Active Goals" in out and "launch" in out
    assert "## Relevant Entities" in out and "Acme" in out
    assert "## User Preferences" in out and "prefers concise replies" in out
    assert "## Artifacts" not in out
    assert "## Available Tools" not in out


def _slim_db():
    """A db mock whose .execute(...).scalars().all() returns [] (for the direct-query helpers)."""
    db = MagicMock()
    result = MagicMock()
    result.scalars.return_value.all.return_value = []
    db.execute = AsyncMock(return_value=result)
    return db


async def test_build_jit_slim_pack_skips_bulky_categories():
    wm = MagicMock()
    wm.resolve_entities = AsyncMock(return_value=[{"entity_id": "e1", "canonical_name": "Acme"}])
    ge = MagicMock()
    ge.traverse_weighted = AsyncMock(return_value=[{"entity_id": "e2", "name": "Beta"}])
    mem = MagicMock()
    mem.retrieve = AsyncMock(return_value=[{"memory_type": "episodic", "fact_text": "x"}])
    mem.get_user_preferences = AsyncMock(return_value=[{"memory_id": "p1", "fact_text": "concise"}])

    builder = ContextBuilder(world_model=wm, memory_service=mem, graph_engine=ge, db=_slim_db())
    pack = await builder.build(user_id="u", query="q", workspace_id="w", jit=True)

    # Bulky categories NOT eagerly populated in slim mode:
    assert pack.graph_relationships == []
    assert pack.related_runs == []
    # The expensive semantic/graph calls must NOT fire:
    wm.resolve_entities.assert_not_called()
    ge.traverse_weighted.assert_not_called()
    mem.retrieve.assert_not_called()
    # Always-on core present (explicit prefs via get_user_preferences):
    assert any(p.get("fact_text") == "concise" for p in pack.preferences)


def test_to_prompt_jit_renders_compact_entities_and_retrieval_hint():
    pack = ContextPack(
        task_summary="q",
        entities=[{"canonical_name": "Acme", "entity_type": "org"}],
        goals=[{"title": "launch"}],
        preferences=[{"fact_text": "concise"}],
    )
    out = ContextBuilder.to_prompt(pack, jit=True)
    assert "Acme (org)" in out
    assert "importance=" not in out  # compact: no eager decoration in slim mode
    assert "get_entity" in out or "query_facts" in out  # retrieval hint present
