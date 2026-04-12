"""Tests for validate_tier3_health — detects configured-but-missing Tier 3 services."""

from unittest.mock import MagicMock

from src.orchestrator.services import ServiceContainer
from src.runtime import validate_tier3_health


def _make_settings(**overrides) -> MagicMock:
    settings = MagicMock()
    defaults = dict(
        neo4j_url="",
        qdrant_url="",
        reranker_enabled=False,
    )
    defaults.update(overrides)
    for k, v in defaults.items():
        setattr(settings, k, v)
    return settings


class TestValidateTier3Health:
    def test_detects_configured_but_missing_neo4j(self):
        """neo4j_url set but graph_engine None → 'neo4j' in degraded."""
        settings = _make_settings(neo4j_url="bolt://localhost:7687")
        svc = ServiceContainer()
        svc.graph_engine = None

        degraded = validate_tier3_health(settings, svc)

        assert "neo4j" in degraded
        assert svc.extras["degraded_services"] == degraded

    def test_detects_configured_but_missing_qdrant(self):
        """qdrant_url set but vector_store None → 'qdrant' in degraded."""
        settings = _make_settings(qdrant_url="http://localhost:6333")
        svc = ServiceContainer()
        svc.vector_store = None

        degraded = validate_tier3_health(settings, svc)

        assert "qdrant" in degraded
        assert svc.extras["degraded_services"] == degraded

    def test_no_degradation_when_all_healthy(self):
        """Both URLs set and services available → empty degraded list."""
        settings = _make_settings(
            neo4j_url="bolt://localhost:7687",
            qdrant_url="http://localhost:6333",
        )
        svc = ServiceContainer()
        svc.graph_engine = MagicMock()
        svc.vector_store = MagicMock()

        degraded = validate_tier3_health(settings, svc)

        assert degraded == []
        assert svc.extras["degraded_services"] == []

    def test_no_degradation_when_not_configured(self):
        """URLs empty and services None → empty degraded list (not configured)."""
        settings = _make_settings(neo4j_url="", qdrant_url="")
        svc = ServiceContainer()
        svc.graph_engine = None
        svc.vector_store = None

        degraded = validate_tier3_health(settings, svc)

        assert degraded == []
        assert svc.extras["degraded_services"] == []

    def test_detects_configured_but_missing_reranker(self):
        """reranker_enabled True but reranker None → 'reranker' in degraded."""
        settings = _make_settings(reranker_enabled=True)
        svc = ServiceContainer()
        svc.reranker = None

        degraded = validate_tier3_health(settings, svc)

        assert "reranker" in degraded

    def test_extras_degraded_services_stored(self):
        """validate_tier3_health always writes degraded_services to svc.extras."""
        settings = _make_settings()
        svc = ServiceContainer()

        validate_tier3_health(settings, svc)

        assert "degraded_services" in svc.extras

    def test_multiple_degraded_services(self):
        """Both neo4j and qdrant configured but missing → both in degraded list."""
        settings = _make_settings(
            neo4j_url="bolt://localhost:7687",
            qdrant_url="http://localhost:6333",
        )
        svc = ServiceContainer()
        svc.graph_engine = None
        svc.vector_store = None

        degraded = validate_tier3_health(settings, svc)

        assert "neo4j" in degraded
        assert "qdrant" in degraded
        assert len(degraded) == 2
