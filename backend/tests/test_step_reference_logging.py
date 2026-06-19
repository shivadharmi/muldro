"""Tests for StepGraphStore.resolve_step_references warning logging.

Retargeted to the StepGraphStore collaborator (extracted from GraphExecutor in
the 2026-06-20 decomposition). The executor exposes ``_resolve_step_references``
as a thin facade over ``self._store.resolve_step_references``; the resolution
logic and its warnings now live in ``src.services.step_graph_store``.
"""

import logging
from unittest.mock import AsyncMock, MagicMock

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


def _make_store(upstream_steps):
    """Build a StepGraphStore whose get_all_steps returns the given steps."""
    from src.services.step_graph_store import StepGraphStore

    store = StepGraphStore(AsyncMock())
    store.get_all_steps = AsyncMock(return_value=upstream_steps)
    return store


@pytest.mark.asyncio
async def test_unresolved_task_reference_logs_warning(caplog):
    """Reference to missing task -> warning with 'not found'."""
    store = _make_store([])
    step = _make_step(
        task_id="downstream",
        input_data={"query": "{missing_task}.output.result"},
    )

    with caplog.at_level(logging.WARNING, logger="src.services.step_graph_store"):
        result = await store.resolve_step_references(step, "run_001")

    assert result["query"] == "{missing_task}.output.result"
    warnings = [r for r in caplog.records if r.levelno == logging.WARNING]
    assert any("not found" in r.message for r in warnings)


@pytest.mark.asyncio
async def test_missing_field_in_output_logs_warning(caplog):
    """Field missing in upstream output -> warning with 'not in'."""
    step = _make_step(
        task_id="downstream",
        input_data={"query": "{upstream}.output.nonexistent"},
    )
    upstream = _make_upstream_step("upstream", {"result": "ok"})
    store = _make_store([upstream])

    with caplog.at_level(logging.WARNING, logger="src.services.step_graph_store"):
        result = await store.resolve_step_references(step, "run_002")

    assert result["query"] == "{upstream}.output.nonexistent"
    warnings = [r for r in caplog.records if r.levelno == logging.WARNING]
    assert any("not in" in r.message for r in warnings)


@pytest.mark.asyncio
async def test_successful_resolution_no_warnings(caplog):
    """Successful resolution -> no warnings."""
    step = _make_step(
        task_id="downstream",
        input_data={"query": "{upstream}.output.result"},
    )
    upstream = _make_upstream_step("upstream", {"result": "hello"})
    store = _make_store([upstream])

    with caplog.at_level(logging.WARNING, logger="src.services.step_graph_store"):
        result = await store.resolve_step_references(step, "run_003")

    assert result["query"] == "hello"
    warnings = [r for r in caplog.records if r.levelno == logging.WARNING]
    assert len(warnings) == 0
