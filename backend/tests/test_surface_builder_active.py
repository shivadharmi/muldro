"""Tests for SurfaceService active execution surfaces."""

from datetime import datetime, timezone
from unittest.mock import AsyncMock, MagicMock

import pytest

from src.services.surface_builder import SurfaceService


def _mock_run(run_id: str, status: str, source: str = "plan") -> MagicMock:
    run = MagicMock()
    run.run_id = run_id
    run.status = status
    run.source = source
    run.plan_id = "plan_01"
    run.user_id = "usr_01"
    run.workspace_id = "ws_01"
    run.created_at = datetime.now(timezone.utc)
    run.started_at = datetime.now(timezone.utc)
    return run


def _mock_step(step_id: str, status: str, name: str | None = None) -> MagicMock:
    step = MagicMock()
    step.step_id = step_id
    step.status = status
    step.name = name
    step.input_data = {"capability": "email.search"}
    step.task_id = f"task_{step_id}"
    step.created_at = datetime.now(timezone.utc)
    step.started_at = datetime.now(timezone.utc) if status != "pending" else None
    step.completed_at = datetime.now(timezone.utc) if status == "completed" else None
    return step


class TestBuildActiveExecutionSurfaces:
    @pytest.mark.asyncio
    async def test_running_run_produces_surface(self):
        db = AsyncMock()
        service = SurfaceService(db=db, workspace_id="ws_01")

        run = _mock_run("run_01", "running")
        steps = [
            _mock_step("s1", "completed", "Search emails"),
            _mock_step("s2", "running", "Draft reply"),
            _mock_step("s3", "pending", "Send email"),
        ]

        call_count = 0

        async def mock_execute(stmt):
            nonlocal call_count
            call_count += 1
            result = MagicMock()
            if call_count == 1:
                result.scalars.return_value.all.return_value = [run]
            else:
                result.scalars.return_value.all.return_value = steps
            return result

        db.execute = mock_execute

        surfaces = await service._build_active_execution_surfaces()

        assert len(surfaces) == 1
        s = surfaces[0]
        assert s["kind"] == "plan"
        assert s["id"] == "exec_run_01"
        assert s["source_run_id"] == "run_01"
        preview = s["preview"]
        assert preview["status"] == "running"
        assert preview["progress"] is not None

    @pytest.mark.asyncio
    async def test_no_active_runs_returns_empty(self):
        db = AsyncMock()
        service = SurfaceService(db=db, workspace_id="ws_01")

        result = MagicMock()
        result.scalars.return_value.all.return_value = []
        db.execute = AsyncMock(return_value=result)

        surfaces = await service._build_active_execution_surfaces()
        assert len(surfaces) == 0

    @pytest.mark.asyncio
    async def test_awaiting_approval_run_included(self):
        """H-14: Runs with awaiting_approval should appear in active execution surfaces."""
        db = AsyncMock()
        service = SurfaceService(db=db, workspace_id="ws_01")

        run = _mock_run("run_03", "awaiting_approval")
        steps = [
            _mock_step("s1", "completed", "Search"),
            _mock_step("s2", "running", "Send email"),
        ]

        call_count = 0

        async def mock_execute(stmt):
            nonlocal call_count
            call_count += 1
            result = MagicMock()
            if call_count == 1:
                result.scalars.return_value.all.return_value = [run]
            else:
                result.scalars.return_value.all.return_value = steps
            return result

        db.execute = mock_execute

        surfaces = await service._build_active_execution_surfaces()
        assert len(surfaces) == 1
        assert surfaces[0]["kind"] == "plan"
        assert surfaces[0]["source_run_id"] == "run_03"

    @pytest.mark.asyncio
    async def test_paused_run_included(self):
        db = AsyncMock()
        service = SurfaceService(db=db, workspace_id="ws_01")

        run = _mock_run("run_02", "paused")
        steps = [_mock_step("s1", "completed", "Search")]

        call_count = 0

        async def mock_execute(stmt):
            nonlocal call_count
            call_count += 1
            result = MagicMock()
            if call_count == 1:
                result.scalars.return_value.all.return_value = [run]
            else:
                result.scalars.return_value.all.return_value = steps
            return result

        db.execute = mock_execute

        surfaces = await service._build_active_execution_surfaces()
        assert len(surfaces) == 1
