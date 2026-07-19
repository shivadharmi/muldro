"""Tests for GraphExecutor — DAG-based execution engine."""

from unittest.mock import AsyncMock, MagicMock, patch

import pytest

from tests.conftest import TEST_USER_ID, make_mock_settings


@pytest.fixture
def mock_db():
    db = AsyncMock()
    db.add = MagicMock()
    db.flush = AsyncMock()
    db.commit = AsyncMock()
    db.refresh = AsyncMock()
    return db


@pytest.fixture
def settings():
    return make_mock_settings()


def _make_plan_task(task_id, task_type="summarize", depends_on=None, input_data=None):
    task = MagicMock()
    task.task_id = task_id
    task.task_type = task_type
    task.depends_on = depends_on or []
    task.input_data = input_data or {"task_type": task_type}
    task.id = hash(task_id)
    return task


def _make_plan(plan_id="plan_001", tasks=None):
    plan = MagicMock()
    plan.plan_id = plan_id
    plan.goal = "Test plan"
    plan.tasks = tasks or []
    return plan


def _make_executor(settings, mock_db):
    with patch("src.services.graph_executor.get_anthropic_client") as mock_client:
        mock_client.return_value = MagicMock()
        from src.services.graph_executor import GraphExecutor

        return GraphExecutor(settings, mock_db)


class TestBuildGraphDefinition:
    def test_builds_from_tasks(self, settings, mock_db):
        executor = _make_executor(settings, mock_db)
        tasks = [
            _make_plan_task("t1", "summarize"),
            _make_plan_task("t2", "draft_email", depends_on=["t1"]),
        ]
        graph = executor._build_graph_definition(tasks)
        assert len(graph["nodes"]) == 2
        assert len(graph["edges"]) == 1
        assert graph["edges"][0] == {"from": "t1", "to": "t2"}

    def test_no_edges_for_independent_tasks(self, settings, mock_db):
        executor = _make_executor(settings, mock_db)
        tasks = [
            _make_plan_task("t1", "summarize"),
            _make_plan_task("t2", "fetch_info"),
        ]
        graph = executor._build_graph_definition(tasks)
        assert len(graph["edges"]) == 0


class TestCreateRun:
    @patch("src.services.graph_executor.get_anthropic_client")
    async def test_creates_run_with_steps(self, mock_client, settings, mock_db):
        mock_client.return_value = MagicMock()
        from src.services.graph_executor import GraphExecutor

        plan = _make_plan(
            tasks=[
                _make_plan_task("t1"),
                _make_plan_task("t2", depends_on=["t1"]),
            ]
        )

        result_mock = MagicMock()
        result_mock.scalar_one_or_none.return_value = plan
        tasks_result = MagicMock()
        tasks_result.scalars.return_value.all.return_value = plan.tasks
        mock_db.execute = AsyncMock(side_effect=[result_mock, tasks_result])

        executor = GraphExecutor(settings, mock_db)
        run = await executor.create_run("plan_001", TEST_USER_ID)

        assert run.run_id.startswith("run_")
        assert run.plan_id == "plan_001"
        assert run.status == "pending"
        # Should have added: 1 run + 2 steps = 3 calls
        assert mock_db.add.call_count == 3


class TestCancelRun:
    @patch("src.services.graph_executor.get_anthropic_client")
    async def test_cancels_and_skips_pending(self, mock_client, settings, mock_db):
        mock_client.return_value = MagicMock()
        from src.services.graph_executor import GraphExecutor

        run = MagicMock()
        run.run_id = "run_001"
        run.status = "running"
        run.plan_id = "plan_001"

        step2 = MagicMock()
        step2.status = "pending"
        step3 = MagicMock()
        step3.status = "ready"

        plan = MagicMock()
        plan.status = "executing"

        run_result = MagicMock()
        run_result.scalar_one_or_none.return_value = run
        steps_result = MagicMock()
        steps_result.scalars.return_value.all.return_value = [step2, step3]
        plan_result = MagicMock()
        plan_result.scalar_one_or_none.return_value = plan

        # 3rd execute is the parent-plan lookup in _reconcile_plan_status
        mock_db.execute = AsyncMock(side_effect=[run_result, steps_result, plan_result])

        executor = GraphExecutor(settings, mock_db)
        cancelled = await executor.cancel_run("run_001")

        assert cancelled.status == "cancelled"
        assert step2.status == "skipped"
        assert step3.status == "skipped"
        # Cancelling a run reconciles its parent plan to a terminal state
        assert plan.status == "cancelled"


class TestPauseRun:
    @patch("src.services.graph_executor.get_anthropic_client")
    async def test_pauses_running(self, mock_client, settings, mock_db):
        mock_client.return_value = MagicMock()
        from src.services.graph_executor import GraphExecutor

        run = MagicMock()
        run.run_id = "run_001"
        run.status = "running"
        run.current_step_ids = []

        run_result = MagicMock()
        run_result.scalar_one_or_none.return_value = run
        mock_db.execute = AsyncMock(return_value=run_result)

        executor = GraphExecutor(settings, mock_db)
        paused = await executor.pause_run("run_001", "manual_pause")

        assert paused.status == "paused"
        mock_db.commit.assert_called_once()


class TestResumeRun:
    @patch("src.services.graph_executor.get_anthropic_client")
    async def test_rejects_non_paused(self, mock_client, settings, mock_db):
        mock_client.return_value = MagicMock()
        from src.services.graph_executor import GraphExecutor

        run = MagicMock()
        run.run_id = "run_001"
        run.status = "running"

        run_result = MagicMock()
        run_result.scalar_one_or_none.return_value = run
        mock_db.execute = AsyncMock(return_value=run_result)

        executor = GraphExecutor(settings, mock_db)
        with pytest.raises(ValueError, match="not resumable"):
            await executor.resume_run("run_001")

    @patch("src.services.graph_executor.get_anthropic_client")
    async def test_resume_uses_fresh_trace_id(self, mock_client, settings, mock_db):
        """Regression: each resume segment must get a fresh trace_id.

        TraceStore._store_to_db does INSERT (not upsert). If resume reused
        run.trace_id, the second segment's INSERT would violate the
        ``traces`` primary key constraint. The initial trace_id must stay
        on run.trace_id so consumers that expect a single canonical
        pointer (routes_history, evidence_bundle) keep working.
        """
        from datetime import datetime, timezone

        from src.services.graph_executor import GraphExecutor

        mock_client.return_value = MagicMock()

        now = datetime.now(timezone.utc)
        run = MagicMock()
        run.run_id = "run_001"
        run.status = "paused"
        run.trace_id = "trace_original_segment"
        run.started_at = now
        run.created_at = now
        run.checkpoint = {}  # falsy → skips _get_all_steps branch
        run.error = None
        run.user_id = TEST_USER_ID
        run.workspace_id = "ws_test"
        run.source = "background"

        run_result = MagicMock()
        run_result.scalar_one_or_none.return_value = run
        mock_db.execute = AsyncMock(return_value=run_result)

        executor = GraphExecutor(settings, mock_db)
        executor._execute_dag = AsyncMock()
        executor._finalize_trace = AsyncMock()
        # transition_run state-machine is tested elsewhere; bypass it so
        # the MagicMock run object doesn't need full state validation.
        with patch("src.services.graph_executor.transition_run"):
            await executor.resume_run("run_001")

        trace = executor._active_traces["run_001"]
        assert trace.trace_id != "trace_original_segment", (
            "resume must create a fresh trace_id to avoid traces PK violation"
        )
        assert trace.trace_id.startswith("trace_")
        assert trace.trigger == "execution:resume"
        # run.trace_id must stay pointing at the initial trace for downstream
        # consumers that expect a single canonical pointer.
        assert run.trace_id == "trace_original_segment"


class TestPopulateStepsCapabilityMapping:
    """Integration tests for _populate_steps preserving capability through input_data."""

    @patch("src.services.graph_executor.get_anthropic_client")
    async def test_populate_steps_maps_capability_from_plan_task(
        self, mock_client, settings, mock_db
    ):
        """_populate_steps copies capability from PlanTask.input_data into TaskStep.input_data."""
        mock_client.return_value = MagicMock()
        from src.services.graph_executor import GraphExecutor

        # Build a PlanTask with capability in input_data
        plan_task = _make_plan_task(
            "t1",
            task_type="draft_email",
            input_data={
                "capability": "email.draft",
                "task_type": "draft_email",
                "goal": "Send update",
            },
        )
        plan = _make_plan(tasks=[plan_task])

        tasks_result = MagicMock()
        tasks_result.scalars.return_value.all.return_value = [plan_task]
        mock_db.execute = AsyncMock(return_value=tasks_result)

        executor = GraphExecutor(settings, mock_db)

        run = MagicMock()
        run.run_id = "run_cap_test"
        run.workspace_id = "ws_test"
        run.user_id = "usr_test"

        added_steps = []

        def capture_add(obj):
            added_steps.append(obj)

        mock_db.add = MagicMock(side_effect=capture_add)

        await executor._populate_steps(run, plan)

        # Should have added exactly one TaskStep
        from src.models.task_graph import TaskStep

        step_objects = [s for s in added_steps if isinstance(s, TaskStep)]
        assert len(step_objects) == 1
        step = step_objects[0]
        assert step.input_data is not None
        assert step.input_data.get("capability") == "email.draft"
        assert step.input_data.get("task_type") == "draft_email"

    @patch("src.services.graph_executor.get_anthropic_client")
    async def test_populate_steps_capability_preserved_without_task_type(
        self, mock_client, settings, mock_db
    ):
        """_populate_steps preserves capability even when task_type is absent from input_data."""
        mock_client.return_value = MagicMock()
        from src.services.graph_executor import GraphExecutor

        plan_task = _make_plan_task(
            "t2",
            task_type="search",
            input_data={"capability": "search.web", "query": "quarterly results"},
        )
        plan = _make_plan(tasks=[plan_task])

        tasks_result = MagicMock()
        tasks_result.scalars.return_value.all.return_value = [plan_task]
        mock_db.execute = AsyncMock(return_value=tasks_result)

        executor = GraphExecutor(settings, mock_db)

        run = MagicMock()
        run.run_id = "run_cap_test2"
        run.workspace_id = "ws_test"
        run.user_id = "usr_test"

        added_steps = []

        def capture_add(obj):
            added_steps.append(obj)

        mock_db.add = MagicMock(side_effect=capture_add)

        await executor._populate_steps(run, plan)

        from src.models.task_graph import TaskStep

        step_objects = [s for s in added_steps if isinstance(s, TaskStep)]
        assert len(step_objects) == 1
        step = step_objects[0]
        assert step.input_data.get("capability") == "search.web"
        # task_type should be backfilled from plan_task.task_type
        assert step.input_data.get("task_type") == "search"


class TestExecuteStepCapabilityReading:
    """Integration tests for _execute_step reading capability from step.input_data."""

    @patch("src.services.graph_executor.get_anthropic_client")
    async def test_execute_step_calls_trust_engine_with_capability(
        self, mock_client, settings, mock_db
    ):
        """_execute_step extracts capability from step.input_data and passes it to trust engine."""
        mock_client.return_value = MagicMock()
        from src.services.graph_executor import GraphExecutor

        executor = GraphExecutor(settings, mock_db)

        # Attach a mock trust engine
        trust_engine = MagicMock()
        trust_engine._workspace_id = ""
        from src.contracts import PolicyDecision

        auto_decision = PolicyDecision(decision="auto_execute_silent", reason="ok")
        trust_engine.evaluate = AsyncMock(return_value=auto_decision)
        executor._trust_engine = trust_engine

        # Mock _assess_step_risk
        executor._trust_gate.assess_step_risk = AsyncMock(return_value="low")

        # Mock _run_step_action so we don't need full agent infra
        executor._runner.run_step_action = AsyncMock(
            return_value={"status": "completed", "result": "ok"}
        )
        executor._store.resolve_step_references = AsyncMock(
            return_value={"capability": "email.draft"}
        )
        executor._dag_runner.finalize_step = AsyncMock()
        executor._surface_emitter.emit_event = AsyncMock()

        step = MagicMock()
        step.step_id = "step_cap_exec"
        step.status = "ready"
        step.input_data = {"capability": "email.draft", "task_type": "draft_email"}
        step.started_at = None
        step.name = "Draft email"
        step.timeout_seconds = None
        step.retry_count = 0
        step.max_retries = 3

        run = MagicMock()
        run.run_id = "run_cap_exec"
        run.user_id = "usr_test"
        run.workspace_id = "ws_test"
        run.status = "running"

        await executor._execute_step(run, step)

        # Verify trust engine was called with the correct capability
        trust_engine.evaluate.assert_called_once_with("email.draft", "low", workspace_id="ws_test")

    @patch("src.services.graph_executor.get_anthropic_client")
    async def test_execute_step_falls_back_to_task_type_when_no_capability(
        self, mock_client, settings, mock_db
    ):
        """_execute_step falls back to task_type when capability is absent from input_data."""
        mock_client.return_value = MagicMock()
        from src.services.graph_executor import GraphExecutor

        executor = GraphExecutor(settings, mock_db)

        trust_engine = MagicMock()
        trust_engine._workspace_id = ""
        from src.contracts import PolicyDecision

        auto_decision = PolicyDecision(decision="auto_execute_silent", reason="ok")
        trust_engine.evaluate = AsyncMock(return_value=auto_decision)
        executor._trust_engine = trust_engine

        executor._trust_gate.assess_step_risk = AsyncMock(return_value="low")
        executor._runner.run_step_action = AsyncMock(
            return_value={"status": "completed", "result": "ok"}
        )
        executor._store.resolve_step_references = AsyncMock(return_value={"task_type": "summarize"})
        executor._dag_runner.finalize_step = AsyncMock()
        executor._surface_emitter.emit_event = AsyncMock()

        step = MagicMock()
        step.step_id = "step_fallback_exec"
        step.status = "ready"
        step.input_data = {"task_type": "summarize"}
        step.started_at = None
        step.name = "Summarize"
        step.timeout_seconds = None
        step.retry_count = 0
        step.max_retries = 3

        run = MagicMock()
        run.run_id = "run_fallback_exec"
        run.user_id = "usr_test"
        run.workspace_id = "ws_test"
        run.status = "running"

        await executor._execute_step(run, step)

        # Should fall back to task_type value
        trust_engine.evaluate.assert_called_once_with("summarize", "low", workspace_id="ws_test")


class TestCapabilityFieldReading:
    """GraphExecutor reads 'capability' field with 'task_type' fallback."""

    def test_capability_preferred_over_task_type(self):
        """When both capability and task_type exist, capability wins."""
        input_data = {"capability": "email.draft", "task_type": "draft_email"}
        result = input_data.get("capability", input_data.get("task_type", "unknown"))
        assert result == "email.draft"

    def test_falls_back_to_task_type(self):
        """When only task_type exists, it's used."""
        input_data = {"task_type": "draft_email"}
        result = input_data.get("capability", input_data.get("task_type", "unknown"))
        assert result == "draft_email"

    def test_defaults_to_unknown(self):
        """When neither exists, defaults to 'unknown'."""
        input_data = {}
        result = input_data.get("capability", input_data.get("task_type", "unknown"))
        assert result == "unknown"


class TestExecuteStepEmptyCapabilityFailsClosed:
    """FIX #2 — a step with empty/missing capability must NOT auto-execute ungated.

    Per CLAUDE.md the Planner ALWAYS emits a capability per PlanStep, so an empty
    capability reaching the TrustEngine gate is contract drift. The gate must
    fail-closed: the step is failed as a contract violation and never reaches the
    auto-execute path (no risk assessment, no tool action).
    """

    @patch("src.services.graph_executor.get_anthropic_client")
    async def test_empty_capability_with_trust_engine_does_not_execute(
        self, mock_client, settings, mock_db
    ):
        mock_client.return_value = MagicMock()
        from src.services.graph_executor import GraphExecutor

        executor = GraphExecutor(settings, mock_db)

        # TrustEngine present — this is the dangerous case (fail-OPEN previously).
        trust_engine = MagicMock()
        trust_engine.evaluate = AsyncMock()
        executor._trust_engine = trust_engine

        # Spy on the execution-side helpers — none must be called.
        executor._trust_gate.assess_step_risk = AsyncMock()
        executor._runner.run_step_action = AsyncMock(return_value={"status": "completed"})
        executor._store.resolve_step_references = AsyncMock(return_value={})
        executor._dag_runner.finalize_step = AsyncMock()
        executor._surface_emitter.emit_event = AsyncMock()

        step = MagicMock()
        step.step_id = "step_empty_cap"
        step.status = "ready"
        step.input_data = {"goal": "do something dangerous"}  # NO capability / task_type
        step.started_at = None
        step.name = "Dangerous write"
        step.timeout_seconds = None
        step.retry_count = 0
        step.max_retries = 3

        run = MagicMock()
        run.run_id = "run_empty_cap"
        run.user_id = "usr_test"
        run.workspace_id = "ws_test"
        run.status = "running"

        await executor._execute_step(run, step)

        # Fail-closed: never assessed risk, never evaluated trust, never ran the action.
        trust_engine.evaluate.assert_not_called()
        executor._trust_gate.assess_step_risk.assert_not_called()
        executor._runner.run_step_action.assert_not_called()
        executor._dag_runner.finalize_step.assert_not_called()
        # Step ended in a terminal non-execution state (failed contract violation).
        assert step.status == "failed"

    @patch("src.services.graph_executor.get_anthropic_client")
    async def test_empty_capability_missing_input_data_does_not_execute(
        self, mock_client, settings, mock_db
    ):
        """input_data is None entirely → still fail-closed, not ungated execution."""
        mock_client.return_value = MagicMock()
        from src.services.graph_executor import GraphExecutor

        executor = GraphExecutor(settings, mock_db)
        trust_engine = MagicMock()
        trust_engine.evaluate = AsyncMock()
        executor._trust_engine = trust_engine
        executor._runner.run_step_action = AsyncMock(return_value={"status": "completed"})
        executor._dag_runner.finalize_step = AsyncMock()
        executor._surface_emitter.emit_event = AsyncMock()

        step = MagicMock()
        step.step_id = "step_no_input"
        step.status = "ready"
        step.input_data = None
        step.started_at = None
        step.name = "No input"
        step.timeout_seconds = None
        step.retry_count = 0
        step.max_retries = 3

        run = MagicMock()
        run.run_id = "run_no_input"
        run.user_id = "usr_test"
        run.workspace_id = "ws_test"
        run.status = "running"

        await executor._execute_step(run, step)

        trust_engine.evaluate.assert_not_called()
        executor._runner.run_step_action.assert_not_called()
        executor._dag_runner.finalize_step.assert_not_called()
        assert step.status == "failed"

    @patch("src.services.graph_executor.get_anthropic_client")
    async def test_resumed_step_skips_gate(self, mock_client, settings, mock_db):
        """Already-approved (status == 'running') steps bypass the gate entirely and
        execute via the common path — the fail-closed guard must not break resume."""
        mock_client.return_value = MagicMock()
        from src.services.graph_executor import GraphExecutor

        executor = GraphExecutor(settings, mock_db)
        trust_engine = MagicMock()
        trust_engine.evaluate = AsyncMock()
        executor._trust_engine = trust_engine
        executor._runner.run_step_action = AsyncMock(return_value={"status": "completed"})
        executor._store.resolve_step_references = AsyncMock(return_value={})
        executor._dag_runner.finalize_step = AsyncMock()
        executor._surface_emitter.emit_event = AsyncMock()
        # Step 6C: the resumed path reads the persisted decision_type + records the
        # verified-outcome trust increment; stub both so this gate-skip test does not
        # depend on a real DB (mirrors the finalize_step stub above).
        executor._dag_runner._read_approval_decision_type = AsyncMock(return_value="approved")
        executor._trust_gate.record_user_approval_outcome = AsyncMock()

        step = MagicMock()
        step.step_id = "step_resumed"
        step.status = "running"  # already approved → already_approved=True
        step.input_data = {"goal": "resumed"}  # empty capability, but already approved
        step.started_at = None
        step.name = "Resumed"
        step.timeout_seconds = None
        step.retry_count = 0
        step.max_retries = 3

        run = MagicMock()
        run.run_id = "run_resumed"
        run.user_id = "usr_test"
        run.workspace_id = "ws_test"
        run.status = "running"

        await executor._execute_step(run, step)

        # Resumed path: gate skipped, action runs, finalized.
        trust_engine.evaluate.assert_not_called()
        executor._runner.run_step_action.assert_called_once()
        executor._dag_runner.finalize_step.assert_called_once()


class TestAuthRequiredDeferral:
    """A step whose tool returns an ``auth_required`` error defers the run for
    OAuth re-authorization instead of failing it (task deferral wiring)."""

    def _auto_decision(self):
        from src.contracts import PolicyDecision

        return PolicyDecision(decision="auto_execute_silent", reason="ok")

    def _make_step_run(self):
        step = MagicMock()
        step.step_id = "step_auth"
        step.status = "ready"
        step.input_data = {"capability": "email.send", "task_type": "send_email"}
        step.started_at = None
        step.name = "Send email"
        step.timeout_seconds = None
        step.retry_count = 0
        step.max_retries = 3

        run = MagicMock()
        run.run_id = "run_auth"
        run.user_id = "usr_test"
        run.workspace_id = "ws_test"
        run.status = "running"
        run.checkpoint = {}
        return step, run

    @patch("src.services.graph_executor.get_anthropic_client")
    async def test_auth_required_output_defers_run(self, mock_client, settings, mock_db):
        mock_client.return_value = MagicMock()
        from src.services.graph_executor import GraphExecutor

        executor = GraphExecutor(settings, mock_db)

        trust_engine = MagicMock()
        trust_engine.evaluate = AsyncMock(return_value=self._auto_decision())
        executor._trust_engine = trust_engine
        executor._trust_gate.assess_step_risk = AsyncMock(return_value="high")

        # The agent loop surfaced an auth_required tool error in the step output.
        executor._runner.run_step_action = AsyncMock(
            return_value={
                "status": "error",
                "error_code": "auth_required",
                "provider": "google",
                "server": "google-workspace",
                "auth_required": {
                    "status": "error",
                    "error_code": "auth_required",
                    "provider": "google",
                    "server": "google-workspace",
                },
            }
        )
        executor._store.resolve_step_references = AsyncMock(
            return_value={"capability": "email.send"}
        )
        executor._dag_runner.finalize_step = AsyncMock()
        executor._dag_runner.handle_step_failure = AsyncMock()
        executor._surface_emitter.emit_event = AsyncMock()

        # Mock the ReauthService injected into the dag_runner. Uses the new
        # coordinator-session contract: defer_run + apply_needs_reauth (DB writes
        # on self._db, no commit) + notify_reauth (external). spec= ensures the
        # removed cross-session mark_needs_reauth is never called.
        reauth = MagicMock(spec=["defer_run", "apply_needs_reauth", "notify_reauth"])
        reauth.defer_run = AsyncMock()
        reauth.apply_needs_reauth = AsyncMock()
        reauth.notify_reauth = AsyncMock()
        executor._reauth_service = reauth

        step, run = self._make_step_run()

        await executor._execute_step(run, step)

        # Run is DEFERRED, not failed/finalized.
        reauth.defer_run.assert_awaited_once()
        defer_args = reauth.defer_run.call_args.args
        # defer_run(db, run, provider) — db is the coordinator session, provider google.
        assert defer_args[0] is executor._db
        assert "google" in defer_args

        # apply_needs_reauth(db, user_id, provider, reason) — same coordinator session.
        reauth.apply_needs_reauth.assert_awaited_once()
        apply_args = reauth.apply_needs_reauth.call_args.args
        assert apply_args[0] is executor._db
        assert "google" in apply_args
        assert "auth_required" in apply_args

        # notify_reauth fired with workspace context.
        reauth.notify_reauth.assert_awaited_once()
        assert reauth.notify_reauth.call_args.kwargs.get("workspace_id") == "ws_test"

        executor._dag_runner.finalize_step.assert_not_called()
        executor._dag_runner.handle_step_failure.assert_not_called()

    @patch("src.services.graph_executor.get_anthropic_client")
    async def test_auth_required_top_level_error_code_defers(self, mock_client, settings, mock_db):
        """Even without a nested ``auth_required`` key, a top-level
        ``error_code == 'auth_required'`` output defers the run."""
        mock_client.return_value = MagicMock()
        from src.services.graph_executor import GraphExecutor

        executor = GraphExecutor(settings, mock_db)

        trust_engine = MagicMock()
        trust_engine.evaluate = AsyncMock(return_value=self._auto_decision())
        executor._trust_engine = trust_engine
        executor._trust_gate.assess_step_risk = AsyncMock(return_value="high")

        executor._runner.run_step_action = AsyncMock(
            return_value={
                "status": "error",
                "error": "google needs re-authorization",
                "error_code": "auth_required",
                "provider": "google",
                "server": "google-workspace",
            }
        )
        executor._store.resolve_step_references = AsyncMock(
            return_value={"capability": "email.send"}
        )
        executor._dag_runner.finalize_step = AsyncMock()
        executor._dag_runner.handle_step_failure = AsyncMock()
        executor._surface_emitter.emit_event = AsyncMock()

        reauth = MagicMock(spec=["defer_run", "apply_needs_reauth", "notify_reauth"])
        reauth.defer_run = AsyncMock()
        reauth.apply_needs_reauth = AsyncMock()
        reauth.notify_reauth = AsyncMock()
        executor._reauth_service = reauth

        step, run = self._make_step_run()
        await executor._execute_step(run, step)

        reauth.defer_run.assert_awaited_once()
        reauth.apply_needs_reauth.assert_awaited_once()
        reauth.notify_reauth.assert_awaited_once()
        executor._dag_runner.finalize_step.assert_not_called()
        executor._dag_runner.handle_step_failure.assert_not_called()

    @patch("src.services.graph_executor.get_anthropic_client")
    async def test_normal_output_still_finalizes(self, mock_client, settings, mock_db):
        """A non-auth output is finalized normally (deferral does not interfere)."""
        mock_client.return_value = MagicMock()
        from src.services.graph_executor import GraphExecutor

        executor = GraphExecutor(settings, mock_db)

        trust_engine = MagicMock()
        trust_engine.evaluate = AsyncMock(return_value=self._auto_decision())
        executor._trust_engine = trust_engine
        executor._trust_gate.assess_step_risk = AsyncMock(return_value="low")
        executor._runner.run_step_action = AsyncMock(
            return_value={"status": "completed", "result": "done"}
        )
        executor._store.resolve_step_references = AsyncMock(
            return_value={"capability": "email.send"}
        )
        executor._dag_runner.finalize_step = AsyncMock()
        executor._surface_emitter.emit_event = AsyncMock()

        reauth = MagicMock(spec=["defer_run", "apply_needs_reauth", "notify_reauth"])
        reauth.defer_run = AsyncMock()
        reauth.apply_needs_reauth = AsyncMock()
        reauth.notify_reauth = AsyncMock()
        executor._reauth_service = reauth

        step, run = self._make_step_run()
        await executor._execute_step(run, step)

        reauth.defer_run.assert_not_called()
        reauth.apply_needs_reauth.assert_not_called()
        executor._dag_runner.finalize_step.assert_called_once()
