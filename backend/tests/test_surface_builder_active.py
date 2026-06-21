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
    run.updated_at = datetime.now(timezone.utc)
    run.completed_at = None
    run.error = None
    run.input_tokens = 1200
    run.output_tokens = 800
    run.cost_usd = 0.0345
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

        surfaces = await service._build_run_surfaces()

        assert len(surfaces) == 1
        s = surfaces[0]
        assert s.kind == "run"
        assert s.id == "run_run_01"
        assert s.source_run_id == "run_01"
        preview = s.preview
        assert preview["status"] == "running"
        assert preview["progress"] is not None

    @pytest.mark.asyncio
    async def test_no_active_runs_returns_empty(self):
        db = AsyncMock()
        service = SurfaceService(db=db, workspace_id="ws_01")

        result = MagicMock()
        result.scalars.return_value.all.return_value = []
        db.execute = AsyncMock(return_value=result)

        surfaces = await service._build_run_surfaces()
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
            elif call_count == 2:
                result.scalars.return_value.all.return_value = steps
            else:
                # _approval_risk_and_flags lookup — no approval row
                result.scalar_one_or_none.return_value = None
            return result

        db.execute = mock_execute

        surfaces = await service._build_run_surfaces()
        assert len(surfaces) == 1
        assert surfaces[0].kind == "run"
        assert surfaces[0].source_run_id == "run_03"
        # awaiting_approval runs should surface as awaiting_approval status
        assert surfaces[0].preview["status"] == "awaiting_approval"

    @pytest.mark.asyncio
    async def test_awaiting_approval_carries_risk_and_flags(self):
        """Approval context populates risk + flags (Irreversible, trust level)."""
        db = AsyncMock()
        service = SurfaceService(db=db, workspace_id="ws_01")

        run = _mock_run("run_appr", "awaiting_approval")
        steps = [_mock_step("s1", "running", "Send email")]

        approval = MagicMock()
        approval.risk_level = "high"
        approval.artifact_refs = {"tool_name": "email.send", "reversible": False}

        trust_state = MagicMock()
        trust_state.trust_level = "learning"
        trust_state.approved_count = 3

        call_count = 0

        async def mock_execute(stmt):
            nonlocal call_count
            call_count += 1
            result = MagicMock()
            if call_count == 1:
                result.scalars.return_value.all.return_value = [run]
            elif call_count == 2:
                result.scalars.return_value.all.return_value = steps
            elif call_count == 3:
                # _approval_risk_and_flags → pending approval
                result.scalar_one_or_none.return_value = approval
            else:
                # _get_trust_context → TrustState lookup
                result.scalar_one_or_none.return_value = trust_state
            return result

        db.execute = mock_execute

        surfaces = await service._build_run_surfaces()
        preview = surfaces[0].preview
        assert preview["risk"] == "high"
        assert "Irreversible" in preview["flags"]
        assert "LEARNING" in preview["flags"]

    @pytest.mark.asyncio
    async def test_failed_run_produces_alert_surface(self):
        """FAILED runs become a kind='alert' surface with an error subtitle."""
        db = AsyncMock()
        service = SurfaceService(db=db, workspace_id="ws_01")

        run = _mock_run("run_fail", "failed")
        run.completed_at = datetime.now(timezone.utc)
        run.error = {"message": "SMTP connection refused"}

        result = MagicMock()
        result.scalars.return_value.all.return_value = [run]
        db.execute = AsyncMock(return_value=result)

        surfaces = await service._build_alert_surfaces()
        assert len(surfaces) == 1
        s = surfaces[0]
        assert s.kind == "alert"
        assert s.id == "alert_run_fail"
        assert s.source_run_id == "run_fail"
        assert s.preview["status"] == "failed"
        assert s.preview["priority"] == "high"
        assert "SMTP connection refused" in s.preview["subtitle"]

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

        surfaces = await service._build_run_surfaces()
        assert len(surfaces) == 1
