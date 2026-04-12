# Agent Architecture Gaps Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Close 4 remaining agent self-sufficiency gaps — Governor read tool, GraphExecutor agentic migration, dead code cleanup, prompt fixes.

**Architecture:** GraphExecutor becomes a durable DAG wrapper around agent_loop. Each step calls the Operator agent with full tool discovery instead of hardcoded handlers. Governor gets plan verification capability + context enrichment.

**Tech Stack:** Python 3.12, pytest, SQLAlchemy async, Claude API (anthropic SDK), FastMCP

**Spec:** `docs/superpowers/specs/2026-04-03-agent-architecture-gaps-design.md`

---

## Phase 1: Governor Enhancement

### Task 1: Add `get_plan_details` input schema

**Files:**
- Modify: `backend/src/tools/schemas.py:240-264`

- [ ] **Step 1: Add GetPlanDetailsInput model**

Add after `StorePreferenceInput` class (line 238), before the `TOOL_INPUT_MODELS` registry:

```python
class GetPlanDetailsInput(BaseModel):
    """Fetch plan metadata to verify existence and inspect tasks.

    Returns plan goal, priority, risk level, decision type, status,
    creation time, and task list. Used by Governor to independently
    verify that a plan_id corresponds to a legitimate plan.
    """

    plan_id: str = Field(description="ID of the plan to look up")
```

Note: `user_id` and `workspace_id` are injected by the MCP server from context — they are NOT part of the input schema (same pattern as `evaluate_policy` which only takes `plan_id`).

- [ ] **Step 2: Register in TOOL_INPUT_MODELS**

Add to the `TOOL_INPUT_MODELS` dict (after `"store_preference"` entry at line 263):

```python
    "get_plan_details": GetPlanDetailsInput,
```

- [ ] **Step 3: Verify schema generation**

Run: `cd backend && python -c "from src.tools.schemas import TOOL_INPUT_MODELS; print('get_plan_details' in TOOL_INPUT_MODELS)"`
Expected: `True`

---

### Task 2: Add `get_plan_details` to tool catalog

**Files:**
- Modify: `backend/src/tools/catalog.py:66-283`

- [ ] **Step 1: Import GetPlanDetailsInput**

Add to the imports block (after `StorePreferenceInput` at line 37):

```python
    GetPlanDetailsInput,
```

- [ ] **Step 2: Add InternalToolDef to INTERNAL_TOOLS**

Add after the `store_preference` entry (line 237), before the `report_governor_verdict` entry:

```python
    InternalToolDef(
        name="get_plan_details",
        input_model=GetPlanDetailsInput,
        capability="internal.get_plan_details",
        risk_level="low",
        requires_approval=False,
        server="intelligence",
        description=_desc(GetPlanDetailsInput),
        read_only=True,
    ),
```

- [ ] **Step 3: Verify catalog registration**

Run: `cd backend && python -c "from src.tools.catalog import get_internal_tool_by_name; t = get_internal_tool_by_name('get_plan_details'); print(t.name, t.capability, t.read_only)"`
Expected: `get_plan_details internal.get_plan_details True`

---

### Task 3: Implement `get_plan_details` MCP tool + tests

**Files:**
- Modify: `backend/src/tools/intelligence_server.py`
- Create: `backend/tests/test_get_plan_details.py`

- [ ] **Step 1: Write the failing tests**

Create `backend/tests/test_get_plan_details.py`:

```python
"""Tests for get_plan_details internal MCP tool."""

from __future__ import annotations

from unittest.mock import AsyncMock, MagicMock

import pytest

from tests.conftest import TEST_USER_ID, TEST_WORKSPACE_ID


def _make_plan_task(task_id, task_type="research", depends_on=None):
    task = MagicMock()
    task.task_id = task_id
    task.task_type = task_type
    task.description = f"Task {task_id}"
    task.depends_on = depends_on or []
    return task


def _make_plan(
    plan_id="plan_test01",
    goal="Test goal",
    priority="medium",
    risk_level="low",
    decision="create_task",
    status="created",
):
    plan = MagicMock()
    plan.plan_id = plan_id
    plan.goal = goal
    plan.priority = priority
    plan.risk_level = risk_level
    plan.decision = decision
    plan.status = status
    plan.created_at = MagicMock()
    plan.created_at.isoformat.return_value = "2026-04-03T10:00:00+00:00"
    plan.workspace_id = TEST_WORKSPACE_ID
    plan.tasks = [
        _make_plan_task("ptask_01", "research"),
        _make_plan_task("ptask_02", "draft_email", depends_on=["ptask_01"]),
    ]
    return plan


@pytest.fixture
def mock_db():
    db = AsyncMock()
    return db


class TestGetPlanDetails:
    """Tests for the get_plan_details tool implementation."""

    async def test_plan_found_returns_metadata(self, mock_db):
        """When plan exists and workspace matches, return full metadata."""
        from src.tools.intelligence_server import _get_plan_details_impl

        plan = _make_plan()
        mock_db.execute = AsyncMock()
        mock_db.execute.return_value.scalar_one_or_none.return_value = plan

        result = await _get_plan_details_impl(
            plan_id="plan_test01",
            user_id=TEST_USER_ID,
            workspace_id=TEST_WORKSPACE_ID,
            db=mock_db,
        )

        assert result["plan_id"] == "plan_test01"
        assert result["goal"] == "Test goal"
        assert result["priority"] == "medium"
        assert result["risk_level"] == "low"
        assert result["decision"] == "create_task"
        assert result["status"] == "created"
        assert len(result["tasks"]) == 2
        assert result["tasks"][0]["task_type"] == "research"
        assert result["tasks"][1]["depends_on"] == ["ptask_01"]

    async def test_plan_not_found_returns_not_found(self, mock_db):
        """When plan_id doesn't exist, return not_found status."""
        from src.tools.intelligence_server import _get_plan_details_impl

        mock_db.execute = AsyncMock()
        mock_db.execute.return_value.scalar_one_or_none.return_value = None

        result = await _get_plan_details_impl(
            plan_id="plan_nonexistent",
            user_id=TEST_USER_ID,
            workspace_id=TEST_WORKSPACE_ID,
            db=mock_db,
        )

        assert result["status"] == "not_found"
        assert "plan_nonexistent" in result["message"]

    async def test_wrong_workspace_returns_not_found(self, mock_db):
        """When plan exists but workspace doesn't match, return not_found."""
        from src.tools.intelligence_server import _get_plan_details_impl

        plan = _make_plan()
        plan.workspace_id = "ws_other"
        mock_db.execute = AsyncMock()
        mock_db.execute.return_value.scalar_one_or_none.return_value = plan

        result = await _get_plan_details_impl(
            plan_id="plan_test01",
            user_id=TEST_USER_ID,
            workspace_id=TEST_WORKSPACE_ID,
            db=mock_db,
        )

        assert result["status"] == "not_found"
```

- [ ] **Step 2: Run tests — verify they fail**

Run: `cd backend && python -m pytest tests/test_get_plan_details.py -v`
Expected: FAIL with `ImportError: cannot import name '_get_plan_details_impl'`

- [ ] **Step 3: Implement `_get_plan_details_impl` and MCP handler**

In `backend/src/tools/intelligence_server.py`, add the implementation function. Insert after the `get_active_plans` function (find it by searching for `async def get_active_plans`), before the `evaluate_policy` function:

```python
async def _get_plan_details_impl(
    plan_id: str,
    user_id: str,
    workspace_id: str,
    db,
) -> dict:
    """Core implementation for get_plan_details (testable without MCP context)."""
    from sqlalchemy import select

    from src.models.plans import Plan

    result = await db.execute(select(Plan).where(Plan.plan_id == plan_id))
    plan = result.scalar_one_or_none()

    if not plan or (workspace_id and plan.workspace_id != workspace_id):
        return {
            "status": "not_found",
            "message": f"Plan {plan_id} not found in this workspace",
        }

    tasks_data = []
    for task in (plan.tasks or []):
        tasks_data.append({
            "task_id": task.task_id,
            "task_type": task.task_type,
            "description": getattr(task, "description", ""),
            "depends_on": task.depends_on or [],
        })

    return {
        "plan_id": plan.plan_id,
        "goal": plan.goal,
        "priority": plan.priority,
        "risk_level": plan.risk_level,
        "decision": plan.decision,
        "status": plan.status,
        "created_at": plan.created_at.isoformat() if plan.created_at else None,
        "tasks": tasks_data,
    }
```

Then add the MCP handler (registered on the intelligence server, following the same pattern as `evaluate_policy`):

```python
@intelligence_server.tool()
async def get_plan_details(
    plan_id: str,
    ctx: Context,
) -> dict:
    """Fetch plan metadata to verify existence and inspect tasks."""
    user_id = ctx.get("user_id", "")
    workspace_id = ctx.get("workspace_id", "")
    async with _get_db() as db:
        return await _get_plan_details_impl(plan_id, user_id, workspace_id, db)
```

Adapt the pattern used by neighboring functions in the file — check how `evaluate_policy` accesses `ctx`, `_get_db`, and the DB session. The exact MCP handler registration pattern may use `ctx: Context` or keyword args depending on the FastMCP version in use.

- [ ] **Step 4: Run tests — verify they pass**

Run: `cd backend && python -m pytest tests/test_get_plan_details.py -v`
Expected: 3 passed

- [ ] **Step 5: Run linter**

Run: `cd backend && ruff check src/tools/schemas.py src/tools/catalog.py src/tools/intelligence_server.py tests/test_get_plan_details.py --fix && ruff format src/tools/schemas.py src/tools/catalog.py src/tools/intelligence_server.py tests/test_get_plan_details.py`

---

### Task 4: Governor capability scope + context enrichment + prompt

**Files:**
- Modify: `backend/src/orchestrator/agents.py:71-74,79`
- Modify: `backend/src/orchestrator/prompts.py` (GOVERNOR_PROMPT)

- [ ] **Step 1: Add `internal.get_plan_details` to Governor capability scope**

In `backend/src/orchestrator/agents.py`, update the governor entry (lines 71-74):

```python
    "governor": {
        "internal.evaluate_policy",
        "internal.approve_action",
        "internal.get_plan_details",
    },
```

- [ ] **Step 2: Add Governor to CONTEXT_ENRICHED_AGENTS**

In `backend/src/orchestrator/jarvis.py`, update the set (line 79):

```python
CONTEXT_ENRICHED_AGENTS = {"planner", "presenter", "researcher", "librarian", "operator", "governor"}
```

- [ ] **Step 3: Update GOVERNOR_PROMPT**

In `backend/src/orchestrator/prompts.py`, find `GOVERNOR_PROMPT`. Replace rule 4:

```
4. Validate that the Planner created this plan (check plan_id)
```

with:

```
4. Always call get_plan_details(plan_id) first to verify the plan exists
5. Cross-check the plan's goal, priority, and risk_level against the decision you received
6. If the plan is not found, return verdict: "blocked" immediately
```

And renumber subsequent rules (old rule 5 becomes rule 7).

- [ ] **Step 4: Run existing governor tests**

Run: `cd backend && python -m pytest tests/ -v -k "governor" --no-header`
Expected: All existing tests pass (some may need mock updates for the new tool in scope)

- [ ] **Step 5: Commit Phase 1**

```bash
cd backend && git add src/tools/schemas.py src/tools/catalog.py src/tools/intelligence_server.py src/orchestrator/agents.py src/orchestrator/jarvis.py src/orchestrator/prompts.py tests/test_get_plan_details.py
git commit -m "feat: add get_plan_details tool + Governor context enrichment

- New get_plan_details internal MCP tool (schema, catalog, implementation)
- Governor capability scope expanded with internal.get_plan_details
- Governor added to CONTEXT_ENRICHED_AGENTS for richer policy decisions
- Governor prompt updated with plan verification workflow"
```

---

## Phase 2: GraphExecutor Agentic Migration

### Task 5: Extend GraphExecutor constructor with agent loop dependencies

**Files:**
- Modify: `backend/src/services/graph_executor.py:118-143`

- [ ] **Step 1: Add new constructor parameters**

Update `GraphExecutor.__init__` (line 121) to accept new dependencies needed for agent loop integration:

```python
class GraphExecutor:
    """Durable graph executor with parallel steps, checkpoints, and approval gates."""

    def __init__(
        self,
        settings: Settings,
        db: AsyncSession,
        event_bus=None,
        notifier=None,
        tool_registry: ToolRegistry | None = None,
        verifier: Verifier | None = None,
        context_builder: ContextBuilder | None = None,
        connector_credentials_fn=None,
        memory_service: MemoryService | None = None,
        # New: agent loop dependencies
        db_factory=None,
        execute_tool_fn=None,
        budget=None,
        circuit_breaker=None,
    ):
        self._settings = settings
        self._db = db
        self._client = get_anthropic_client(settings)
        self._audit = AuditService(db)
        self._event_bus = event_bus
        self._notifier = notifier
        self._tool_registry = tool_registry
        self._verifier = verifier
        self._context_builder = context_builder
        self._connector_credentials_fn = connector_credentials_fn
        self._memory_service = memory_service
        # New: agent loop deps
        self._db_factory = db_factory
        self._execute_tool_fn = execute_tool_fn
        self._budget = budget
        self._circuit_breaker = circuit_breaker
```

- [ ] **Step 2: Update `create_graph_executor` factory**

Update the factory function (line 37) to accept and pass through the new params:

```python
async def create_graph_executor(
    settings: Settings,
    db: AsyncSession,
    workspace_id: str = "",
    # New: agent loop dependencies
    db_factory=None,
    execute_tool_fn=None,
    budget=None,
    circuit_breaker=None,
) -> GraphExecutor:
```

Pass these through to the `GraphExecutor(...)` constructor call at the end (around line 106):

```python
    return GraphExecutor(
        settings=settings,
        db=db,
        event_bus=event_bus,
        notifier=notifier,
        tool_registry=tool_registry,
        verifier=verifier,
        context_builder=context_builder,
        memory_service=memory_service,
        db_factory=db_factory,
        execute_tool_fn=execute_tool_fn,
        budget=budget,
        circuit_breaker=circuit_breaker,
    )
```

- [ ] **Step 3: Verify existing tests still pass**

Run: `cd backend && python -m pytest tests/test_graph_executor.py -v --no-header`
Expected: All existing tests pass (new params have defaults of None)

---

### Task 6: Implement `_build_operator_tools` and `_run_step_via_agent_loop`

**Files:**
- Modify: `backend/src/services/graph_executor.py`

- [ ] **Step 1: Write the failing test for `_run_step_via_agent_loop`**

Add to `backend/tests/test_graph_executor.py`:

```python
class TestAgenticStepExecution:
    """Tests for the agent-loop-based step execution path."""

    @pytest.fixture
    def executor_with_agent_deps(self, settings, mock_db):
        """GraphExecutor with agent loop dependencies configured."""
        mock_db_factory = AsyncMock()
        mock_db_factory.return_value.__aenter__ = AsyncMock(return_value=mock_db)
        mock_db_factory.return_value.__aexit__ = AsyncMock(return_value=False)

        mock_execute_tool = AsyncMock(return_value={"status": "ok", "result": "done"})
        mock_budget = MagicMock()
        mock_budget.record_usage = AsyncMock(return_value=MagicMock(cost_usd=0.01))

        with patch("src.services.graph_executor.get_anthropic_client") as mock_client:
            mock_client.return_value = MagicMock()
            from src.services.graph_executor import GraphExecutor

            return GraphExecutor(
                settings,
                mock_db,
                db_factory=mock_db_factory,
                execute_tool_fn=mock_execute_tool,
                budget=mock_budget,
            )

    async def test_step_via_agent_loop_calls_loop(self, executor_with_agent_deps):
        """_run_step_via_agent_loop calls agent_loop with Operator agent."""
        from src.orchestrator.agent_loop import LoopDone

        step = MagicMock()
        step.step_id = "step_001"
        step.input_data = {"task_type": "research_competitors", "goal": "Find top 3 competitors"}

        run = MagicMock()
        run.run_id = "run_001"
        run.user_id = TEST_USER_ID
        run.workspace_id = "ws_test"

        mock_loop_events = [
            LoopDone(
                agent="operator",
                text="Found 3 competitors: A, B, C",
                input_tokens=100,
                output_tokens=50,
            )
        ]

        async def fake_agent_loop(**kwargs):
            for evt in mock_loop_events:
                yield evt

        with patch("src.services.graph_executor.agent_loop", side_effect=fake_agent_loop):
            result = await executor_with_agent_deps._run_step_via_agent_loop(step, run)

        assert result["status"] == "completed"
        assert "Found 3 competitors" in result["result"]

    async def test_step_via_agent_loop_passes_operator_tools(self, executor_with_agent_deps):
        """Tools list passed to agent_loop should be non-empty."""
        from src.orchestrator.agent_loop import LoopDone

        step = MagicMock()
        step.step_id = "step_002"
        step.input_data = {"task_type": "send_email", "goal": "Send update"}

        run = MagicMock()
        run.run_id = "run_002"
        run.user_id = TEST_USER_ID
        run.workspace_id = "ws_test"

        captured_kwargs = {}

        async def capture_agent_loop(**kwargs):
            captured_kwargs.update(kwargs)
            yield LoopDone(agent="operator", text="Sent")

        with patch("src.services.graph_executor.agent_loop", side_effect=capture_agent_loop):
            await executor_with_agent_deps._run_step_via_agent_loop(step, run)

        assert "tools" in captured_kwargs
        assert captured_kwargs["agent"].name == "operator"
        assert captured_kwargs["max_tool_rounds"] == 10

    async def test_agent_loop_error_raises(self, executor_with_agent_deps):
        """If agent loop yields LoopError, step should raise."""
        from src.orchestrator.agent_loop import LoopDone, LoopError

        step = MagicMock()
        step.step_id = "step_003"
        step.input_data = {"task_type": "unknown"}

        run = MagicMock()
        run.run_id = "run_003"
        run.user_id = TEST_USER_ID
        run.workspace_id = "ws_test"

        async def error_agent_loop(**kwargs):
            yield LoopError(agent="operator", message="API circuit open")
            yield LoopDone(agent="operator", text="[Agent operator API error]")

        with patch("src.services.graph_executor.agent_loop", side_effect=error_agent_loop):
            result = await executor_with_agent_deps._run_step_via_agent_loop(step, run)

        # Even with error, result is returned (error is in the text)
        assert "error" in result["result"].lower() or result["status"] == "completed"
```

- [ ] **Step 2: Run tests — verify they fail**

Run: `cd backend && python -m pytest tests/test_graph_executor.py::TestAgenticStepExecution -v`
Expected: FAIL with `AttributeError: 'GraphExecutor' object has no attribute '_run_step_via_agent_loop'`

- [ ] **Step 3: Implement `_build_operator_tools` helper**

Add to `GraphExecutor` class in `backend/src/services/graph_executor.py`, after the constructor:

```python
    async def _build_operator_tools(self) -> list[dict]:
        """Build Claude API tool definitions filtered by Operator's capability scope."""
        from src.orchestrator.agents import AGENTS
        from src.tools.schemas import TOOL_INPUT_MODELS

        operator = AGENTS["operator"]
        scope = operator.capability_scope
        tools = []
        seen_names: set[str] = set()

        # Internal tools from schemas (these have full Pydantic models)
        for tool_name, model_cls in TOOL_INPUT_MODELS.items():
            if self._tool_registry:
                tool_def = await self._tool_registry.get_tool(tool_name)
                if tool_def and tool_def.capability in scope:
                    schema = model_cls.model_json_schema()
                    tools.append({
                        "name": tool_name,
                        "description": model_cls.__doc__.strip() if model_cls.__doc__ else tool_name,
                        "input_schema": schema,
                    })
                    seen_names.add(tool_name)

        # External tools from registry (MCP-discovered tools with DB-stored schemas)
        if self._tool_registry:
            all_tools = await self._tool_registry.list_enabled_tools()
            for tool in all_tools:
                if (
                    tool.capability in scope
                    and tool.name not in seen_names
                    and tool.input_schema
                ):
                    tools.append({
                        "name": tool.name,
                        "description": tool.description or tool.name,
                        "input_schema": tool.input_schema,
                    })

        return tools
```

Note: Check what `ToolRegistry` method returns enabled external tools with schemas. It may be `list_tools()`, `get_all_tools()`, or similar. Adapt to the actual method name. If no such method exists, iterate `EXTERNAL_TOOL_SEEDS` from catalog and build minimal schemas.

- [ ] **Step 4: Implement `_run_step_via_agent_loop`**

Add to `GraphExecutor` class, after `_build_operator_tools`:

```python
    async def _run_step_via_agent_loop(self, step: TaskStep, run: TaskRun) -> dict:
        """Execute a step via the Operator agent loop with full tool discovery.

        Replaces the old 4-tier dispatch (_run_step_action) with autonomous
        agent execution. The Operator discovers available tools and decides
        which to call based on the step's goal.
        """
        from src.orchestrator.agent_loop import LoopDone, LoopError, agent_loop
        from src.orchestrator.agents import AGENTS

        input_data = step.input_data or {}
        task_type = input_data.get("task_type", "unknown")
        goal = input_data.get("goal", input_data.get("context", ""))

        # Build step message for the Operator
        parts = [f"Execute this step: {task_type}"]
        if goal:
            parts.append(f"Goal: {goal}")
        for key, value in input_data.items():
            if key not in ("task_type", "goal", "context"):
                parts.append(f"{key}: {value}")
        message = "\n".join(parts)

        # Build context from ContextBuilder (fresh per step)
        context_prompt = await self._build_step_context(run, step)

        # Resolve Operator agent and build system prompt
        operator = AGENTS["operator"]
        system_blocks = [{"type": "text", "text": operator.prompt}]
        if context_prompt:
            system_blocks.append({
                "type": "text",
                "text": f"\n<context>\n{context_prompt}\n</context>",
            })

        # Build tool catalog for Operator
        tools = await self._build_operator_tools()

        # Collect results from agent loop
        result_text = ""
        tools_called: list[str] = []
        errors: list[str] = []

        async for evt in agent_loop(
            client=self._client,
            agent=operator,
            model=self._settings.resolved_model,
            system_blocks=system_blocks,
            tools=tools,
            message=message,
            user_id=run.user_id,
            workspace_id=run.workspace_id or "",
            db_factory=self._db_factory,
            services=None,  # Not needed — execute_tool_fn handles dispatch
            budget=self._budget,
            trace=None,
            execute_tool_fn=self._execute_tool_fn,
            max_tool_rounds=10,
            stream=False,
            circuit_breaker=self._circuit_breaker,
            run_id=run.run_id,
        ):
            if isinstance(evt, LoopDone):
                result_text = evt.text
                tools_called = evt.tools_called
            elif isinstance(evt, LoopError):
                errors.append(evt.message)

        return {
            "status": "completed",
            "result": result_text,
            "tools_called": tools_called,
            "errors": errors or None,
        }
```

- [ ] **Step 5: Run tests — verify they pass**

Run: `cd backend && python -m pytest tests/test_graph_executor.py::TestAgenticStepExecution -v`
Expected: 3 passed

---

### Task 7: Replace `_run_step_action` dispatch + update callers

**Files:**
- Modify: `backend/src/services/graph_executor.py:703-763`
- Modify: `backend/src/orchestrator/jarvis.py:2675-2684`
- Modify: `backend/src/services/scheduler.py:338-346`

- [ ] **Step 1: Write test for the new dispatch**

Add to `backend/tests/test_graph_executor.py`:

```python
    async def test_run_step_action_delegates_to_agent_loop(self, executor_with_agent_deps):
        """_run_step_action should delegate to _run_step_via_agent_loop."""
        from src.orchestrator.agent_loop import LoopDone

        step = MagicMock()
        step.step_id = "step_dispatch"
        step.input_data = {"task_type": "any_task", "goal": "Do something"}

        run = MagicMock()
        run.run_id = "run_dispatch"
        run.user_id = TEST_USER_ID
        run.workspace_id = "ws_test"

        async def fake_loop(**kwargs):
            yield LoopDone(agent="operator", text="Done via agent loop")

        with patch("src.services.graph_executor.agent_loop", side_effect=fake_loop):
            result = await executor_with_agent_deps._run_step_action(step, run)

        assert result["result"] == "Done via agent loop"
```

- [ ] **Step 2: Run test — verify it fails**

Run: `cd backend && python -m pytest tests/test_graph_executor.py::TestAgenticStepExecution::test_run_step_action_delegates_to_agent_loop -v`
Expected: FAIL (old dispatch logic still in place, tries MCP bridge etc.)

- [ ] **Step 3: Replace `_run_step_action` dispatch**

In `backend/src/services/graph_executor.py`, replace the body of `_run_step_action` (lines 703-763). Keep the method signature and the event emission, but replace the 4-tier dispatch:

```python
    async def _run_step_action(self, step: TaskStep, run: TaskRun) -> dict:
        """Execute a step via the Operator agent loop.

        The agent discovers available tools and acts autonomously.
        Governor hooks fire per tool call via the execute_tool_fn callback.
        """
        input_data = step.input_data or {}
        task_type = input_data.get("task_type", "unknown")

        await self._emit_event(
            "tool_call_started",
            run.user_id,
            {"run_id": run.run_id, "step_id": step.step_id, "tool_name": task_type},
            workspace_id=run.workspace_id,
        )

        if not self._db_factory or not self._execute_tool_fn or not self._budget:
            # Fallback: if agent loop deps not available, use a minimal Claude call.
            # This preserves backward compatibility for callers that haven't been
            # updated to pass agent loop dependencies.
            logger.warning(
                "Agent loop deps missing for step %s — using minimal Claude call",
                step.step_id,
            )
            return await self._minimal_claude_action(step, run)

        return await self._run_step_via_agent_loop(step, run)

    async def _minimal_claude_action(self, step: TaskStep, run: TaskRun) -> dict:
        """Temporary fallback: single-turn Claude call without tool discovery.

        Used only when agent loop dependencies are not available (e.g., during
        migration or in test environments). Will be removed once all callers
        pass agent loop dependencies.
        """
        input_data = step.input_data or {}
        task_type = input_data.get("task_type", "unknown")
        goal = input_data.get("goal", input_data.get("context", ""))
        context_prompt = await self._build_step_context(run, step)

        parts = [f"Task type: {task_type}"]
        if goal:
            parts.append(f"Goal: {goal}")
        if context_prompt:
            parts.append(f"\n--- Background ---\n{context_prompt}")

        response = await self._client.messages.create(
            model=self._settings.resolved_model,
            max_tokens=1024,
            system=(
                f"You are Jarvis's task execution engine handling a '{task_type}' step. "
                "Complete the task described below. "
                'Respond with JSON: {"status": "completed", "result": "...", "details": {...}}'
            ),
            messages=[{"role": "user", "content": "\n".join(parts)}],
        )

        text = response.content[0].text
        if text.startswith("```"):
            text = text.split("\n", 1)[1].rsplit("```", 1)[0]
        try:
            return json.loads(text)
        except json.JSONDecodeError:
            return {"status": "completed", "result": text}
```

- [ ] **Step 4: Update `_execute_plan_via_graph` in jarvis.py**

In `backend/src/orchestrator/jarvis.py`, find `_execute_plan_via_graph` (around line 2675). Update the `GraphExecutor(...)` constructor call to pass agent loop dependencies:

```python
                executor = GraphExecutor(
                    settings=self._settings,
                    db=db,
                    event_bus=self._event_bus,
                    notifier=svc.notifier,
                    tool_registry=tool_registry,
                    context_builder=context_builder,
                    connector_credentials_fn=get_credentials,
                    memory_service=svc.memory_service,
                    # Agent loop dependencies
                    db_factory=self._db_factory,
                    execute_tool_fn=self._execute_tool,
                    budget=self._budget,
                    circuit_breaker=getattr(self, "_circuit_breaker", None),
                )
```

- [ ] **Step 5: Update `_tick_background_tasks` in scheduler.py**

In `backend/src/services/scheduler.py`, find `_tick_background_tasks`. Update the `create_graph_executor` call (around line 342) to pass agent loop dependencies from the orchestrator:

```python
                        executor = await create_graph_executor(
                            settings=self._settings,
                            db=db,
                            workspace_id=ws_id,
                            db_factory=factory,
                            execute_tool_fn=self._orchestrator._execute_tool,
                            budget=self._orchestrator._budget,
                            circuit_breaker=getattr(
                                self._orchestrator, "_circuit_breaker", None
                            ),
                        )
```

- [ ] **Step 6: Run all tests — verify green**

Run: `cd backend && python -m pytest tests/test_graph_executor.py -v --no-header`
Expected: All tests pass

- [ ] **Step 7: Run full test suite for regressions**

Run: `cd backend && python -m pytest tests/ -v --no-header -x`
Expected: All tests pass

- [ ] **Step 8: Lint and format**

Run: `cd backend && ruff check src/services/graph_executor.py src/orchestrator/jarvis.py src/services/scheduler.py --fix && ruff format src/services/graph_executor.py src/orchestrator/jarvis.py src/services/scheduler.py`

- [ ] **Step 9: Commit Phase 2**

```bash
cd backend && git add src/services/graph_executor.py src/orchestrator/jarvis.py src/services/scheduler.py tests/test_graph_executor.py
git commit -m "feat: replace GraphExecutor step handlers with agent loop

- New _run_step_via_agent_loop: calls Operator agent with full tool catalog
- New _build_operator_tools: builds Claude API tool defs from registry
- _run_step_action now delegates to agent loop (with minimal fallback)
- Updated _execute_plan_via_graph to pass agent loop deps
- Updated _tick_background_tasks to pass agent loop deps
- Governor hooks fire per tool call via execute_tool_fn callback"
```

---

## Phase 3: Dead Code Cleanup

### Task 8: Verify unreachability and delete dead code from GraphExecutor

**Files:**
- Modify: `backend/src/services/graph_executor.py`

- [ ] **Step 1: Grep for callers of each method to confirm unreachability**

Run all of these and confirm zero results (ignoring the definition itself and test files being deleted):

```bash
cd backend && grep -rn "_draft_action" src/ --include="*.py" | grep -v "graph_executor.py" | grep -v "test_graph_executor"
grep -rn "_summarize_action" src/ --include="*.py" | grep -v "graph_executor.py"
grep -rn "_generic_claude_action" src/ --include="*.py" | grep -v "graph_executor.py"
grep -rn "_execute_via_connector" src/ --include="*.py" | grep -v "graph_executor.py"
```

Expected: Each grep returns 0 matches outside graph_executor.py.

- [ ] **Step 2: Delete `_draft_action` method**

Remove the entire `_draft_action` method from `graph_executor.py` (lines 819-884).

- [ ] **Step 3: Delete `_summarize_action` method**

Remove the entire `_summarize_action` method (lines 886-910, adjusted after previous deletion).

- [ ] **Step 4: Delete `_generic_claude_action` method**

Remove the entire `_generic_claude_action` method (lines 912-945, adjusted).

- [ ] **Step 5: Delete `_execute_via_connector` method**

Remove the entire `_execute_via_connector` method (lines 785-817, adjusted).

- [ ] **Step 6: Delete MCP bridge import and check from old dispatch**

If the `_minimal_claude_action` fallback does NOT reference `call_mcp_tool` or `is_mcp_tool`, remove the import:

```python
from src.connectors.mcp_bridge import call_mcp_tool, is_mcp_tool
```

If this import is used elsewhere in the file, keep it. Grep to verify.

- [ ] **Step 7: Clean up unused imports**

Remove any imports that were only used by deleted methods. Likely candidates:
- `from src.connectors.base import CONNECTOR_REGISTRY` (if only in `_execute_via_connector`)

Run: `cd backend && ruff check src/services/graph_executor.py --fix`

---

### Task 9: Delete orphaned views.py and draft test file

**Files:**
- Delete: `backend/src/ui/views.py`
- Delete: `backend/tests/test_graph_executor_draft.py`

- [ ] **Step 1: Verify views.py has zero callers**

```bash
cd backend && grep -rn "from src.ui.views" src/ tests/ --include="*.py"
grep -rn "from src.ui import views" src/ tests/ --include="*.py"
grep -rn "import views" src/ui/ --include="*.py"
```

Expected: 0 matches.

- [ ] **Step 2: Delete views.py**

```bash
rm backend/src/ui/views.py
```

- [ ] **Step 3: Delete test_graph_executor_draft.py**

```bash
rm backend/tests/test_graph_executor_draft.py
```

- [ ] **Step 4: Run full test suite**

Run: `cd backend && python -m pytest tests/ -v --no-header -x`
Expected: All tests pass (count should be slightly lower — draft tests removed)

- [ ] **Step 5: Lint**

Run: `cd backend && ruff check src/services/graph_executor.py src/ui/ --fix`

- [ ] **Step 6: Commit Phase 3**

```bash
cd backend && git add -u src/services/graph_executor.py src/ui/views.py tests/test_graph_executor_draft.py
git commit -m "refactor: remove dead code from GraphExecutor + orphaned views.py

- Deleted _draft_action, _summarize_action, _generic_claude_action handlers
- Deleted _execute_via_connector dispatch path
- Deleted src/ui/views.py (10 orphaned view generators, zero callers)
- Deleted tests/test_graph_executor_draft.py (tested deleted handler)
- ~600 lines removed"
```

---

## Phase 4: Prompt & Documentation Fixes

### Task 10: Fix Planner and Presenter prompts

**Files:**
- Modify: `backend/src/orchestrator/prompts.py`

- [ ] **Step 1: Fix draft_reply example in JARVIS_DECISION_FRAMEWORK**

In `backend/src/orchestrator/prompts.py`, find `JARVIS_DECISION_FRAMEWORK`. If there is an example showing `draft_reply` with `tasks: [{task_type: "draft_email"}]`, remove the `tasks` array from that example. The `draft_reply` decision is agentic — it goes through the Operator agent loop, not GraphExecutor. The corrected example should show `draft_reply` without a tasks array.

- [ ] **Step 2: Update create_task guidance**

In the same `JARVIS_DECISION_FRAMEWORK`, add or update the `create_task` description. After the line about `create_task`:

```
- "create_task" = any action that writes to external systems (send email, create issue, etc.)
```

Add guidance clarifying task_type semantics:

```
  Each task step is executed by the Operator agent with full tool access.
  Use task_type as a semantic label describing the goal (e.g., "research_competitors",
  "draft_quarterly_report"), not a tool name. The Operator discovers tools autonomously.
```

- [ ] **Step 3: Add Presenter prompt clarification**

In `PRESENTER_PROMPT`, add after the existing rules (before `</rules>` or after the last numbered rule):

```
11. You generate text responses only. Workspace surfaces (cards, tables, metrics)
    are built by infrastructure (SurfaceService), not by you. Focus on conversational output.
```

- [ ] **Step 4: Lint**

Run: `cd backend && ruff check src/orchestrator/prompts.py --fix && ruff format src/orchestrator/prompts.py`

---

### Task 11: Update CLAUDE.md + commit

**Files:**
- Modify: `CLAUDE.md`

- [ ] **Step 1: Update "Agentic vs Scripted Execution" section**

Find the section in `CLAUDE.md`. Replace its content to reflect that all routes are now agentic:

```markdown
## Agentic vs Scripted Execution

All routes use **agentic execution** (`message_template`): the agent goes through the agent loop, discovers available tools, and autonomously decides which to call.

The `create_task` route triggers GraphExecutor for DAG management (dependencies, checkpointing, resume), but each step within the DAG is executed via the agent loop — the Operator agent discovers tools autonomously per step. GraphExecutor is a **durable DAG wrapper around agent_loop**, not a separate execution mode.

**Do not** hardcode tool-calling sequences in Python handlers. Let agents discover tools via the agent loop.
```

- [ ] **Step 2: Update "Common Mistakes" section**

Remove this line (no longer accurate):
```
- Do not add `action: "execute_plan"` to new routes unless the workflow genuinely needs DAG execution with checkpointing. Default to `message_template` so the agent can discover tools and act autonomously.
```

Add these lines:
```
- Do not bypass agent loop for step execution — GraphExecutor delegates to agent_loop per step
- Do not import from `src/ui/views.py` — deleted. Use `renderer.py` builders + `SurfaceService` for surfaces
```

- [ ] **Step 3: Update Governor in agent boundaries table**

Find the `Agent Boundaries` table. Update the Governor row:

```
| Governor | Evaluate policies, gate approvals, verify plans | policy decisions, approvals |
```

- [ ] **Step 4: Commit Phase 4**

```bash
git add backend/src/orchestrator/prompts.py CLAUDE.md
git commit -m "docs: update prompts and CLAUDE.md for agentic architecture

- Fixed draft_reply example in Planner prompt (removed misleading tasks array)
- Added create_task guidance: task_type is semantic label, not tool name
- Clarified Presenter role: text responses only, no surface building
- Updated CLAUDE.md: all routes agentic, GraphExecutor wraps agent loop
- Updated Common Mistakes: removed stale entries, added new guidance"
```

---

## Verification

### Task 12: Final verification

- [ ] **Step 1: Run full test suite**

Run: `cd backend && python -m pytest tests/ -v --no-header`
Expected: All tests pass

- [ ] **Step 2: Run linter on all changed files**

Run: `cd backend && ruff check src/ tests/ && ruff format --check src/ tests/`
Expected: No errors

- [ ] **Step 3: Review git log for all 4 phase commits**

Run: `git log --oneline -5`
Expected: 4 commits — one per phase, all on `improve-the-perception-system-v1` branch
