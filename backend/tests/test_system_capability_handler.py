"""Tests for _handle_system_capability, plan persistence, and public orchestrator methods."""

from unittest.mock import AsyncMock, MagicMock, patch

import pytest

from src.orchestrator.contracts import PlanOutput, PlanStep


def _make_orchestrator():
    """Create a minimal JarvisOrchestrator with mocked deps."""
    from src.orchestrator.jarvis import JarvisOrchestrator

    settings = MagicMock()
    settings.use_bedrock = False
    settings.daily_token_budget_usd = 10.0
    settings.redis_url = "redis://localhost:6379"
    db_factory = MagicMock()
    services = MagicMock()
    services.memory_service = AsyncMock()
    services.memory_service.store_goal_memory = AsyncMock(return_value="mem_test123")
    services.memory_service.store_instruction_memory = AsyncMock(return_value="mem_instr456")
    services.memory_service.store_briefing_memory = AsyncMock(return_value="mem_brief789")
    services.redis = None

    with patch("src.orchestrator.jarvis.get_anthropic_client"):
        orch = JarvisOrchestrator(settings=settings, db_factory=db_factory, services=services)
    return orch


class TestHandleSystemCapability:
    """system.* capability steps route to the correct direct handler."""

    @pytest.mark.asyncio
    async def test_system_set_goal(self):
        orch = _make_orchestrator()
        step = PlanStep(
            step_id="s1",
            description="Set goal: launch by April",
            capability="system.set_goal",
            input={},
        )
        plan = PlanOutput(
            goal="Launch product by April",
            reasoning="User wants to set a goal",
            priority="high",
            steps=[step],
        )
        result = await orch._handle_system_capability(step, plan, "usr_1", "ws_1")
        assert result["status"] == "created"
        assert result["memory_id"] == "mem_test123"
        orch._services.memory_service.store_goal_memory.assert_called_once_with(
            user_id="usr_1",
            workspace_id="ws_1",
            title="Set goal: launch by April",
            priority="high",
        )

    @pytest.mark.asyncio
    async def test_system_set_instruction(self):
        orch = _make_orchestrator()
        step = PlanStep(
            step_id="s1",
            description="Summarize email every morning",
            capability="system.set_instruction",
            input={
                "instruction": {
                    "instruction_text": "Summarize email every morning",
                    "instruction_type": "schedule",
                }
            },
        )
        plan = PlanOutput(goal="Set recurring instruction", steps=[step])
        result = await orch._handle_system_capability(step, plan, "usr_1", "ws_1")
        assert result["status"] == "created"
        assert result["memory_id"] == "mem_instr456"

    @pytest.mark.asyncio
    async def test_system_add_to_brief(self):
        orch = _make_orchestrator()
        step = PlanStep(
            step_id="s1",
            description="Add investor update to briefing",
            capability="system.add_to_brief",
            input={},
        )
        plan = PlanOutput(goal="Add to briefing", steps=[step])
        result = await orch._handle_system_capability(step, plan, "usr_1", "ws_1")
        assert result["status"] == "stored"
        assert result["memory_id"] == "mem_brief789"

    @pytest.mark.asyncio
    async def test_system_schedule_reminder(self):
        orch = _make_orchestrator()
        # Mock DB for schedule creation
        mock_db = AsyncMock()
        mock_db.commit = AsyncMock()
        mock_db.add = MagicMock()
        ctx = AsyncMock()
        ctx.__aenter__ = AsyncMock(return_value=mock_db)
        ctx.__aexit__ = AsyncMock(return_value=False)
        orch._db_factory = MagicMock(return_value=ctx)

        step = PlanStep(
            step_id="s1",
            description="Remind me to call John at 3pm",
            capability="system.schedule_reminder",
            input={"cron_expr": "0 15 * * *"},
        )
        plan = PlanOutput(goal="Schedule reminder", priority="medium", steps=[step])
        result = await orch._handle_system_capability(step, plan, "usr_1", "ws_1")
        assert result["status"] == "created"
        assert "schedule_id" in result

    @pytest.mark.asyncio
    async def test_system_respond_returns_empty(self):
        orch = _make_orchestrator()
        step = PlanStep(step_id="s1", description="Respond", capability="system.respond")
        plan = PlanOutput(goal="Respond", steps=[step])
        result = await orch._handle_system_capability(step, plan, "usr_1", "ws_1")
        assert result == {}

    @pytest.mark.asyncio
    async def test_unknown_system_capability_returns_empty(self):
        orch = _make_orchestrator()
        step = PlanStep(step_id="s1", description="?", capability="system.unknown_thing")
        plan = PlanOutput(goal="?", steps=[step])
        result = await orch._handle_system_capability(step, plan, "usr_1", "ws_1")
        assert result == {}


class TestPublicOrchestratorMethods:
    """get_budget_status() and get_system_health() — public API for Telegram."""

    @pytest.mark.asyncio
    async def test_get_budget_status(self):
        orch = _make_orchestrator()
        mock_status = MagicMock()
        mock_status.daily_spend_usd = 1.5
        mock_status.daily_limit_usd = 10.0
        orch._budget = MagicMock()
        orch._budget.get_budget_status = AsyncMock(return_value=mock_status)

        mock_db = AsyncMock()
        ctx = AsyncMock()
        ctx.__aenter__ = AsyncMock(return_value=mock_db)
        ctx.__aexit__ = AsyncMock(return_value=False)
        orch._db_factory = MagicMock(return_value=ctx)

        status = await orch.get_budget_status()
        assert status.daily_spend_usd == 1.5
        assert status.daily_limit_usd == 10.0

    @pytest.mark.asyncio
    async def test_get_system_health(self):
        orch = _make_orchestrator()
        orch._circuit_breaker = MagicMock()
        orch._circuit_breaker.is_open = MagicMock(return_value=False)
        orch._background_tasks = set()

        health = await orch.get_system_health()
        assert health["circuit_breaker_open"] is False
        assert health["background_tasks"] == 0
        assert "agents" in health


class TestPlannerSystemPrompt:
    """Planner gets capability summary, not JARVIS_DECISION_FRAMEWORK."""

    def test_build_system_prompt_planner_with_cap_summary(self):
        orch = _make_orchestrator()
        from src.orchestrator.agents import AGENTS

        planner = AGENTS["planner"]
        blocks = orch._build_system_prompt(
            planner,
            context="",
            capability_summary=(
                "<connected_services>\n  Email: search, read\n</connected_services>"
            ),
        )
        prompt_text = blocks[0]["text"]
        assert "Email: search, read" in prompt_text
        assert "decision_framework" not in prompt_text.lower()

    def test_build_system_prompt_non_planner_ignores_cap_summary(self):
        orch = _make_orchestrator()
        from src.orchestrator.agents import AGENTS

        presenter = AGENTS["presenter"]
        blocks = orch._build_system_prompt(
            presenter, context="", capability_summary="should not appear"
        )
        prompt_text = blocks[0]["text"]
        assert "should not appear" not in prompt_text

    def test_build_system_prompt_planner_without_cap_summary(self):
        orch = _make_orchestrator()
        from src.orchestrator.agents import AGENTS

        planner = AGENTS["planner"]
        blocks = orch._build_system_prompt(planner, context="")
        prompt_text = blocks[0]["text"]
        # Raw placeholder should remain when no summary provided
        assert "{capability_summary}" in prompt_text


class TestPlanPersistence:
    """_persist_plan_record accepts PlanOutput and creates Plan + PlanTasks."""

    @pytest.mark.asyncio
    async def test_persist_plan_record_with_plan_output(self):
        orch = _make_orchestrator()

        mock_db = AsyncMock()
        mock_db.commit = AsyncMock()
        mock_db.add = MagicMock()
        mock_db.execute = AsyncMock(
            return_value=MagicMock(scalar_one_or_none=MagicMock(return_value=None))
        )
        ctx = AsyncMock()
        ctx.__aenter__ = AsyncMock(return_value=mock_db)
        ctx.__aexit__ = AsyncMock(return_value=False)
        orch._db_factory = MagicMock(return_value=ctx)

        plan = PlanOutput(
            goal="Send follow-up email",
            reasoning="User wants to follow up with investor",
            priority="high",
            steps=[
                PlanStep(
                    step_id="s1",
                    description="Read email",
                    capability="email.read",
                    risk="none",
                ),
                PlanStep(
                    step_id="s2",
                    description="Draft reply",
                    capability="email.draft",
                    depends_on=["s1"],
                    risk="medium",
                ),
            ],
        )

        result = await orch._persist_plan_record(plan, "usr_1", "ws_1")
        assert isinstance(result, PlanOutput)
        assert result.plan_id is not None
        assert result.plan_id.startswith("plan_")
        assert mock_db.add.called
        assert mock_db.commit.called

    @pytest.mark.asyncio
    async def test_persist_plan_record_skips_user_steps(self):
        orch = _make_orchestrator()

        added_objects = []
        mock_db = AsyncMock()
        mock_db.commit = AsyncMock()
        mock_db.add = MagicMock(side_effect=lambda obj: added_objects.append(obj))
        mock_db.execute = AsyncMock(
            return_value=MagicMock(scalar_one_or_none=MagicMock(return_value=None))
        )
        ctx = AsyncMock()
        ctx.__aenter__ = AsyncMock(return_value=mock_db)
        ctx.__aexit__ = AsyncMock(return_value=False)
        orch._db_factory = MagicMock(return_value=ctx)

        plan = PlanOutput(
            goal="Send email",
            steps=[
                PlanStep(
                    step_id="s1",
                    description="Draft",
                    capability="email.draft",
                    risk="medium",
                ),
                PlanStep(
                    step_id="s2",
                    description="User reviews",
                    capability="email.send",
                    actor="user",
                ),
            ],
        )

        await orch._persist_plan_record(plan, "usr_1", "ws_1")
        from src.models.plans import Plan

        plans = [o for o in added_objects if isinstance(o, Plan)]
        assert len(plans) == 1
        assert len(plans[0].tasks) == 1  # Only jarvis step becomes a task

    @pytest.mark.asyncio
    async def test_persist_plan_record_maps_depends_on(self):
        """Step depends_on step_ids are mapped to task_ids."""
        orch = _make_orchestrator()

        added_objects = []
        mock_db = AsyncMock()
        mock_db.commit = AsyncMock()
        mock_db.add = MagicMock(side_effect=lambda obj: added_objects.append(obj))
        mock_db.execute = AsyncMock(
            return_value=MagicMock(scalar_one_or_none=MagicMock(return_value=None))
        )
        ctx = AsyncMock()
        ctx.__aenter__ = AsyncMock(return_value=mock_db)
        ctx.__aexit__ = AsyncMock(return_value=False)
        orch._db_factory = MagicMock(return_value=ctx)

        plan = PlanOutput(
            goal="Multi-step email workflow",
            steps=[
                PlanStep(step_id="s1", description="Search", capability="email.search"),
                PlanStep(
                    step_id="s2",
                    description="Draft",
                    capability="email.draft",
                    depends_on=["s1"],
                    risk="medium",
                ),
                PlanStep(
                    step_id="s3",
                    description="Send",
                    capability="email.send",
                    depends_on=["s2"],
                    risk="high",
                ),
            ],
        )

        await orch._persist_plan_record(plan, "usr_1", "ws_1")
        from src.models.plans import Plan

        plans = [o for o in added_objects if isinstance(o, Plan)]
        assert len(plans) == 1
        tasks = plans[0].tasks
        assert len(tasks) == 3

        # First task has no depends_on
        assert tasks[0].depends_on is None or tasks[0].depends_on == []
        # Second task depends on first task's task_id
        assert tasks[1].depends_on == [tasks[0].task_id]
        # Third task depends on second task's task_id
        assert tasks[2].depends_on == [tasks[1].task_id]

    @pytest.mark.asyncio
    async def test_persist_plan_record_derives_risk_and_execution_mode(self):
        """Max step risk determines Plan risk_level and execution_mode."""
        orch = _make_orchestrator()

        added_objects = []
        mock_db = AsyncMock()
        mock_db.commit = AsyncMock()
        mock_db.add = MagicMock(side_effect=lambda obj: added_objects.append(obj))
        mock_db.execute = AsyncMock(
            return_value=MagicMock(scalar_one_or_none=MagicMock(return_value=None))
        )
        ctx = AsyncMock()
        ctx.__aenter__ = AsyncMock(return_value=mock_db)
        ctx.__aexit__ = AsyncMock(return_value=False)
        orch._db_factory = MagicMock(return_value=ctx)

        # Plan with high risk step
        plan = PlanOutput(
            goal="Delete everything",
            steps=[
                PlanStep(
                    step_id="s1",
                    description="Delete files",
                    capability="fs.delete",
                    risk="high",
                ),
            ],
        )

        await orch._persist_plan_record(plan, "usr_1", "ws_1")
        from src.models.plans import Plan

        plans = [o for o in added_objects if isinstance(o, Plan)]
        assert plans[0].risk_level == "high"
        assert plans[0].execution_mode == "approval_required"

    @pytest.mark.asyncio
    async def test_persist_plan_record_low_risk_auto_execute(self):
        """Low risk plans get auto_execute execution_mode."""
        orch = _make_orchestrator()

        added_objects = []
        mock_db = AsyncMock()
        mock_db.commit = AsyncMock()
        mock_db.add = MagicMock(side_effect=lambda obj: added_objects.append(obj))
        mock_db.execute = AsyncMock(
            return_value=MagicMock(scalar_one_or_none=MagicMock(return_value=None))
        )
        ctx = AsyncMock()
        ctx.__aenter__ = AsyncMock(return_value=mock_db)
        ctx.__aexit__ = AsyncMock(return_value=False)
        orch._db_factory = MagicMock(return_value=ctx)

        plan = PlanOutput(
            goal="Read emails",
            steps=[
                PlanStep(
                    step_id="s1",
                    description="Search inbox",
                    capability="email.search",
                    risk="low",
                ),
            ],
        )

        await orch._persist_plan_record(plan, "usr_1", "ws_1")
        from src.models.plans import Plan

        plans = [o for o in added_objects if isinstance(o, Plan)]
        assert plans[0].risk_level == "low"
        assert plans[0].execution_mode == "auto_execute"

    @pytest.mark.asyncio
    async def test_persist_plan_record_idempotency_skip(self):
        """Duplicate idempotency key returns plan without persisting."""
        orch = _make_orchestrator()

        mock_db = AsyncMock()
        mock_db.commit = AsyncMock()
        mock_db.add = MagicMock()
        # Simulate existing plan found
        mock_db.execute = AsyncMock(
            return_value=MagicMock(scalar_one_or_none=MagicMock(return_value="plan_existing123"))
        )
        ctx = AsyncMock()
        ctx.__aenter__ = AsyncMock(return_value=mock_db)
        ctx.__aexit__ = AsyncMock(return_value=False)
        orch._db_factory = MagicMock(return_value=ctx)

        plan = PlanOutput(
            goal="Something",
            steps=[
                PlanStep(step_id="s1", description="Do it", capability="do.it"),
            ],
        )

        result = await orch._persist_plan_record(plan, "usr_1", "ws_1", idempotency_key="test_key")
        # Should return original plan without plan_id (not persisted)
        assert result.plan_id is None
        mock_db.add.assert_not_called()


class TestLightweightRun:
    """_create_lightweight_run accepts PlanOutput."""

    @pytest.mark.asyncio
    async def test_create_lightweight_run_with_plan_output(self):
        orch = _make_orchestrator()

        added_objects = []
        mock_db = AsyncMock()
        mock_db.commit = AsyncMock()
        mock_db.add = MagicMock(side_effect=lambda obj: added_objects.append(obj))
        ctx = AsyncMock()
        ctx.__aenter__ = AsyncMock(return_value=mock_db)
        ctx.__aexit__ = AsyncMock(return_value=False)
        orch._db_factory = MagicMock(return_value=ctx)

        plan = PlanOutput(
            goal="Answer a question",
            reasoning="Simple factual query",
            plan_id="plan_abc123",
        )

        run_id = await orch._create_lightweight_run(
            user_id="usr_1",
            workspace_id="ws_1",
            plan=plan,
            trace_id="trace_xyz",
            conversation_id="conv_1",
        )

        assert run_id is not None
        assert run_id.startswith("run_")
        assert len(added_objects) == 2  # TaskRun + TaskStep

        from src.models.task_graph import TaskRun, TaskStep

        runs = [o for o in added_objects if isinstance(o, TaskRun)]
        steps = [o for o in added_objects if isinstance(o, TaskStep)]
        assert len(runs) == 1
        assert len(steps) == 1

        assert runs[0].plan_id == "plan_abc123"
        assert runs[0].status == "running"
        assert steps[0].step_type == "plan"
        assert steps[0].input_data["goal"] == "Answer a question"

    @pytest.mark.asyncio
    async def test_create_lightweight_run_db_failure_returns_none(self):
        orch = _make_orchestrator()

        ctx = AsyncMock()
        ctx.__aenter__ = AsyncMock(side_effect=Exception("DB down"))
        ctx.__aexit__ = AsyncMock(return_value=False)
        orch._db_factory = MagicMock(return_value=ctx)

        plan = PlanOutput(goal="test")
        result = await orch._create_lightweight_run(
            user_id="usr_1",
            workspace_id="ws_1",
            plan=plan,
            trace_id="trace_1",
        )
        assert result is None
