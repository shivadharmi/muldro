"""A durable (state-recording) runtime event must NOT swallow a DB-persist failure:
the failure propagates so the state-change transaction aborts atomically. A
non-durable (best-effort) event still swallows (unchanged behavior). Redis stays
best-effort in both modes (Step 5 §4.8, D-A2)."""

from unittest.mock import AsyncMock, MagicMock

import pytest

from src.services.execution_surface_emitter import SurfaceEmitter


def _emitter_with_failing_flush():
    db = MagicMock()
    db.add = MagicMock()
    db.flush = AsyncMock(side_effect=RuntimeError("db down"))
    settings = MagicMock()
    # No event_bus/redis so we isolate the DB-persist path.
    return SurfaceEmitter(settings=settings, db=db, event_bus=None, redis=None, db_factory=None)


async def test_durable_event_propagates_persist_failure():
    emitter = _emitter_with_failing_flush()
    with pytest.raises(RuntimeError, match="db down"):
        await emitter.emit_event(
            "step_completed",
            "usr_1",
            {"run_id": "run_1", "step_id": "s1"},
            workspace_id="ws_1",
            durable=True,
        )


async def test_nondurable_event_swallows_persist_failure():
    emitter = _emitter_with_failing_flush()
    # Must NOT raise (default best-effort behavior preserved).
    await emitter.emit_event("surface_created", "usr_1", {"run_id": "run_1"}, workspace_id="ws_1")


async def test_default_is_nondurable():
    emitter = _emitter_with_failing_flush()
    await emitter.emit_event("step.started", "usr_1", {"run_id": "run_1"}, workspace_id="ws_1")
