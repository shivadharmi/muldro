"""run_step_via_agent_loop must hand agent_loop an IDEMPOTENT execute_tool_fn
(wrapped), not the raw one — proving the ledger is installed on the autonomous
path. We intercept agent_loop and inspect the execute_tool_fn it receives."""

from types import SimpleNamespace
from unittest.mock import AsyncMock, MagicMock, patch

import pytest

from src.services.step_runner import StepRunner


def _runner():
    r = StepRunner.__new__(StepRunner)
    r._settings = SimpleNamespace(resolved_model="m")
    r._client = MagicMock()
    r._store = MagicMock()
    r._store.get_all_steps = AsyncMock(return_value=[])
    r._emitter = MagicMock()
    # _db_factory and _active_traces are read-only @property accessors that call
    # the *_provider(); set the providers, not the properties.
    r._db_factory_provider = MagicMock(return_value=MagicMock())
    r._active_traces_provider = MagicMock(return_value={})
    r._execute_tool_fn = AsyncMock(name="RAW_execute_tool_fn")
    r._budget = None
    r._circuit_breaker = None
    return r


@pytest.mark.asyncio
async def test_agent_loop_receives_a_wrapped_execute_tool_fn():
    runner = _runner()
    step = SimpleNamespace(step_id="st", input_data={"capability": "email.send"}, status="running")
    run = SimpleNamespace(run_id="r", user_id="u", workspace_id="ws")

    captured = {}

    async def fake_agent_loop(*args, **kwargs):
        captured["execute_tool_fn"] = kwargs.get("execute_tool_fn")
        if False:
            yield  # make this an async generator
        return

    with (
        patch("src.orchestrator.agent_loop.agent_loop", fake_agent_loop),
        patch("src.orchestrator.agents.AGENTS", {"operator": SimpleNamespace(prompt="p")}),
        patch.object(runner, "build_step_context", AsyncMock(return_value="")),
        patch.object(runner, "build_operator_tools", AsyncMock(return_value=[])),
    ):
        await runner.run_step_via_agent_loop(step, run)

    fn = captured["execute_tool_fn"]
    assert fn is not None
    assert fn is not runner._execute_tool_fn, "agent_loop got the RAW fn — ledger not installed"
    assert fn.__name__ == "_idempotent_execute"
