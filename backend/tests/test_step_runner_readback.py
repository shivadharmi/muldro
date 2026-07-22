"""Unit tests for StepRunner.run_readback — the ONE connector-call integration seam
of the §4.5 read-back verifier.

Covers:
  - the D8 production-safety denylist (calendar.get -> raises, so the verifier fails
    SAFE to UNVERIFIED rather than risking a false CONTRADICTED on a real success);
  - the resolvable path: capability -> tool.name via the registry's ToolDefinition,
    invoked as execute_tool_fn(name, read_args, user_id=..., workspace_id=...).
No DB, no network — tool_registry and execute_tool_fn are fakes.
"""

from types import SimpleNamespace
from unittest.mock import AsyncMock, MagicMock

import pytest

from src.services.step_runner import StepRunner
from tests.conftest import make_mock_settings


def _make_runner(*, tool_registry=None, execute_tool_fn=None):
    return StepRunner(
        settings=make_mock_settings(),
        client=MagicMock(),
        store=MagicMock(),
        emitter=MagicMock(),
        db_factory_provider=lambda: None,
        active_traces_provider=lambda: {},
        tool_registry=tool_registry,
        execute_tool_fn=execute_tool_fn,
    )


def _make_run():
    run = MagicMock()
    run.user_id = "usr_test"
    run.workspace_id = "ws_test"
    return run


class _FakeRegistry:
    def __init__(self, tools):
        self._tools = tools

    async def list_tools(self, enabled_only=True):
        return list(self._tools)


async def test_denylisted_read_capability_raises_for_fail_safe():
    """calendar.get is backed by query_freebusy (free/busy ranges, NOT event-by-id) on
    this branch, so a LIVE read-back would risk a false CONTRADICTED. run_readback
    REFUSES it -> raises -> ReadBackVerifier resolves to UNVERIFIED."""
    execute_tool_fn = AsyncMock()
    runner = _make_runner(tool_registry=_FakeRegistry([]), execute_tool_fn=execute_tool_fn)

    with pytest.raises(RuntimeError):
        await runner.run_readback("calendar.get", {"event_id": "e"}, _make_run())

    execute_tool_fn.assert_not_awaited()  # never dispatched


async def test_resolvable_capability_dispatches_with_expected_args():
    """A resolvable read capability resolves to tool.name via the registry and is
    invoked as execute_tool_fn(name, read_args, user_id=..., workspace_id=...)."""
    tool = SimpleNamespace(name="get_message", capability="email.get")
    execute_tool_fn = AsyncMock(return_value={"id": "m1"})
    runner = _make_runner(tool_registry=_FakeRegistry([tool]), execute_tool_fn=execute_tool_fn)
    run = _make_run()

    read_args = {"message_id": "m1"}
    result = await runner.run_readback("email.get", read_args, run)

    assert result == {"id": "m1"}
    execute_tool_fn.assert_awaited_once_with(
        "get_message", read_args, user_id="usr_test", workspace_id="ws_test"
    )


@pytest.mark.parametrize(
    "error_result",
    [
        {"error": "connector down"},  # unknown-tool / failed / unknown-backend shape
        {"error": "Tool 'x' is disabled", "blocked": True},  # disabled shape
        {"status": "error", "error": "internal mcp error"},  # internal-mcp error shape
    ],
)
async def test_executor_error_dict_raises_for_fail_safe(error_result):
    """execute_tool NEVER raises on a read failure — it returns an error dict. run_readback
    RAISES on the executor error contract so ReadBackVerifier resolves UNVERIFIED, never a
    false CONTRADICTED (a verification OUTAGE must not false-fail a correct write, spec §7)."""
    tool = SimpleNamespace(name="get_message", capability="email.get")
    execute_tool_fn = AsyncMock(return_value=error_result)
    runner = _make_runner(tool_registry=_FakeRegistry([tool]), execute_tool_fn=execute_tool_fn)

    with pytest.raises(RuntimeError):
        await runner.run_readback("email.get", {"message_id": "m1"}, _make_run())


async def test_successful_read_dict_is_not_treated_as_error():
    """A legitimate success dict ({"status": "ok", ...}) must pass through untouched — only
    the executor's error markers trip the fail-safe guard."""
    tool = SimpleNamespace(name="get_message", capability="email.get")
    ok_result = {"status": "ok", "result": {"id": "m1"}}
    execute_tool_fn = AsyncMock(return_value=ok_result)
    runner = _make_runner(tool_registry=_FakeRegistry([tool]), execute_tool_fn=execute_tool_fn)

    result = await runner.run_readback("email.get", {"message_id": "m1"}, _make_run())
    assert result == ok_result


async def test_unresolvable_capability_raises():
    """No tool serves the read capability -> raise (verifier fails safe to UNVERIFIED)."""
    runner = _make_runner(tool_registry=_FakeRegistry([]), execute_tool_fn=AsyncMock())
    with pytest.raises(RuntimeError):
        await runner.run_readback("email.get", {"message_id": "m1"}, _make_run())


async def test_missing_execute_tool_fn_raises():
    runner = _make_runner(tool_registry=_FakeRegistry([]), execute_tool_fn=None)
    with pytest.raises(RuntimeError):
        await runner.run_readback("email.get", {}, _make_run())
