"""to_prompt renders per-entity confidence (+ a low-confidence abstention hint) so the
agent can ask/abstain (spec §4.6 item 4 / §4.5). Pure string assembly — no DB."""

from src.services.context_builder import ContextBuilder, ContextPack


def _prompt_for(entities: list[dict]) -> str:
    pack = ContextPack(entities=entities)
    return ContextBuilder.to_prompt(pack)


def test_high_confidence_entity_shows_confidence():
    out = _prompt_for([{"canonical_name": "Bob", "entity_type": "person", "confidence": 0.92}])
    assert "Bob (person)" in out
    assert "confidence=0.92" in out


def test_low_confidence_entity_shows_an_abstention_hint():
    out = _prompt_for(
        [{"canonical_name": "Acme", "entity_type": "organization", "confidence": 0.30}]
    )
    assert "confidence=0.30" in out
    assert "unverified" in out.lower() or "low confidence" in out.lower()


def test_missing_confidence_renders_without_crashing():
    out = _prompt_for([{"canonical_name": "Dana", "entity_type": "person"}])
    assert "Dana (person)" in out
