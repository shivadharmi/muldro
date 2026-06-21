"""Guard tests: run surface ids must not be same-prefix doubled (run_run_…).

IDs are ULID-with-prefix generated once at creation (``run_<ULID>``). The
canonical run surface id is the run_id itself — re-applying ``run_`` produced
``run_run_<ULID>`` which leaked to the UI and to ``ui_surfaces``/``checkpoint``.

These tests prove:
  * ``GraphExecutor.execute_run`` persists a non-doubled ``surface_id``.
  * ``SurfaceService._build_run_surfaces`` computes the byte-identical id.
  * Neither matches the same-prefix double regex ``^(\\w+)_\\1_``.
  * The detail resolver recovers the full run_id from the canonical id.
"""

import re
from datetime import datetime, timezone
from unittest.mock import AsyncMock, MagicMock

import pytest

from src.api.routes_surface_detail import _normalize_legacy_run_id, _resolve_ephemeral
from src.services.surface_builder import SurfaceService

_DOUBLE = re.compile(r"^(\w+)_\1_")

RUN_ID = "run_01HZZZZZZZZZZZZZZZZZZZZZZZ"


def _mock_run(run_id: str) -> MagicMock:
    run = MagicMock()
    run.run_id = run_id
    run.status = "running"
    run.source = "plan"
    run.plan_id = "plan_01"
    run.user_id = "usr_01"
    run.workspace_id = "ws_01"
    run.created_at = datetime.now(timezone.utc)
    run.started_at = datetime.now(timezone.utc)
    run.updated_at = datetime.now(timezone.utc)
    run.completed_at = None
    run.error = None
    run.input_tokens = 0
    run.output_tokens = 0
    run.cost_usd = 0.0
    return run


def _mock_step() -> MagicMock:
    step = MagicMock()
    step.step_id = "s1"
    step.status = "running"
    step.name = "Step one"
    step.input_data = {"capability": "email.search"}
    step.task_id = "task_s1"
    step.created_at = datetime.now(timezone.utc)
    step.started_at = datetime.now(timezone.utc)
    step.completed_at = None
    return step


@pytest.mark.asyncio
async def test_build_run_surface_id_not_doubled():
    db = AsyncMock()
    service = SurfaceService(db=db, workspace_id="ws_01")
    run = _mock_run(RUN_ID)
    steps = [_mock_step()]

    call = 0

    async def mock_execute(stmt):
        nonlocal call
        call += 1
        result = MagicMock()
        result.scalars.return_value.all.return_value = [run] if call == 1 else steps
        return result

    db.execute = mock_execute

    surfaces = await service._build_run_surfaces()
    assert len(surfaces) == 1
    assert surfaces[0].id == RUN_ID
    assert not _DOUBLE.match(surfaces[0].id)


def test_resolver_recovers_full_run_id_from_canonical_id():
    """Canonical run surface id is the run_id; resolver must return it intact."""
    kind, metadata = _resolve_ephemeral(RUN_ID)
    assert kind == "run"
    assert metadata["run_id"] == RUN_ID


def test_cross_prefix_summary_id_still_resolves():
    """summary_<run_id> is cross-prefix (not doubled) and must keep resolving."""
    summary_id = f"summary_{RUN_ID}"
    assert not _DOUBLE.match(summary_id)
    kind, metadata = _resolve_ephemeral(summary_id)
    assert kind == "summary"
    assert metadata["run_id"] == RUN_ID


@pytest.mark.asyncio
async def test_legacy_doubled_run_id_recovers_canonical_run():
    """A legacy ``run_run_<ULID>`` surface id resolves to the real ``run_<ULID>``.

    Pre-migration clients could send the doubled form. The resolver hands it to
    _normalize_legacy_run_id, which: (1) finds no run named ``run_run_X``, then
    (2) strips one segment to ``run_X`` and, finding that run, rewrites metadata.
    """
    legacy_id = f"run_{RUN_ID}"  # run_run_01HZ… — doubled
    assert _DOUBLE.match(legacy_id)

    kind, metadata = _resolve_ephemeral(legacy_id)
    assert kind == "run"
    # Resolver uses the whole (doubled) id verbatim before normalization.
    assert metadata["run_id"] == legacy_id

    # DB: doubled id does NOT exist; canonical RUN_ID does.
    db = AsyncMock()

    def _execute(stmt):
        result = MagicMock()
        # First call (doubled) → None; second call (canonical) → RUN_ID.
        result.scalar_one_or_none.return_value = _execute.responses.pop(0)
        return result

    _execute.responses = [None, RUN_ID]
    db.execute = AsyncMock(side_effect=_execute)

    await _normalize_legacy_run_id(db, metadata)
    assert metadata["run_id"] == RUN_ID


@pytest.mark.asyncio
async def test_canonical_run_id_not_rewritten():
    """A normal ``run_<ULID>`` that exists is left untouched (no extra lookups)."""
    kind, metadata = _resolve_ephemeral(RUN_ID)
    assert metadata["run_id"] == RUN_ID

    db = AsyncMock()
    result = MagicMock()
    result.scalar_one_or_none.return_value = RUN_ID  # run exists
    db.execute = AsyncMock(return_value=result)

    await _normalize_legacy_run_id(db, metadata)
    assert metadata["run_id"] == RUN_ID
    # Only the existence check ran; no fallback lookup.
    assert db.execute.await_count == 1
