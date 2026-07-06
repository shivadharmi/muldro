"""DagRunner re-auth deferral tests (C1 churn-prevention + C4 atomic defer).

C1: when a step hits ``auth_required`` and the run is parked in
``awaiting_reauth``, the ready-step loop must EXIT immediately (exactly like the
approval gate). The store's ``get_ready_steps`` returns steps that are still
``running`` (so they resume after reconnect), so if the loop does NOT break on
``awaiting_reauth`` it re-picks the same step, re-executes, re-defers — raising
an invalid ``awaiting_reauth → awaiting_reauth`` transition (swallowed) and
churning until the subtick timeout.

C4: all DB writes for the defer (run → awaiting_reauth, integration flagged,
sources paused) happen on the COORDINATOR session ``self._db`` — no separate
committing session — so graph_executor's single commit makes them atomic.
"""

from unittest.mock import AsyncMock, MagicMock

from src.services.dag_runner import DagRunner
from src.services.execution_state import transition_run


class _Run:
    """A minimal TaskRun stand-in with a real mutable ``status`` attribute so the
    state machine (transition_run) operates on it for real."""

    def __init__(self, status="running"):
        self.run_id = "run_reauth"
        self.plan_id = "plan_test"
        self.user_id = "usr_test"
        self.workspace_id = "ws_test"
        self.status = status
        self.checkpoint = {}
        self.current_step_ids = []
        self.completed_at = None
        self.error = None
        self.input_tokens = 0
        self.output_tokens = 0
        self.cost_usd = 0.0


class _Step:
    def __init__(self, step_id="step_auth", status="ready"):
        self.step_id = step_id
        self.task_id = "task_1"
        self.status = status
        self.input_data = {"capability": "email.send", "task_type": "send_email"}
        self.output_data = None
        self.started_at = None
        self.completed_at = None
        self.error = None
        self.timeout_seconds = None
        self.retry_count = 0
        self.max_retries = 3


def _auto_decision():
    from src.contracts import PolicyDecision

    return PolicyDecision(decision="auto_execute_silent", reason="ok")


def _build_runner(run, step, reauth, *, auth_output):
    """Wire a DagRunner whose store/runner/gate are mocks but whose state
    transitions are real, driving ``execute_dag`` against a single step.

    The store's ``get_ready_steps`` mirrors the real StepGraphStore: it returns
    a step that is ``ready`` OR ``running`` (the latter so a resumed run re-picks
    in-flight steps). This is exactly what makes the missing-break-guard churn
    reproducible.
    """
    db = AsyncMock()
    db.flush = AsyncMock()
    db.refresh = AsyncMock()  # no-op: keep in-memory run.status authoritative

    store = MagicMock()
    store.get_all_steps = AsyncMock(return_value=[step])

    async def _get_ready_steps(_run_id):
        return [step] if step.status in ("ready", "running") else []

    store.get_ready_steps = AsyncMock(side_effect=_get_ready_steps)
    store.resolve_step_references = AsyncMock(return_value=dict(step.input_data))
    store.checkpoint = AsyncMock()

    runner = MagicMock()
    runner.run_step_action = AsyncMock(return_value=auth_output)

    trust_gate = MagicMock()
    trust_gate.assess_step_risk = AsyncMock(return_value="high")
    trust_gate.notify_auto_executed = AsyncMock()
    trust_gate.record_auto_execution_outcome = AsyncMock()

    learner = MagicMock()
    learner.verification_enabled = False
    learner.run_verification = AsyncMock()
    learner.writeback_memories = AsyncMock()

    emitter = MagicMock()
    emitter.emit_event = AsyncMock()
    emitter.emit_surface_update = AsyncMock()
    emitter.emit_summary_surface = AsyncMock()

    trust_engine = MagicMock()
    trust_engine.evaluate = AsyncMock(return_value=_auto_decision())

    dag = DagRunner(
        db=db,
        store=store,
        trust_gate=trust_gate,
        runner=runner,
        learner=learner,
        emitter=emitter,
        trust_engine_provider=lambda: trust_engine,
        reauth_service_provider=lambda: reauth,
    )
    return dag, runner, store


def _make_reauth():
    """A ReauthService double whose defer_run actually parks the run (real state
    transition) and whose apply_needs_reauth/notify_reauth are observable.

    Deliberately has NO ``mark_needs_reauth`` attribute so the test fails loudly
    if the production code still calls the removed cross-session helper."""
    reauth = MagicMock(spec=["defer_run", "apply_needs_reauth", "notify_reauth"])

    async def _defer_run(db, run, provider):
        transition_run(run, "awaiting_reauth")
        run.checkpoint = {**(run.checkpoint or {}), "awaiting_provider": provider}

    reauth.defer_run = AsyncMock(side_effect=_defer_run)
    reauth.apply_needs_reauth = AsyncMock()
    reauth.notify_reauth = AsyncMock()
    return reauth


_AUTH_OUTPUT = {
    "status": "error",
    "error": "google needs re-authorization",
    "error_code": "auth_required",
    "provider": "google",
    "server": "google-workspace",
}


class TestExecuteDagReauthChurnPrevention:
    """C1: execute_dag exits the moment the run is parked in awaiting_reauth."""

    async def test_execute_dag_parks_and_exits_without_churn(self):
        run = _Run()
        step = _Step()
        reauth = _make_reauth()
        dag, runner, store = _build_runner(run, step, reauth, auth_output=_AUTH_OUTPUT)

        # Must not raise InvalidTransitionError (awaiting_reauth → awaiting_reauth
        # or awaiting_reauth → failed/completed) and must terminate (no churn).
        await dag.execute_dag(run)

        # Run is durably parked.
        assert run.status == "awaiting_reauth"
        assert run.checkpoint.get("awaiting_provider") == "google"

        # The step ran EXACTLY once — the loop did not re-run the still-running
        # step that get_ready_steps keeps returning.
        assert runner.run_step_action.await_count == 1

        # The run was NOT subsequently driven to a terminal state in the same pass.
        assert run.completed_at is None
        assert run.error is None

    async def test_defer_writes_all_on_coordinator_session(self):
        """C4: defer_run + apply_needs_reauth both run on the SAME coordinator
        session (self._db); no separate committing session, no mark_needs_reauth."""
        run = _Run()
        step = _Step()
        reauth = _make_reauth()
        dag, runner, store = _build_runner(run, step, reauth, auth_output=_AUTH_OUTPUT)

        await dag.execute_dag(run)

        # defer_run got the coordinator session as its first positional arg.
        reauth.defer_run.assert_awaited_once()
        assert reauth.defer_run.call_args.args[0] is dag._db

        # apply_needs_reauth (DB writes only, no commit) also got the coordinator
        # session — atomic with the run defer under graph_executor's commit.
        reauth.apply_needs_reauth.assert_awaited_once()
        assert reauth.apply_needs_reauth.call_args.args[0] is dag._db
        flat = (
            " ".join(str(a) for a in reauth.apply_needs_reauth.call_args.args)
            + " "
            + " ".join(str(v) for v in reauth.apply_needs_reauth.call_args.kwargs.values())
        )
        assert "google" in flat  # provider threaded through
        assert "auth_required" in flat  # reason threaded through

        # notify (external/Redis, idempotent) fired with workspace context.
        reauth.notify_reauth.assert_awaited_once()
        assert reauth.notify_reauth.call_args.kwargs.get("workspace_id") == "ws_test"

    async def test_no_commit_on_coordinator_session_during_defer(self):
        """The defer path must NOT commit self._db — the coordinator owns the
        commit so the run-defer + status-flip + source-pause stay atomic."""
        run = _Run()
        step = _Step()
        reauth = _make_reauth()
        dag, runner, store = _build_runner(run, step, reauth, auth_output=_AUTH_OUTPUT)

        await dag.execute_dag(run)

        dag._db.commit.assert_not_called()

    async def test_no_reauth_service_falls_through_to_failure(self):
        """When no ReauthService is wired, auth_required is a normal failure
        (not a deferral) — _defer_for_reauth returns False and the run does not
        park in awaiting_reauth."""
        run = _Run()
        step = _Step()
        # reauth=None → provider resolves to None → _defer_for_reauth bails early.
        dag, runner, store = _build_runner(run, step, None, auth_output=_AUTH_OUTPUT)

        await dag.execute_dag(run)

        assert run.status != "awaiting_reauth"
