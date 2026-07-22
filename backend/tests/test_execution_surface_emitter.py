"""Characterization tests for the surface/event emission cluster.

These freeze the observable behavior of the emission methods
(``_emit_event``, ``_publish_progress``, ``_emit_surface_update``,
``_emit_summary_surface``) BEFORE the SurfaceEmitter collaborator extraction
(SVC-P1-3). They drive the methods through the GraphExecutor hub so they remain
valid both before extraction (methods live on the hub) and after (hub forwards
to the collaborator). They must stay green across the structural change.
"""

import json
import types
from unittest.mock import AsyncMock, MagicMock, patch

import pytest

from tests.conftest import make_mock_settings


def _make_executor(redis_mock=None, event_bus=None, db_factory=None):
    from src.services.graph_executor import GraphExecutor

    settings = make_mock_settings()
    db = AsyncMock()
    executor = GraphExecutor(
        settings=settings,
        db=db,
        event_bus=event_bus,
        redis=redis_mock,
        db_factory=db_factory,
    )
    return executor


class TestEmitSurfaceUpdate:
    async def test_publishes_surface_update_to_redis_channel(self):
        redis = AsyncMock()
        executor = _make_executor(redis_mock=redis)

        await executor._emit_surface_update(
            surface_id="surf_abc",
            user_id="usr_01",
            phase="executing",
            progress="running step 1",
        )

        redis.publish.assert_called_once()
        channel, raw = redis.publish.call_args.args
        assert channel == "jarvis:a2ui:usr_01"
        payload = json.loads(raw)
        assert payload["type"] == "surface_update"
        assert payload["surface_id"] == "surf_abc"
        assert payload["phase"] == "executing"
        assert payload["progress"] == "running step 1"

    async def test_no_op_without_surface_id(self):
        redis = AsyncMock()
        executor = _make_executor(redis_mock=redis)

        await executor._emit_surface_update(
            surface_id=None,
            user_id="usr_01",
            phase="plan_ready",
        )

        redis.publish.assert_not_called()

    async def test_falls_back_to_event_bus_when_no_redis(self):
        event_bus = AsyncMock()
        executor = _make_executor(redis_mock=None, event_bus=event_bus)

        await executor._emit_surface_update(
            surface_id="surf_xyz",
            user_id="usr_02",
            phase="completed",
        )

        event_bus.publish_to_channel.assert_called_once()
        channel = event_bus.publish_to_channel.call_args.args[0]
        assert channel == "jarvis:a2ui:usr_02"


class TestEmitEventProgress:
    async def test_run_event_triggers_progress_publish(self):
        redis = AsyncMock()
        event_bus = MagicMock()
        event_bus.agent_stream = MagicMock(return_value="stream")
        event_bus.publish = AsyncMock()
        executor = _make_executor(redis_mock=redis, event_bus=event_bus)

        await executor._emit_event(
            "step.completed", "usr_1", {"run_id": "run_001", "step_id": "s1"}
        )

        # _emit_event publishes progress to the run_progress channel for run events
        redis.publish.assert_called_once()
        channel, raw = redis.publish.call_args.args
        assert channel == "jarvis:run_progress:run_001"
        payload = json.loads(raw)
        assert payload["event_type"] == "step.completed"
        assert payload["run_id"] == "run_001"

    async def test_event_without_run_id_skips_progress(self):
        redis = AsyncMock()
        event_bus = MagicMock()
        event_bus.agent_stream = MagicMock(return_value="stream")
        event_bus.publish = AsyncMock()
        executor = _make_executor(redis_mock=redis, event_bus=event_bus)

        await executor._emit_event("memory.updated", "usr_1", {"memory_id": "mem_001"})

        redis.publish.assert_not_called()


class TestEmitSummarySurface:
    def _make_db_factory(self):
        """A db_factory whose async context manager yields a db supporting the
        execute/add/commit calls used by _emit_summary_surface."""
        step_result = MagicMock()
        step_result.scalars.return_value.all.return_value = []
        run_result = MagicMock()
        run_result.scalar_one_or_none.return_value = None

        db = AsyncMock()
        db.execute = AsyncMock(side_effect=[step_result, run_result])
        db.add = MagicMock()
        db.commit = AsyncMock()

        cm = MagicMock()
        cm.__aenter__ = AsyncMock(return_value=db)
        cm.__aexit__ = AsyncMock(return_value=False)
        return MagicMock(return_value=cm), db

    async def test_publishes_summary_surface_to_workspace_feed(self):
        redis = AsyncMock()
        db_factory, _db = self._make_db_factory()
        executor = _make_executor(redis_mock=redis, db_factory=db_factory)

        run = types.SimpleNamespace(
            run_id="run_777",
            user_id="usr_5",
            workspace_id="ws_1",
            status="completed",
            error=None,
            input_tokens=100,
            output_tokens=50,
            cost_usd=0.01,
        )

        await executor._emit_summary_surface(run, "run_surface_777")

        # A "surface" message (kind=summary) is published to the a2ui feed.
        assert redis.publish.await_count >= 1
        channel, raw = redis.publish.call_args.args
        assert channel == "jarvis:a2ui:usr_5"
        payload = json.loads(raw)
        assert payload["type"] == "surface"
        assert payload["surface"]["kind"] == "summary"
        assert payload["surface"]["source_run_id"] == "run_777"

    async def test_no_op_without_db_factory(self):
        redis = AsyncMock()
        executor = _make_executor(redis_mock=redis, db_factory=None)

        run = types.SimpleNamespace(
            run_id="run_888",
            user_id="usr_6",
            workspace_id="ws_2",
            status="completed",
            error=None,
            input_tokens=0,
            output_tokens=0,
            cost_usd=0.0,
        )

        await executor._emit_summary_surface(run, "run_surface_888")

        redis.publish.assert_not_called()


class TestPublishProgress:
    async def test_uses_injected_redis(self):
        redis = AsyncMock()
        executor = _make_executor(redis_mock=redis)

        await executor._publish_progress("run_01", {"event": "test"})

        redis.publish.assert_called_once()
        channel = redis.publish.call_args.args[0]
        assert channel == "jarvis:run_progress:run_01"

    async def test_tolerates_redis_error(self):
        executor = _make_executor(redis_mock=None)
        with patch("redis.asyncio.from_url", side_effect=RuntimeError("down")):
            # best-effort: must not raise
            await executor._publish_progress("run_02", {"data": "x"})


if __name__ == "__main__":
    pytest.main([__file__, "-v"])
