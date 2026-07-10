"""Step 10C P1b: autonomous deep-step executor ROUTING + wiring.

``run_step_action`` gains a per-surface effective-runtime branch keyed ``"autonomous"``.
When the 10B gate resolves ``"deep"`` AND an injected ``deep_step_runner`` is present AND
the agent-loop DB factory is wired, the step routes to ``run_step_via_deep_agent`` — which
builds the SAME message/context/per-step tools the legacy path builds and delegates to the
injected runner (``AgentInvoker.run_autonomous_deep_step``). Everything is byte-neutral when
the gate is off (default ``"legacy"``) OR ``deep_step_runner`` is ``None`` (both hold in prod
and in every existing test), so the legacy path is untouched.

These tests build a minimal ``StepRunner`` via ``__new__`` (mirroring
``tests/idempotency/test_step_runner_wiring.py`` + ``tests/test_step_runner_scope.py``): the
routing decision + the delegation call are exercised directly, with the deep/legacy leaves
patched so no real agent build/stream runs.
"""

from __future__ import annotations

from types import SimpleNamespace
from unittest.mock import AsyncMock, MagicMock, patch

from src.services.step_runner import StepRunner
from tests.conftest import TEST_USER_ID, TEST_WORKSPACE_ID, make_mock_settings

GATE = "src.services.runtime_gate.effective_runtime"


def _runner(*, deep_step_runner=None, redis=None, settings=None) -> StepRunner:
    """A minimal StepRunner whose routing + delegation can run without a real graph.

    Bypasses ``__init__`` (like the other step_runner tests) and wires just the attributes
    ``run_step_action`` / ``run_step_via_deep_agent`` read. ``_execute_tool_fn`` + ``_budget``
    are set truthy so the LEGACY branch's ``if self._db_factory and self._execute_tool_fn and
    self._budget`` guard is satisfied (the routing tests can then assert deep-vs-legacy).
    """
    r = StepRunner.__new__(StepRunner)
    r._settings = settings or make_mock_settings(runtime="legacy")
    r._client = MagicMock()
    r._store = MagicMock()
    r._store.get_all_steps = AsyncMock(return_value=[])
    r._emitter = MagicMock()
    r._emitter.emit_event = AsyncMock()
    r._db_factory_provider = MagicMock(return_value=MagicMock())
    r._active_traces_provider = MagicMock(return_value={})
    r._tool_registry = None
    r._context_builder = None
    r._execute_tool_fn = AsyncMock(name="RAW_execute_tool_fn")
    r._budget = MagicMock()
    r._circuit_breaker = None
    r._redis = redis
    r._deep_step_runner = deep_step_runner
    return r


def _step(capability: str = "email.send") -> SimpleNamespace:
    return SimpleNamespace(
        step_id="s1",
        input_data={"capability": capability, "goal": "send the update"},
        status="running",
        output_data=None,
    )


def _run() -> SimpleNamespace:
    return SimpleNamespace(run_id="run_1", user_id=TEST_USER_ID, workspace_id=TEST_WORKSPACE_ID)


# ─────────────────────────── Test 1 — route to deep ──────────────────────────


async def test_run_step_action_routes_to_deep_when_gate_deep():
    """Gate ``"deep"`` for ``"autonomous"`` + injected deep_step_runner + wired db_factory →
    ``run_step_via_deep_agent`` is awaited and the LEGACY agent loop is NOT called."""
    deep_runner = AsyncMock(
        return_value={"status": "completed", "result": "ok", "tools_called": [], "errors": []}
    )
    runner = _runner(deep_step_runner=deep_runner)
    step, run = _step(), _run()

    with (
        patch(GATE, AsyncMock(return_value="deep")),
        patch.object(
            runner, "run_step_via_deep_agent", AsyncMock(return_value={"status": "completed"})
        ) as deep,
        patch.object(runner, "run_step_via_agent_loop", AsyncMock()) as legacy,
    ):
        result = await runner.run_step_action(step, run)

    deep.assert_awaited_once()
    legacy.assert_not_awaited()
    assert result == {"status": "completed"}


async def test_run_step_action_deep_branch_forwards_cancel_event():
    """The deep branch forwards the ``cancel_event`` through to ``run_step_via_deep_agent``."""
    runner = _runner(deep_step_runner=AsyncMock())
    step, run = _step(), _run()
    sentinel = object()

    with (
        patch(GATE, AsyncMock(return_value="deep")),
        patch.object(runner, "run_step_via_deep_agent", AsyncMock(return_value={})) as deep,
        patch.object(runner, "run_step_via_agent_loop", AsyncMock()),
    ):
        await runner.run_step_action(step, run, cancel_event=sentinel)

    assert deep.await_args.kwargs.get("cancel_event") is sentinel


# ────────────────────── Test 2 — route to legacy (byte-neutral) ──────────────


async def test_run_step_action_routes_to_legacy_when_gate_legacy():
    """Gate ``"legacy"`` (default / no key / redis None) → the LEGACY agent loop runs and the
    deep path is NOT touched — the byte-neutral floor."""
    runner = _runner(deep_step_runner=AsyncMock())
    step, run = _step(), _run()

    with (
        patch(GATE, AsyncMock(return_value="legacy")),
        patch.object(runner, "run_step_via_deep_agent", AsyncMock()) as deep,
        patch.object(
            runner, "run_step_via_agent_loop", AsyncMock(return_value={"status": "completed"})
        ) as legacy,
    ):
        result = await runner.run_step_action(step, run)

    legacy.assert_awaited_once()
    deep.assert_not_awaited()
    assert result == {"status": "completed"}


async def test_run_step_action_gate_deep_but_no_deep_runner_falls_to_legacy():
    """Defensive double-guard: gate ``"deep"`` but ``deep_step_runner is None`` → STILL legacy.
    Both the gate AND an injected runner must hold to reach the deep branch."""
    runner = _runner(deep_step_runner=None)
    step, run = _step(), _run()

    with (
        patch(GATE, AsyncMock(return_value="deep")),
        patch.object(runner, "run_step_via_deep_agent", AsyncMock()) as deep,
        patch.object(
            runner, "run_step_via_agent_loop", AsyncMock(return_value={"status": "completed"})
        ) as legacy,
    ):
        await runner.run_step_action(step, run)

    legacy.assert_awaited_once()
    deep.assert_not_awaited()


async def test_run_step_action_default_settings_no_redis_is_byte_neutral():
    """With the real default (no ``effective_runtime`` patch): ``_redis=None`` → the gate
    short-circuits to ``settings.runtime="legacy"`` → legacy path, deep untouched."""
    runner = _runner(
        deep_step_runner=AsyncMock(), redis=None, settings=make_mock_settings(runtime="legacy")
    )
    step, run = _step(), _run()

    with (
        patch.object(runner, "run_step_via_deep_agent", AsyncMock()) as deep,
        patch.object(
            runner, "run_step_via_agent_loop", AsyncMock(return_value={"status": "completed"})
        ) as legacy,
    ):
        await runner.run_step_action(step, run)

    legacy.assert_awaited_once()
    deep.assert_not_awaited()


# ─────────────── Test 3 — run_step_via_deep_agent delegates correctly ────────


async def test_run_step_via_deep_agent_delegates_with_step_capability():
    """``run_step_via_deep_agent`` builds the message/context/per-step tools and delegates to
    the injected runner with ``pre_approved_capabilities == frozenset({step capability})``
    (LOW-2: EXACTLY the step's already-step-gated capability, never a broad set), the run/step
    identity, and returns the runner's dict unchanged."""
    runner_return = {
        "status": "completed",
        "result": "sent",
        "tools_called": ["send_email"],
        "errors": [],
    }
    deep_runner = AsyncMock(return_value=runner_return)
    runner = _runner(deep_step_runner=deep_runner)
    step, run = _step("email.send"), _run()
    tools_sentinel = [{"name": "send_email", "description": "d", "input_schema": {}}]

    with (
        patch("src.orchestrator.agents.AGENTS", {"executor": SimpleNamespace(prompt="p")}),
        patch.object(runner, "_build_step_message", AsyncMock(return_value="MSG")) as build_msg,
        patch.object(runner, "build_step_context", AsyncMock(return_value="CTX")),
        patch.object(runner, "build_executor_tools", AsyncMock(return_value=tools_sentinel)),
    ):
        result = await runner.run_step_via_deep_agent(step, run)

    assert result is runner_return  # returned unchanged
    build_msg.assert_awaited_once_with(step, run)
    deep_runner.assert_awaited_once()
    kw = deep_runner.await_args.kwargs
    assert kw["pre_approved_capabilities"] == frozenset({"email.send"})
    assert kw["message"] == "MSG"
    assert kw["context_block"] == "CTX"
    assert kw["tools"] is tools_sentinel
    assert kw["run_id"] == "run_1"
    assert kw["step_id"] == "s1"
    assert kw["user_id"] == TEST_USER_ID
    assert kw["workspace_id"] == TEST_WORKSPACE_ID
    assert kw["executor"].name == "executor" if hasattr(kw["executor"], "name") else True
    assert kw["model"] == runner._settings.resolved_model


async def test_run_step_via_deep_agent_scopes_to_step_capability_only():
    """A ``calendar.create`` step delegates with ``frozenset({"calendar.create"})`` — the
    pre-approved set tracks the step's OWN capability, not a hard-coded value."""
    deep_runner = AsyncMock(return_value={"status": "completed"})
    runner = _runner(deep_step_runner=deep_runner)
    step, run = _step("calendar.create"), _run()

    with (
        patch("src.orchestrator.agents.AGENTS", {"executor": SimpleNamespace(prompt="p")}),
        patch.object(runner, "_build_step_message", AsyncMock(return_value="MSG")),
        patch.object(runner, "build_step_context", AsyncMock(return_value="")),
        patch.object(runner, "build_executor_tools", AsyncMock(return_value=[])),
    ):
        await runner.run_step_via_deep_agent(step, run)

    assert deep_runner.await_args.kwargs["pre_approved_capabilities"] == frozenset(
        {"calendar.create"}
    )


# ────────────────────── Test 4 — executor-not-found early return ─────────────


async def test_run_step_via_deep_agent_executor_not_found():
    """No executor agent registered → the SAME early dict shape the legacy path returns,
    and the injected deep_step_runner is NEVER called."""
    deep_runner = AsyncMock()
    runner = _runner(deep_step_runner=deep_runner)
    step, run = _step(), _run()

    with (
        patch("src.orchestrator.agents.AGENTS", {}),
        patch.object(runner, "_build_step_message", AsyncMock(return_value="MSG")),
        patch.object(runner, "build_step_context", AsyncMock(return_value="")),
    ):
        out = await runner.run_step_via_deep_agent(step, run)

    assert out == {
        "status": "completed",
        "result": "Executor agent not found",
        "errors": ["Executor agent not configured"],
    }
    deep_runner.assert_not_awaited()
