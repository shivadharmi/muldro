"""Tests for EvidenceBundleService."""

from src.services.evidence_models import EntityRef, EvidenceBundle, MemoryRef, SourceRef


class TestEvidenceBundle:
    def test_empty_bundle(self):
        bundle = EvidenceBundle()
        assert bundle.entities == []
        assert bundle.memories == []
        assert bundle.sources == []
        assert bundle.confidence is None

    def test_bundle_with_entities(self):
        bundle = EvidenceBundle(
            entities=[
                EntityRef(
                    entity_id="ent_test",
                    name="John Doe",
                    entity_type="person",
                    relevance=0.9,
                )
            ]
        )
        assert len(bundle.entities) == 1
        assert bundle.entities[0].name == "John Doe"

    def test_bundle_with_sources(self):
        bundle = EvidenceBundle(
            sources=[
                SourceRef(
                    source_type="trace",
                    source_id="trace_123",
                    label="Trace: planner",
                )
            ]
        )
        assert len(bundle.sources) == 1
        assert bundle.sources[0].source_type == "trace"

    def test_bundle_serialization(self):
        bundle = EvidenceBundle(
            entities=[
                EntityRef(
                    entity_id="ent_1",
                    name="Alice",
                    entity_type="person",
                )
            ],
            memories=[
                MemoryRef(
                    memory_id="mem_1",
                    content="Alice is the CEO",
                    memory_type="semantic",
                )
            ],
            confidence=0.85,
            risk_level="low",
        )
        data = bundle.model_dump()
        assert data["confidence"] == 0.85
        assert len(data["entities"]) == 1
        assert len(data["memories"]) == 1
