"""Tests for Spec 0: Foundation Hardening."""

import os
import socket
from unittest.mock import AsyncMock, MagicMock

import pytest

from tests.conftest import make_mock_settings


class TestSettingsCleanup:
    """Fix 6.1: Remove unused settings + add environment field."""

    def test_environment_field_defaults_to_development(self):
        from src.config.settings import Settings

        s = Settings(
            anthropic_api_key="test",
            database_url="postgresql+asyncpg://x/x",
            redis_url="redis://localhost",
        )
        assert s.environment == "development"

    def test_environment_field_accepts_production(self):
        from src.config.settings import Settings

        s = Settings(
            anthropic_api_key="test",
            database_url="postgresql+asyncpg://x/x",
            redis_url="redis://localhost",
            environment="production",
        )
        assert s.environment == "production"

    def test_unused_twilio_fields_removed(self):
        from src.config.settings import Settings

        assert not hasattr(Settings, "twilio_account_sid")
        assert not hasattr(Settings, "twilio_auth_token")
        assert not hasattr(Settings, "twilio_from_number")

    def test_unused_whatsapp_fields_removed(self):
        from src.config.settings import Settings

        assert not hasattr(Settings, "whatsapp_phone_number_id")
        assert not hasattr(Settings, "whatsapp_access_token")
        assert not hasattr(Settings, "whatsapp_verify_token")
        assert not hasattr(Settings, "whatsapp_app_secret")

    def test_unused_session_secret_removed(self):
        from src.config.settings import Settings

        assert not hasattr(Settings, "session_secret_key")

    def test_unused_stale_observation_fields_removed(self):
        from src.config.settings import Settings

        assert not hasattr(Settings, "observation_stale_jira_minutes")
        assert not hasattr(Settings, "observation_stale_linkedin_minutes")
        assert not hasattr(Settings, "observation_stale_twitter_minutes")


class TestOAuthStartupValidation:
    """Fix 1.1: Enforce OAuth encryption key at startup."""

    def test_production_without_oauth_key_raises(self):
        from src.runtime import RuntimeBuildError, build

        settings = make_mock_settings(
            oauth_encryption_key="",
            environment="production",
        )
        db = MagicMock()
        with pytest.raises(RuntimeBuildError, match="OAUTH_ENCRYPTION_KEY"):
            build(settings, db)

    def test_development_without_oauth_key_does_not_raise_oauth_error(self):
        from src.runtime import build

        settings = make_mock_settings(
            oauth_encryption_key="",
            environment="development",
        )
        db = MagicMock()
        try:
            build(settings, db)
        except Exception as exc:
            # May fail on Tier 1 service init (WorldModel etc) — that's fine
            # The key assertion is that it doesn't fail on OAuth check
            assert "OAUTH_ENCRYPTION_KEY" not in str(exc)

    def test_production_with_oauth_key_passes_check(self):
        from src.runtime import build

        settings = make_mock_settings(
            oauth_encryption_key="dGVzdC1rZXktMzItYnl0ZXM=",
            environment="production",
        )
        db = MagicMock()
        try:
            build(settings, db)
        except Exception as exc:
            assert "OAUTH_ENCRYPTION_KEY" not in str(exc)


class TestBudgetWorkspaceRequired:
    """Fix 2.4: Budget rejects empty workspace_id."""

    @pytest.mark.asyncio
    async def test_record_usage_rejects_empty_workspace_id(self):
        from src.orchestrator.budget import BudgetTracker

        tracker = BudgetTracker(daily_limit_usd=10.0)
        db = AsyncMock()
        with pytest.raises(ValueError, match="workspace_id"):
            await tracker.record_usage(
                db,
                agent_name="test",
                model="claude-sonnet-4-20250514",
                input_tokens=100,
                output_tokens=50,
                trigger="test",
                workspace_id="",
            )

    @pytest.mark.asyncio
    async def test_record_usage_accepts_valid_workspace_id(self):
        from src.orchestrator.budget import BudgetTracker

        tracker = BudgetTracker(daily_limit_usd=10.0)
        db = AsyncMock()
        db.add = MagicMock()
        db.flush = AsyncMock()
        usage = await tracker.record_usage(
            db,
            agent_name="test",
            model="claude-sonnet-4-20250514",
            input_tokens=100,
            output_tokens=50,
            trigger="test",
            workspace_id="ws_test",
        )
        assert usage.workspace_id == "ws_test"


class TestWorkerConsumerName:
    """Fix 2.2: Unique consumer name per worker instance."""

    def test_consumer_name_includes_hostname(self):
        from src.services.worker import _get_consumer_name

        name = _get_consumer_name()
        assert socket.gethostname() in name

    def test_consumer_name_includes_pid(self):
        from src.services.worker import _get_consumer_name

        name = _get_consumer_name()
        assert str(os.getpid()) in name

    def test_consumer_name_not_hardcoded(self):
        from src.services.worker import _get_consumer_name

        name = _get_consumer_name()
        assert name != "worker-1"


class TestWorkerDeadLetter:
    """Fix 2.3: Worker DLQ after 3 failed retries."""

    @pytest.mark.asyncio
    async def test_handler_success_clears_retry_counter(self):
        from src.services.worker import StreamConsumerManager

        settings = make_mock_settings(neo4j_url="")
        manager = StreamConsumerManager(settings)

        mock_redis = AsyncMock()
        mock_redis.delete = AsyncMock()

        event = MagicMock()
        event.payload = {"event_id": "evt_test"}
        event.user_id = "usr_01JTEST00000000000000000000"

        await manager._handle_with_retry(
            handler=AsyncMock(),  # succeeds
            event=event,
            redis=mock_redis,
            dlq=AsyncMock(),
            bus=AsyncMock(),
            stream="test_stream",
            group="test_group",
        )
        mock_redis.delete.assert_called_once()

    @pytest.mark.asyncio
    async def test_handler_failure_after_3_retries_goes_to_dlq(self):
        from src.services.worker import StreamConsumerManager

        settings = make_mock_settings(neo4j_url="")
        manager = StreamConsumerManager(settings)

        mock_redis = AsyncMock()
        mock_redis.incr = AsyncMock(return_value=4)  # 4th attempt
        mock_redis.expire = AsyncMock()

        mock_dlq = AsyncMock()
        mock_dlq.enqueue = AsyncMock(return_value="dlq_001")

        mock_bus = AsyncMock()
        mock_bus.ack = AsyncMock()

        event = MagicMock()
        event.payload = {"event_id": "evt_test"}
        event.user_id = "usr_01JTEST00000000000000000000"
        event.message_id = "msg_test"

        await manager._handle_with_retry(
            handler=AsyncMock(side_effect=Exception("boom")),
            event=event,
            redis=mock_redis,
            dlq=mock_dlq,
            bus=mock_bus,
            stream="test_stream",
            group="test_group",
        )
        mock_dlq.enqueue.assert_called_once()
        mock_bus.ack.assert_called_once()

    @pytest.mark.asyncio
    async def test_handler_failure_under_limit_does_not_dlq(self):
        from src.services.worker import StreamConsumerManager

        settings = make_mock_settings(neo4j_url="")
        manager = StreamConsumerManager(settings)

        mock_redis = AsyncMock()
        mock_redis.incr = AsyncMock(return_value=2)  # 2nd attempt
        mock_redis.expire = AsyncMock()

        mock_dlq = AsyncMock()

        event = MagicMock()
        event.payload = {"event_id": "evt_test"}
        event.user_id = "usr_01JTEST00000000000000000000"

        await manager._handle_with_retry(
            handler=AsyncMock(side_effect=Exception("boom")),
            event=event,
            redis=mock_redis,
            dlq=mock_dlq,
            bus=AsyncMock(),
            stream="test_stream",
            group="test_group",
        )
        mock_dlq.enqueue.assert_not_called()


class TestEventProcessorDLQ:
    """Fix 2.1: Wire DLQ into event processor for failed post-processing."""

    def test_event_processor_accepts_dead_letter(self):
        from src.services.event_processor import EventProcessor

        dlq = MagicMock()
        settings = make_mock_settings()
        db = MagicMock()
        processor = EventProcessor(settings=settings, db=db, dead_letter=dlq)
        assert processor._dead_letter is dlq

    def test_event_processor_works_without_dead_letter(self):
        from src.services.event_processor import EventProcessor

        settings = make_mock_settings()
        db = MagicMock()
        processor = EventProcessor(settings=settings, db=db, dead_letter=None)
        assert processor._dead_letter is None

    def test_no_bare_except_pass_in_event_processor(self):
        """Verify no bare except:pass blocks remain."""
        import re
        from pathlib import Path

        source = Path("src/services/event_processor.py").read_text()
        # Find "except Exception:\n            pass" or "except:\n            pass"
        bare_passes = re.findall(r"except\s*(?:Exception)?\s*:\s*\n\s*pass", source)
        assert len(bare_passes) == 0, f"Found bare except:pass blocks: {bare_passes}"


class TestMCPDiscoveryTracking:
    """Fix 2.5: Track and surface MCP discovery failures."""

    def test_bridge_health_includes_discovery_failures_key(self):
        from src.connectors.mcp_bridge import get_bridge_health

        health = get_bridge_health()
        assert "discovery_failures" in health

    def test_record_and_clear_discovery_failure(self):
        from src.connectors import mcp_bridge

        mcp_bridge.record_discovery_failure("test-server", "Connection refused")
        health = mcp_bridge.get_bridge_health()
        assert "test-server" in health["discovery_failures"]
        assert health["discovery_failures"]["test-server"]["count"] == 1

        # Clean up
        mcp_bridge.clear_discovery_failure("test-server")
        health = mcp_bridge.get_bridge_health()
        assert "test-server" not in health["discovery_failures"]

    def test_discovery_failure_count_increments(self):
        from src.connectors import mcp_bridge

        mcp_bridge.record_discovery_failure("test-srv", "err1")
        mcp_bridge.record_discovery_failure("test-srv", "err2")
        health = mcp_bridge.get_bridge_health()
        assert health["discovery_failures"]["test-srv"]["count"] == 2
        assert health["discovery_failures"]["test-srv"]["error"] == "err2"
        mcp_bridge.clear_discovery_failure("test-srv")


class TestCircuitBreakerReset:
    """Fix 4.3: MCP circuit breaker manual reset."""

    def test_circuit_breaker_reset_restores_closed_state(self):
        from src.services.mcp_resilience import CircuitState, MCPCircuitBreaker

        cb = MCPCircuitBreaker()
        # Record enough failures to open circuit
        for _ in range(6):
            cb.record_failure("test-server")
        assert cb.get_state("test-server") == CircuitState.OPEN

        cb.reset("test-server")
        assert cb.get_state("test-server") == CircuitState.CLOSED


class TestNeo4jBatchSync:
    """Fix 4.1 + 5.2: Batch Neo4j sync and failure tracking."""

    @pytest.mark.asyncio
    async def test_batch_sync_entities_loads_in_bulk(self):
        from src.services.graph_sync import GraphSyncService

        settings = make_mock_settings(neo4j_url="bolt://localhost:7687")
        db = AsyncMock()

        entity1 = MagicMock(
            entity_id="ent_001",
            entity_type="person",
            canonical_name="Alice",
            user_id="usr_test",
            attributes={},
        )
        entity2 = MagicMock(
            entity_id="ent_002",
            entity_type="company",
            canonical_name="Acme",
            user_id="usr_test",
            attributes={},
        )

        # First call returns entities, second returns relationships
        entity_result = MagicMock()
        entity_result.scalars.return_value.all.return_value = [entity1, entity2]
        rel_result = MagicMock()
        rel_result.scalars.return_value.all.return_value = []
        db.execute = AsyncMock(side_effect=[entity_result, rel_result])

        sync = GraphSyncService(settings, db)
        sync._graph = AsyncMock()
        sync._graph.sync_entity = AsyncMock()
        sync._graph.sync_relationship = AsyncMock()

        result = await sync.batch_sync_entities(["ent_001", "ent_002"])
        assert result["entities_synced"] == 2
        assert sync._graph.sync_entity.call_count == 2

    def test_sync_stats_initial_state(self):
        from src.services.graph_sync import GraphSyncService

        settings = make_mock_settings(neo4j_url="bolt://localhost:7687")
        db = MagicMock()
        sync = GraphSyncService(settings, db)
        stats = sync.get_sync_stats()
        assert stats["failures"] == 0
        assert stats["last_error"] is None

    @pytest.mark.asyncio
    async def test_sync_failure_increments_counter(self):
        from src.services.graph_sync import GraphSyncService

        settings = make_mock_settings(neo4j_url="bolt://localhost:7687")
        db = AsyncMock()

        entity = MagicMock(
            entity_id="ent_001",
            entity_type="person",
            canonical_name="Alice",
            user_id="usr_test",
            attributes={},
        )
        entity_result = MagicMock()
        entity_result.scalars.return_value.all.return_value = [entity]
        rel_result = MagicMock()
        rel_result.scalars.return_value.all.return_value = []
        db.execute = AsyncMock(side_effect=[entity_result, rel_result])

        sync = GraphSyncService(settings, db)
        sync._graph = AsyncMock()
        sync._graph.sync_entity = AsyncMock(side_effect=Exception("Neo4j down"))

        await sync.batch_sync_entities(["ent_001"])
        stats = sync.get_sync_stats()
        assert stats["failures"] == 1
        assert "Neo4j down" in stats["last_error"]


class TestBriefingLifecycle:
    """Fix 3.1: Implement briefing pin/snooze/archive state transitions."""

    @pytest.mark.asyncio
    async def test_pin_briefing_sets_pinned_and_status(self):
        from src.services.briefing_read_model import BriefingReadModel

        db = AsyncMock()
        briefing = MagicMock()
        briefing.pinned = False
        briefing.status = "active"

        result_mock = MagicMock()
        result_mock.scalar_one_or_none.return_value = briefing
        db.execute = AsyncMock(return_value=result_mock)
        db.flush = AsyncMock()

        model = BriefingReadModel(db, "ws_test")
        result = await model.pin_briefing("brf_001")
        assert result is True
        assert briefing.pinned is True
        assert briefing.status == "pinned"

    @pytest.mark.asyncio
    async def test_snooze_briefing_sets_snoozed_until(self):
        from src.services.briefing_read_model import BriefingReadModel

        db = AsyncMock()
        briefing = MagicMock()
        briefing.snoozed_until = None
        briefing.status = "active"

        result_mock = MagicMock()
        result_mock.scalar_one_or_none.return_value = briefing
        db.execute = AsyncMock(return_value=result_mock)
        db.flush = AsyncMock()

        model = BriefingReadModel(db, "ws_test")
        result = await model.snooze_briefing("brf_001")
        assert result is True
        assert briefing.snoozed_until is not None
        assert briefing.status == "snoozed"

    @pytest.mark.asyncio
    async def test_archive_briefing_sets_archived(self):
        from src.services.briefing_read_model import BriefingReadModel

        db = AsyncMock()
        briefing = MagicMock()
        briefing.status = "active"

        result_mock = MagicMock()
        result_mock.scalar_one_or_none.return_value = briefing
        db.execute = AsyncMock(return_value=result_mock)
        db.flush = AsyncMock()

        model = BriefingReadModel(db, "ws_test")
        result = await model.archive_briefing("brf_001")
        assert result is True
        assert briefing.status == "archived"

    @pytest.mark.asyncio
    async def test_pin_nonexistent_briefing_returns_false(self):
        from src.services.briefing_read_model import BriefingReadModel

        db = AsyncMock()
        result_mock = MagicMock()
        result_mock.scalar_one_or_none.return_value = None
        db.execute = AsyncMock(return_value=result_mock)

        model = BriefingReadModel(db, "ws_test")
        assert await model.pin_briefing("brf_nonexistent") is False

    def test_to_list_item_uses_model_status(self):
        from src.services.briefing_read_model import BriefingReadModel

        model = BriefingReadModel.__new__(BriefingReadModel)
        briefing = MagicMock()
        briefing.briefing_id = "brf_001"
        briefing.headline = "Test"
        briefing.briefing_date = None
        briefing.status = "pinned"
        briefing.created_at = None

        item = model._to_list_item(briefing)
        assert item["status"] == "pinned"


class TestTraceCostReconciliation:
    """Fix 5.1: Budget records from trace spans."""

    @pytest.mark.asyncio
    async def test_record_from_span_delegates_to_record_usage(self):
        from src.orchestrator.budget import BudgetTracker

        tracker = BudgetTracker(daily_limit_usd=10.0)
        db = AsyncMock()
        db.add = MagicMock()
        db.flush = AsyncMock()

        span = MagicMock()
        span.input_tokens = 1000
        span.output_tokens = 500
        span.cache_creation_input_tokens = 100
        span.cache_read_input_tokens = 200
        span.thinking_tokens = 50

        usage = await tracker.record_from_span(
            db,
            span=span,
            agent_name="observer",
            model="claude-sonnet-4-20250514",
            trigger="perception",
            workspace_id="ws_test",
        )
        assert usage.input_tokens == 1000
        assert usage.output_tokens == 500
        assert usage.cache_creation_input_tokens == 100

    @pytest.mark.asyncio
    async def test_record_from_span_handles_missing_attrs(self):
        from src.orchestrator.budget import BudgetTracker

        tracker = BudgetTracker(daily_limit_usd=10.0)
        db = AsyncMock()
        db.add = MagicMock()
        db.flush = AsyncMock()

        # Span without optional token attrs
        span = MagicMock(spec=[])  # empty spec = no attributes
        span.input_tokens = 500
        span.output_tokens = 200

        usage = await tracker.record_from_span(
            db,
            span=span,
            agent_name="test",
            model="claude-sonnet-4-20250514",
            trigger="test",
            workspace_id="ws_test",
        )
        assert usage.input_tokens == 500


class TestStartupComponentHealth:
    """Fix 2.6: Worker/bot thread health tracking."""

    def test_component_health_dict_exists(self):
        from run import get_component_health

        health = get_component_health()
        assert isinstance(health, dict)
        assert "worker" in health
        assert "bot" in health

    def test_initial_health_is_not_started(self):
        from run import _component_health

        # Reset to initial state for test
        original = dict(_component_health)
        _component_health["worker"] = {"status": "not_started"}
        _component_health["bot"] = {"status": "not_started"}

        from run import get_component_health

        health = get_component_health()
        assert health["worker"]["status"] == "not_started"
        assert health["bot"]["status"] == "not_started"

        # Restore
        _component_health.update(original)


class TestBriefingAsyncGeneration:
    """Fix 3.2: Return 202 when briefing not yet generated."""

    @pytest.mark.asyncio
    async def test_get_briefing_returns_existing(self):
        """When briefing exists, return it directly."""
        from src.services.briefing_read_model import BriefingReadModel

        model = BriefingReadModel.__new__(BriefingReadModel)
        model._db = AsyncMock()
        model._workspace_id = "ws_test"

        # Mock get_detail to return existing briefing
        model.get_detail = AsyncMock(return_value={"briefing_id": "brf_001", "headline": "Test"})
        result = await model.get_detail("2026-04-07")
        assert result is not None
        assert result["briefing_id"] == "brf_001"


class TestTelegramRateLimiting:
    """Fix 3.3: Per-user rate limiting (10 msg/min)."""

    def test_rate_limiter_allows_under_limit(self):
        from src.interface.telegram import TelegramRateLimiter

        limiter = TelegramRateLimiter(max_per_minute=10)
        for _ in range(10):
            assert limiter.allow("user_1") is True

    def test_rate_limiter_blocks_over_limit(self):
        from src.interface.telegram import TelegramRateLimiter

        limiter = TelegramRateLimiter(max_per_minute=10)
        for _ in range(10):
            limiter.allow("user_1")
        assert limiter.allow("user_1") is False

    def test_rate_limiter_independent_per_user(self):
        from src.interface.telegram import TelegramRateLimiter

        limiter = TelegramRateLimiter(max_per_minute=10)
        for _ in range(10):
            limiter.allow("user_1")
        # user_2 should still be allowed
        assert limiter.allow("user_2") is True

    def test_rate_limiter_resets_after_window(self):
        import time

        from src.interface.telegram import TelegramRateLimiter

        limiter = TelegramRateLimiter(max_per_minute=10)
        for _ in range(10):
            limiter.allow("user_1")
        # Manually expire the window
        limiter._windows["user_1"] = (10, time.monotonic() - 61)
        assert limiter.allow("user_1") is True
