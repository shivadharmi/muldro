"""Tests for Phase 4C: WebSocket progress + Redis pubsub publishing."""

import json
from unittest.mock import AsyncMock, MagicMock, patch

from tests.conftest import make_mock_settings


class TestPublishProgress:
    """Tests for GraphExecutor._publish_progress and Redis integration."""

    def _make_executor(self):
        from src.services.graph_executor import GraphExecutor

        settings = make_mock_settings()
        db = AsyncMock()

        with patch("src.services.graph_executor.get_anthropic_client"):
            executor = GraphExecutor(settings=settings, db=db)
        return executor

    async def test_publish_progress_sends_to_redis(self):
        executor = self._make_executor()

        mock_redis = AsyncMock()
        mock_redis.publish = AsyncMock()
        mock_redis.close = AsyncMock()

        with patch("redis.asyncio.from_url", return_value=mock_redis):
            await executor._publish_progress("run_001", {"step_id": "s1", "status": "completed"})

        mock_redis.publish.assert_called_once()
        channel, data = mock_redis.publish.call_args[0]
        assert channel == "jarvis:run_progress:run_001"
        parsed = json.loads(data)
        assert parsed["step_id"] == "s1"
        assert parsed["status"] == "completed"

    async def test_publish_progress_closes_redis(self):
        executor = self._make_executor()

        mock_redis = AsyncMock()
        mock_redis.publish = AsyncMock()
        mock_redis.close = AsyncMock()

        with patch("redis.asyncio.from_url", return_value=mock_redis):
            await executor._publish_progress("run_002", {"event": "test"})

        mock_redis.close.assert_called_once()

    async def test_publish_progress_tolerates_redis_error(self):
        executor = self._make_executor()

        with patch("redis.asyncio.from_url", side_effect=RuntimeError("Redis down")):
            # Should not raise
            await executor._publish_progress("run_003", {"data": "x"})

    async def test_emit_event_triggers_progress_for_run_events(self):
        """Events with run_id in payload trigger Redis progress publish."""
        executor = self._make_executor()
        executor._event_bus = MagicMock()
        executor._event_bus.agent_stream = MagicMock(return_value="stream")
        executor._event_bus.publish = AsyncMock()
        executor._publish_progress = AsyncMock()

        await executor._emit_event(
            "step.completed", "usr_1", {"run_id": "run_001", "step_id": "s1"}
        )

        executor._publish_progress.assert_called_once_with(
            "run_001", {"event_type": "step.completed", "run_id": "run_001", "step_id": "s1"}
        )

    async def test_emit_event_skips_progress_without_run_id(self):
        """Events without run_id don't trigger progress publish."""
        executor = self._make_executor()
        executor._event_bus = MagicMock()
        executor._event_bus.agent_stream = MagicMock(return_value="stream")
        executor._event_bus.publish = AsyncMock()
        executor._publish_progress = AsyncMock()

        await executor._emit_event("memory.updated", "usr_1", {"memory_id": "mem_001"})

        executor._publish_progress.assert_not_called()


