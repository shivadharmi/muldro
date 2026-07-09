"""Step 8 characterization + JIT tests for ContextBuilder."""

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
