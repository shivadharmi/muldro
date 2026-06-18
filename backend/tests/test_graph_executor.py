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
        run.context_pack_json = {}

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


@pytest.fixture
def executor_with_agent_deps(settings, mock_db):
    """GraphExecutor with agent loop dependencies for agentic execution tests."""

    async def mock_db_factory():
        """Mock async context manager for db_factory."""

        class MockDbContext:
            async def __aenter__(self):
                return mock_db

            async def __aexit__(self, *args):
                pass

        return MockDbContext()

    execute_tool_fn = AsyncMock()

    # Mock BudgetTracker with record_usage
    budget = MagicMock()
    budget.record_usage = AsyncMock(return_value=MagicMock(cost_usd=0.01))

    with patch("src.services.graph_executor.get_anthropic_client") as mock_client:
        mock_client.return_value = MagicMock()
        from src.services.graph_executor import GraphExecutor

        return GraphExecutor(
            settings,
            mock_db,
            db_factory=mock_db_factory,
            execute_tool_fn=execute_tool_fn,
            budget=budget,
            circuit_breaker=None,
        )


class TestAgenticStepExecution:
    """Tests for agent loop integration in GraphExecutor."""

    @patch("src.orchestrator.agent_loop.agent_loop")
    async def test_step_via_agent_loop_calls_loop(self, mock_agent_loop, executor_with_agent_deps):
        """Test that _run_step_via_agent_loop calls agent_loop and returns result."""
        from src.orchestrator.agent_loop import LoopDone

        # Mock agent_loop to yield LoopDone
        async def fake_agent_loop(**kwargs):
            yield LoopDone(agent="operator", text="Task completed successfully")

        mock_agent_loop.side_effect = fake_agent_loop
        executor_with_agent_deps._get_all_steps = AsyncMock(return_value=[])

        # Create mock step and run
        step = MagicMock()
        step.input_data = {"task_type": "summarize", "goal": "Test goal"}
        run = MagicMock()
        run.user_id = TEST_USER_ID
        run.workspace_id = "ws_test"
        run.run_id = "run_test"

        result = await executor_with_agent_deps._run_step_via_agent_loop(step, run)

        assert result["status"] == "completed"
        assert "Task completed successfully" in result["result"]

    @patch("src.orchestrator.agent_loop.agent_loop")
    async def test_step_via_agent_loop_passes_operator(
        self, mock_agent_loop, executor_with_agent_deps
    ):
        """Test that agent_loop is called with operator agent and max_tool_rounds=10."""
        from src.orchestrator.agent_loop import LoopDone

        captured_kwargs = {}

        async def fake_agent_loop(**kwargs):
            captured_kwargs.update(kwargs)
            yield LoopDone(agent="operator", text="Done")

        mock_agent_loop.side_effect = fake_agent_loop
        executor_with_agent_deps._get_all_steps = AsyncMock(return_value=[])

        step = MagicMock()
        step.input_data = {"task_type": "test_task"}
        run = MagicMock()
        run.user_id = TEST_USER_ID
        run.workspace_id = "ws_test"
        run.run_id = "run_test"

        await executor_with_agent_deps._run_step_via_agent_loop(step, run)

        assert captured_kwargs["agent"].name == "operator"
        assert captured_kwargs["max_tool_rounds"] == 10

    @patch("src.orchestrator.agent_loop.agent_loop")
    async def test_agent_loop_error_still_returns(self, mock_agent_loop, executor_with_agent_deps):
        """Test that LoopError is collected but result is still returned."""
        from src.orchestrator.agent_loop import LoopDone, LoopError

        async def fake_agent_loop(**kwargs):
            yield LoopError(agent="operator", message="Something went wrong")
            yield LoopDone(agent="operator", text="Recovered and completed")

        mock_agent_loop.side_effect = fake_agent_loop
        executor_with_agent_deps._get_all_steps = AsyncMock(return_value=[])

        step = MagicMock()
        step.input_data = {"task_type": "test_task"}
        run = MagicMock()
        run.user_id = TEST_USER_ID
        run.workspace_id = "ws_test"
        run.run_id = "run_test"

        result = await executor_with_agent_deps._run_step_via_agent_loop(step, run)

        assert result["status"] == "completed"
        assert "errors" in result
        assert len(result["errors"]) == 1
        assert "Something went wrong" in result["errors"][0]

    @patch("src.orchestrator.agent_loop.agent_loop")
    async def test_run_step_action_delegates_to_agent_loop(
        self, mock_agent_loop, executor_with_agent_deps
    ):
        """_run_step_action should delegate to _run_step_via_agent_loop when deps available."""
        from src.orchestrator.agent_loop import LoopDone

        async def fake_loop(**kwargs):
            yield LoopDone(agent="operator", text="Done via agent loop")

        mock_agent_loop.side_effect = fake_loop
        executor_with_agent_deps._get_all_steps = AsyncMock(return_value=[])

        step = MagicMock()
        step.step_id = "step_dispatch"
        step.input_data = {"task_type": "any_task", "goal": "Do something"}

        run = MagicMock()
        run.run_id = "run_dispatch"
        run.user_id = "usr_test"
        run.workspace_id = "ws_test"

        result = await executor_with_agent_deps._run_step_action(step, run)

        assert result["result"] == "Done via agent loop"

    @patch("src.orchestrator.agent_loop.agent_loop")
    async def test_prior_step_outputs_injected(self, mock_agent_loop, executor_with_agent_deps):
        """Completed predecessor step outputs are injected into the operator message."""
        from src.orchestrator.agent_loop import LoopDone

        captured_kwargs = {}

        async def fake_agent_loop(**kwargs):
            captured_kwargs.update(kwargs)
            yield LoopDone(agent="operator", text="Created page with content")

        mock_agent_loop.side_effect = fake_agent_loop

        # Simulate a completed perceiver step with output_data
        prior_step = MagicMock()
        prior_step.step_id = "step_perceiver"
        prior_step.status = "completed"
        prior_step.output_data = {"result": "# My Document\nFull markdown content here"}
        prior_step.input_data = {"capability": "file.read", "goal": "Read the markdown file"}

        current_step = MagicMock()
        current_step.step_id = "step_operator"
        current_step.input_data = {"capability": "notion.create_page", "goal": "Copy to Notion"}

        executor_with_agent_deps._get_all_steps = AsyncMock(return_value=[prior_step, current_step])

        run = MagicMock()
        run.run_id = "run_copy"
        run.user_id = TEST_USER_ID
        run.workspace_id = "ws_test"

        await executor_with_agent_deps._run_step_via_agent_loop(current_step, run)

        # Verify prior step output was injected into the message
        message = captured_kwargs["message"]
        assert "Prior step results" in message
        assert "Full markdown content here" in message

    async def test_run_step_action_falls_back_without_deps(self, settings, mock_db):
        """_run_step_action uses minimal fallback when agent loop deps are missing."""
        with patch("src.services.graph_executor.get_anthropic_client") as mock_client_fn:
            mock_client = MagicMock()
            response = MagicMock()
            response.content = [MagicMock(text='{"status": "completed", "result": "fallback"}')]
            mock_client.messages.create = AsyncMock(return_value=response)
            mock_client_fn.return_value = mock_client

            from src.services.graph_executor import GraphExecutor

            executor = GraphExecutor(settings, mock_db)  # No agent loop deps

        step = MagicMock()
        step.step_id = "step_fallback"
        step.input_data = {"task_type": "test_task"}

        run = MagicMock()
        run.run_id = "run_fallback"
        run.user_id = "usr_test"
        run.workspace_id = "ws_test"

        result = await executor._run_step_action(step, run)
        assert result["status"] == "completed"


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
        executor._assess_step_risk = AsyncMock(return_value="low")

        # Mock _run_step_action so we don't need full agent infra
        executor._run_step_action = AsyncMock(return_value={"status": "completed", "result": "ok"})
        executor._resolve_step_references = AsyncMock(return_value={"capability": "email.draft"})
        executor._finalize_step = AsyncMock()
        executor._emit_event = AsyncMock()

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

        executor._assess_step_risk = AsyncMock(return_value="low")
        executor._run_step_action = AsyncMock(return_value={"status": "completed", "result": "ok"})
        executor._resolve_step_references = AsyncMock(return_value={"task_type": "summarize"})
        executor._finalize_step = AsyncMock()
        executor._emit_event = AsyncMock()

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
        executor._assess_step_risk = AsyncMock()
        executor._run_step_action = AsyncMock(return_value={"status": "completed"})
        executor._resolve_step_references = AsyncMock(return_value={})
        executor._finalize_step = AsyncMock()
        executor._emit_event = AsyncMock()

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
        executor._assess_step_risk.assert_not_called()
        executor._run_step_action.assert_not_called()
        executor._finalize_step.assert_not_called()
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
        executor._run_step_action = AsyncMock(return_value={"status": "completed"})
        executor._finalize_step = AsyncMock()
        executor._emit_event = AsyncMock()

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
        executor._run_step_action.assert_not_called()
        executor._finalize_step.assert_not_called()
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
        executor._run_step_action = AsyncMock(return_value={"status": "completed"})
        executor._resolve_step_references = AsyncMock(return_value={})
        executor._finalize_step = AsyncMock()
        executor._emit_event = AsyncMock()

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
        executor._run_step_action.assert_called_once()
        executor._finalize_step.assert_called_once()
