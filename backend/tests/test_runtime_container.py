"""Tests for RuntimeContainer.build() — verifies all tiers populate correctly."""

from unittest.mock import AsyncMock, MagicMock, patch

import pytest

from tests.conftest import make_mock_settings


@pytest.fixture
def mock_db():
    db = AsyncMock()
    db.execute = AsyncMock(return_value=MagicMock(scalar_one_or_none=MagicMock(return_value=None)))
    db.add = MagicMock()
    db.flush = AsyncMock()
    db.commit = AsyncMock()
    return db


@pytest.fixture
def settings():
    return make_mock_settings()


class TestRuntimeBuild:
    def test_all_tiers_populated(self, settings, mock_db):
        """When all services are available, RuntimeContainer should populate them all."""
        from src.runtime import build

        svc = build(settings, mock_db)

        # Tier 1 — must be present
        assert svc.world_model is not None
        assert svc.memory_service is not None
        assert svc.extras.get("embedding_service") is not None

        # Tier 2 — should be present when all deps available
        assert svc.event_processor is not None
        assert svc.governor is not None
        assert svc.presenter is not None
        assert svc.audit is not None
        assert svc.graph_executor is not None

    def test_tier1_failure_raises(self, settings, mock_db):
        """Tier 1 failure should raise RuntimeBuildError, not silently degrade."""
        from src.runtime import RuntimeBuildError, build

        with patch("src.services.world_model.WorldModel.__init__", side_effect=Exception("boom")):
            with pytest.raises(RuntimeBuildError, match="WorldModel"):
                build(settings, mock_db)

    def test_tier2_failure_degrades(self, settings, mock_db):
        """Tier 2 failure should degrade gracefully, not raise."""
        from src.runtime import build

        with patch("src.services.governor.Governor.__init__", side_effect=Exception("boom")):
            svc = build(settings, mock_db)
            assert svc.governor is None
            # Other services should still be populated
            assert svc.world_model is not None

    def test_tier3_failure_degrades(self, settings, mock_db):
        """Tier 3 failure should not affect other services."""
        from src.runtime import build

        with patch("src.services.vector_store.VectorStore.__init__", side_effect=Exception("boom")):
            svc = build(settings, mock_db)
            assert svc.vector_store is None
            assert svc.world_model is not None

    def test_graph_executor_wired(self, settings, mock_db):
        """GraphExecutor should be wired into ServiceContainer."""
        from src.runtime import build

        svc = build(settings, mock_db)
        assert svc.graph_executor is not None

    def test_service_container_attribute_access(self, settings, mock_db):
        """ServiceContainer should support typed attribute access."""
        from src.runtime import build

        svc = build(settings, mock_db)
        assert svc.world_model is not None
        assert svc.memory_service is not None

    def test_event_bus_wired_with_real_client_not_url_string(self, settings, mock_db):
        """EventBus must receive a Redis client object, not the redis_url string.

        Regression for P1 #2: build() passed ``settings.redis_url`` (a str) into
        ``EventBus(redis)``. Construction did not raise, so the GraphExecutor held
        an EventBus whose ``_redis`` was a bare string — ``.xadd()`` then failed at
        publish time and durable graph-executor domain events were silently dropped.
        """
        from src.runtime import build

        svc = build(settings, mock_db)
        assert svc.graph_executor is not None
        event_bus = svc.graph_executor._event_bus
        assert event_bus is not None, "EventBus should be wired into GraphExecutor"
        assert not isinstance(event_bus._redis, str), (
            "EventBus._redis must be a Redis client, not the redis_url string"
        )
        # A real aioredis client exposes the stream API the bus depends on.
        assert hasattr(event_bus._redis, "xadd")

    def test_build_shared_has_no_db_bound_services(self, settings):
        """build_shared() builds only session-free singletons; DB-bound
        services stay None so nothing holds a long-lived AsyncSession.

        Regression for P2 #4: the orchestrator must not share one AsyncSession
        across concurrent requests.
        """
        from src.runtime import build_shared

        shared = build_shared(settings)

        # Session-free singletons present.
        assert shared.vector_store is not None
        assert shared.extras.get("embedding_service") is not None

        # DB-bound services absent — they are built per-request via attach_session.
        assert shared.world_model is None
        assert shared.memory_service is None
        assert shared.governor is None
        assert shared.presenter is None
        assert shared.audit is None
        assert shared.event_processor is None
        assert shared.graph_executor is None
        assert shared.trust_engine is None

    def test_attach_session_binds_db_bound_services_to_the_request_db(self, settings, mock_db):
        """attach_session() binds DB-bound services to the per-request session."""
        from src.runtime import attach_session, build_shared

        shared = build_shared(settings)
        svc = attach_session(shared, settings, mock_db)

        assert svc.world_model is not None and svc.world_model._db is mock_db
        assert svc.memory_service is not None and svc.memory_service._db is mock_db
        assert svc.governor is not None and svc.governor._db is mock_db
        assert svc.audit is not None and svc.audit._db is mock_db
        assert svc.graph_executor is not None

    def test_attach_session_reuses_shared_singletons_by_identity(self, settings, mock_db):
        """Per-request containers reuse the same session-free singletons (no churn)."""
        from src.runtime import attach_session, build_shared

        shared = build_shared(settings)
        svc = attach_session(shared, settings, mock_db)

        assert svc.vector_store is shared.vector_store
        assert svc.extras.get("embedding_service") is shared.extras.get("embedding_service")

    def test_two_attach_sessions_do_not_share_db_bound_state(self, settings):
        """Two concurrent requests get independent sessions/services — the core
        fix for the singleton-orchestrator concurrency hazard."""
        from unittest.mock import AsyncMock

        from src.runtime import attach_session, build_shared

        shared = build_shared(settings)
        db_a, db_b = AsyncMock(), AsyncMock()
        svc_a = attach_session(shared, settings, db_a)
        svc_b = attach_session(shared, settings, db_b)

        assert svc_a.world_model is not svc_b.world_model
        assert svc_a.world_model._db is db_a
        assert svc_b.world_model._db is db_b

    def test_request_services_reuses_injected_container(self, settings, mock_db):
        """request_services reuses a container that already carries DB-bound
        services (injected mocks / single-flow build) — it must NOT rebuild and
        discard injected services."""
        from unittest.mock import MagicMock

        from src.orchestrator.services import ServiceContainer
        from src.runtime import request_services

        injected_wm = MagicMock()
        base = ServiceContainer(world_model=injected_wm)  # partial injection

        result = request_services(base, settings, mock_db)

        assert result is base
        assert result.world_model is injected_wm

    def test_request_services_builds_per_request_for_shared_container(self, settings, mock_db):
        """A session-free shared container (all DB-bound None) triggers a
        per-request build bound to the given session."""
        from src.runtime import build_shared, request_services

        shared = build_shared(settings)  # no DB-bound services
        result = request_services(shared, settings, mock_db)

        assert result is not shared
        assert result.world_model is not None
        assert result.world_model._db is mock_db

    def test_verifier_wired_with_correct_arg_order(self, settings, mock_db):
        """Verifier(settings, db) — not (db, settings).

        Regression for P2 #3: runtime.build() called ``Verifier(db, settings)`` but
        the signature is ``Verifier(settings, db)``. The swap put an AsyncSession in
        ``_settings`` and Settings in ``_db``, so verification misbehaved/disabled in
        the runtime composition path while the factory path was correct.
        """
        from src.runtime import build

        svc = build(settings, mock_db)
        assert svc.graph_executor is not None
        verifier = svc.graph_executor._verifier
        assert verifier is not None, "Verifier should be wired into GraphExecutor"
        assert verifier._settings is settings, "Verifier._settings must be the Settings object"
        assert verifier._db is mock_db, "Verifier._db must be the AsyncSession"
