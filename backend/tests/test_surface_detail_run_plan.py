"""Regression tests for build_run_plan_tab attribute mapping.

Reproduces the production HTTP 500 where build_run_plan_tab read
``plan.reasoning`` / ``plan.success_criteria`` which do not exist on the
``Plan`` model. The real columns are ``reasoning_summary`` (Text) and
``success_conditions`` (JSONB dict).
"""

from unittest.mock import AsyncMock, MagicMock

import pytest

from src.models.plans import Plan
from src.models.task_graph import TaskRun
from src.services.surface_detail_builders.run import build_run_plan_tab
from src.ui.contracts import DetailTabResponse


def _mock_run_surface(run_id: str = "run_abc123"):
    s = MagicMock()
    s.surface_id = run_id
    s.surface_type = "run"
    s.payload = {}
    s.workspace_id = "ws_test"
    return s


def _mock_db(run: TaskRun, plan: Plan | None) -> AsyncMock:
    """Mock async db whose execute() returns run then plan on successive calls."""
    run_result = MagicMock()
    run_result.scalar_one_or_none.return_value = run
    plan_result = MagicMock()
    plan_result.scalar_one_or_none.return_value = plan

    db = AsyncMock()
    db.execute.side_effect = [run_result, plan_result]
    return db


def _build_run(plan_id: str = "plan_xyz") -> TaskRun:
    run = TaskRun()
    run.run_id = "run_abc123"  # post-4893e16: run surface_id IS the run_id
    run.plan_id = plan_id
    run.workspace_id = "ws_test"
    return run


def _build_plan() -> Plan:
    plan = Plan()
    plan.plan_id = "plan_xyz"
    plan.goal = "Ship the perception fix"
    plan.reasoning_summary = "User asked to remediate the polling subsystem."
    plan.success_conditions = {"tests": "green", "coverage": ">=80%"}
    plan.priority = "high"
    plan.trigger_type = "chat"
    return plan


@pytest.mark.asyncio
async def test_build_run_plan_tab_uses_model_attributes():
    """Builder must read reasoning_summary + success_conditions without raising."""
    plan = _build_plan()
    run = _build_run()
    db = _mock_db(run, plan)

    result = await build_run_plan_tab(db, _mock_run_surface())

    assert isinstance(result, DetailTabResponse)
    assert result.tab_id == "plan"
    assert len(result.sections) > 0

    # Reasoning text and success-condition values must surface in the tree.
    rendered = str(result.model_dump())
    assert "User asked to remediate the polling subsystem." in rendered
    assert "green" in rendered
    assert "Ship the perception fix" in rendered


@pytest.mark.asyncio
async def test_build_run_plan_tab_none_safe():
    """None reasoning_summary / success_conditions must not raise."""
    plan = _build_plan()
    plan.reasoning_summary = None
    plan.success_conditions = None
    run = _build_run()
    db = _mock_db(run, plan)

    result = await build_run_plan_tab(db, _mock_run_surface())

    assert isinstance(result, DetailTabResponse)
    assert result.tab_id == "plan"
    assert len(result.sections) > 0
