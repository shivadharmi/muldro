"""Unit tests for StepRunner.minimal_claude_action — the raw-SDK fallback re-homed onto
the shared ``complete_text`` seam (Step 11 Phase 2). No DB, no network."""

from types import SimpleNamespace
from unittest.mock import AsyncMock, MagicMock, patch

import pytest

from src.services.step_runner import StepRunner
from tests.conftest import make_mock_settings


def _runner():
    return StepRunner(
        settings=make_mock_settings(),
        client=MagicMock(),
        store=MagicMock(),
        emitter=MagicMock(),
        db_factory_provider=lambda: None,
        active_traces_provider=lambda: {},
        tool_registry=None,
        execute_tool_fn=None,
    )


def _step():
    return SimpleNamespace(input_data={"capability": "system.respond", "goal": "say hi"})


def _run():
    return SimpleNamespace(user_id="usr_test", workspace_id="ws_test")


async def _empty_ctx(run, step):  # noqa: ANN001, ARG001
    return ""


@pytest.mark.asyncio
async def test_minimal_claude_action_parses_json(monkeypatch):
    runner = _runner()
    monkeypatch.setattr(runner, "build_step_context", _empty_ctx)

    with patch(
        "src.services.step_runner.complete_text",
        AsyncMock(return_value='{"status": "completed", "result": "hi"}'),
    ):
        out = await runner.minimal_claude_action(_step(), _run())
    assert out == {"status": "completed", "result": "hi"}


@pytest.mark.asyncio
async def test_minimal_claude_action_falls_back_on_non_json(monkeypatch):
    runner = _runner()
    monkeypatch.setattr(runner, "build_step_context", _empty_ctx)

    with patch(
        "src.services.step_runner.complete_text",
        AsyncMock(return_value="not json, just prose"),
    ):
        out = await runner.minimal_claude_action(_step(), _run())
    # parse_llm_json finds no JSON value -> JSONDecodeError -> raw-text fallback.
    assert out["status"] == "completed"
    assert out["result"] == "not json, just prose"


@pytest.mark.asyncio
async def test_minimal_claude_action_falls_back_on_a_json_array(monkeypatch):
    """A JSON ARRAY parses SUCCESSFULLY, so the `except JSONDecodeError` never fires and a
    list escapes to a caller that treats the return as a step-result dict."""
    runner = _runner()
    monkeypatch.setattr(runner, "build_step_context", _empty_ctx)

    with patch(
        "src.services.step_runner.complete_text",
        AsyncMock(return_value='["completed", "hi"]'),
    ):
        out = await runner.minimal_claude_action(_step(), _run())

    assert isinstance(out, dict)
    assert out["status"] == "completed"
    assert out["result"] == '["completed", "hi"]'
