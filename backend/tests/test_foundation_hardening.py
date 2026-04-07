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
