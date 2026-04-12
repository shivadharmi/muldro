"""Tests for _resolve_step_references warning logging."""

import logging
from unittest.mock import AsyncMock, MagicMock, patch

import pytest


def _make_step(task_id="step_1", input_data=None):
    step = MagicMock()
    step.task_id = task_id
    step.step_id = f"sid_{task_id}"
    step.input_data = input_data or {}
    return step


def _make_upstream_step(task_id, output_data):
    step = MagicMock()
    step.task_id = task_id
    step.output_data = output_data
    return step


def _make_executor():
    with patch("src.services.graph_executor.get_anthropic_client") as mock_client:
        mock_client.return_value = MagicMock()
        from src.services.graph_executor import GraphExecutor
        from tests.conftest import make_mock_settings

        db = AsyncMock()
        return GraphExecutor(make_mock_settings(), db)


def _mock_get_all_steps(executor, upstream_steps):
    """Mock _get_all_steps to return the given upstream steps."""
    executor._get_all_steps = AsyncMock(return_value=upstream_steps)


@pytest.mark.asyncio
async def test_unresolved_task_reference_logs_warning(caplog):
    """Reference to missing task -> warning with 'not found'."""
    executor = _make_executor()
    step = _make_step(
        task_id="downstream",
        input_data={"query": "{missing_task}.output.result"},
    )
    _mock_get_all_steps(executor, [])

    with caplog.at_level(logging.WARNING, logger="src.services.graph_executor"):
        result = await executor._resolve_step_references(step, "run_001")

    assert result["query"] == "{missing_task}.output.result"
    warnings = [r for r in caplog.records if r.levelno == logging.WARNING]
    assert any("not found" in r.message for r in warnings)


@pytest.mark.asyncio
async def test_missing_field_in_output_logs_warning(caplog):
    """Field missing in upstream output -> warning with 'not in'."""
    executor = _make_executor()
    step = _make_step(
        task_id="downstream",
        input_data={"query": "{upstream}.output.nonexistent"},
    )
    upstream = _make_upstream_step("upstream", {"result": "ok"})
    _mock_get_all_steps(executor, [upstream])

    with caplog.at_level(logging.WARNING, logger="src.services.graph_executor"):
        result = await executor._resolve_step_references(step, "run_002")

    assert result["query"] == "{upstream}.output.nonexistent"
    warnings = [r for r in caplog.records if r.levelno == logging.WARNING]
    assert any("not in" in r.message for r in warnings)


@pytest.mark.asyncio
async def test_successful_resolution_no_warnings(caplog):
    """Successful resolution -> no warnings."""
    executor = _make_executor()
    step = _make_step(
        task_id="downstream",
        input_data={"query": "{upstream}.output.result"},
    )
    upstream = _make_upstream_step("upstream", {"result": "hello"})
    _mock_get_all_steps(executor, [upstream])

    with caplog.at_level(logging.WARNING, logger="src.services.graph_executor"):
        result = await executor._resolve_step_references(step, "run_003")

    assert result["query"] == "hello"
    warnings = [r for r in caplog.records if r.levelno == logging.WARNING]
    assert len(warnings) == 0
