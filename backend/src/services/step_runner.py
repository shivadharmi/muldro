"""StepRunner — agentic execution of a single DAG step.

Extracted from ``GraphExecutor`` (god-object decomposition, 2026-06-20). This is
the durable-DAG-wraps-``agent_loop`` core: given a ready ``TaskStep``, it runs the
Operator sub-agent through the agent loop (with full tool discovery, prior-step
context injection, and per-run trace accumulation), falling back to a minimal
single-turn Claude call when the agent-loop dependencies are not wired.

It depends *downward* on ``StepGraphStore`` (to read sibling step outputs) and
``SurfaceEmitter`` (the ``tool_call_started`` event); it never imports
``graph_executor``. The ``db_factory`` is resolved live via a provider so the
coordinator stays the single source of truth (tests reassign ``_db_factory``),
and the per-run trace is read through ``active_traces_provider`` for the same
reason — the coordinator owns the ``_active_traces`` dict lifecycle.
"""

from __future__ import annotations

import json
import logging

from src.config.settings import Settings
from src.llm_utils import parse_llm_json
from src.models.task_graph import TaskRun, TaskStep
from src.services.execution_surface_emitter import SurfaceEmitter
from src.services.step_graph_store import StepGraphStore

logger = logging.getLogger(__name__)


class StepRunner:
    """Runs one step via the Operator agent loop (or a minimal Claude fallback)."""

    def __init__(
        self,
        *,
        settings: Settings,
        client,
        store: StepGraphStore,
        emitter: SurfaceEmitter,
        db_factory_provider,
        active_traces_provider,
        tool_registry=None,
        context_builder=None,
        execute_tool_fn=None,
        budget=None,
        circuit_breaker=None,
    ):
        self._settings = settings
        self._client = client
        self._store = store
        self._emitter = emitter
        self._db_factory_provider = db_factory_provider
        self._active_traces_provider = active_traces_provider
        self._tool_registry = tool_registry
        self._context_builder = context_builder
        self._execute_tool_fn = execute_tool_fn
        self._budget = budget
        self._circuit_breaker = circuit_breaker

    @property
    def _db_factory(self):
        """Resolve the current DB session factory live via the provider."""
        return self._db_factory_provider()

    @property
    def _active_traces(self):
        """Resolve the coordinator's live per-run trace map via the provider."""
        return self._active_traces_provider()

    async def run_step_action(
        self,
        step: TaskStep,
        run: TaskRun,
        cancel_event=None,
    ) -> dict:
        """Execute the actual action for a step.

        Routes to agent loop if dependencies are available, otherwise uses
        a minimal single-turn Claude fallback.
        """
        input_data = step.input_data or {}
        task_type = input_data.get("capability", input_data.get("task_type", "unknown"))

        await self._emitter.emit_event(
            "tool_call_started",
            run.user_id,
            {"run_id": run.run_id, "step_id": step.step_id, "tool_name": task_type},
            workspace_id=run.workspace_id,
        )

        # Check if agent loop dependencies are available
        if self._db_factory and self._execute_tool_fn and self._budget:
            return await self.run_step_via_agent_loop(step, run, cancel_event=cancel_event)

        # Fallback: minimal single-turn Claude call
        return await self.minimal_claude_action(step, run)

    async def minimal_claude_action(self, step: TaskStep, run: TaskRun) -> dict:
        """Minimal single-turn Claude action without tool discovery.

        Used as fallback when agent loop dependencies are not available.
        """
        input_data = step.input_data or {}
        task_type = input_data.get("capability", input_data.get("task_type", "unknown"))
        context_prompt = await self.build_step_context(run, step)

        goal = input_data.get("goal", input_data.get("context", ""))
        parts = [f"Task type: {task_type}"]
        if goal:
            parts.append(f"Goal: {goal}")
        for key, value in input_data.items():
            if key not in ("task_type", "goal", "context"):
                parts.append(f"{key}: {value}")
        if context_prompt:
            parts.append(f"\n--- Background ---\n{context_prompt}")

        system = (
            f"You are Jarvis's task execution engine handling a '{task_type}' step. "
            "Complete the task described below. "
            'Respond with JSON: {"status": "completed", "result": "...", "details": {...}}'
        )

        response = await self._client.messages.create(
            model=self._settings.resolved_model,
            max_tokens=1024,
            system=system,
            messages=[{"role": "user", "content": "\n".join(parts)}],
        )

        try:
            return parse_llm_json(response.content[0].text)
        except json.JSONDecodeError:
            return {"status": "completed", "result": response.content[0].text}

    async def build_operator_tools(self) -> list[dict]:
        """Build Claude API tool definitions filtered by Operator's capability scope."""
        if not self._tool_registry:
            return []

        from src.orchestrator.agents import AGENTS
        from src.tools.schemas import TOOL_INPUT_MODELS

        operator = AGENTS.get("operator")
        if not operator:
            return []

        scope = operator.capability_scope
        tools = []
        seen = set()

        # Internal tools from TOOL_INPUT_MODELS
        for tool_name, model_cls in TOOL_INPUT_MODELS.items():
            tool_def = await self._tool_registry.get_tool(tool_name)
            if tool_def and tool_def.capability and tool_def.capability in scope:
                schema = model_cls.model_json_schema()
                tools.append(
                    {
                        "name": tool_name,
                        "description": (
                            model_cls.__doc__.strip() if model_cls.__doc__ else tool_name
                        ),
                        "input_schema": schema,
                    }
                )
                seen.add(tool_name)

        # External tools from registry
        try:
            all_tools = await self._tool_registry.list_tools(enabled_only=True)
            for tool_def in all_tools:
                if (
                    tool_def.name not in seen
                    and tool_def.capability
                    and tool_def.capability in scope
                ):
                    tools.append(
                        {
                            "name": tool_def.name,
                            "description": tool_def.description or tool_def.name,
                            "input_schema": tool_def.input_schema or {"type": "object"},
                        }
                    )
                    seen.add(tool_def.name)
        except Exception:
            logger.debug("Failed to list external tools", exc_info=True)

        return tools

    async def run_step_via_agent_loop(
        self,
        step: TaskStep,
        run: TaskRun,
        cancel_event=None,
    ) -> dict:
        """Execute a step via the Operator agent loop with full tool discovery."""
        from src.orchestrator.agent_loop import (
            LoopDone,
            LoopError,
            LoopToolCall,
            LoopToolResult,
            agent_loop,
        )
        from src.orchestrator.agents import AGENTS

        input_data = step.input_data or {}
        task_type = input_data.get("capability", input_data.get("task_type", "unknown"))
        goal = input_data.get("goal", input_data.get("context", ""))

        # Build message from step input
        message_parts = [f"Task type: {task_type}"]
        if goal:
            message_parts.append(f"Goal: {goal}")
        for key, value in input_data.items():
            if key not in ("task_type", "goal", "context"):
                message_parts.append(f"{key}: {value}")

        message = "\n".join(message_parts)

        # Inject completed predecessor step outputs so the operator sees
        # what earlier agents (e.g. Perceiver) read or produced.
        all_steps = await self._store.get_all_steps(run.run_id)
        prior_parts: list[str] = []
        for s in all_steps:
            if s.step_id == step.step_id:
                continue
            if s.status != "completed" or not s.output_data:
                continue
            result_text = s.output_data.get("result", "")
            if not result_text:
                continue
            cap = (s.input_data or {}).get("capability", "unknown")
            desc = (s.input_data or {}).get("goal", cap)
            prior_parts.append(f"[{desc}]:\n{str(result_text)}")
        if prior_parts:
            message += (
                "\n\n--- Prior step results ---\n"
                + "\n\n".join(prior_parts)
                + "\n--- End of prior step results ---\n"
            )

        # Get context
        context_prompt = await self.build_step_context(run, step)

        # Resolve operator agent
        operator = AGENTS.get("operator")
        if not operator:
            return {
                "status": "completed",
                "result": "Operator agent not found",
                "errors": ["Operator agent not configured"],
            }

        # Build system blocks
        system_blocks = [{"type": "text", "text": operator.prompt}]
        if context_prompt:
            system_blocks.append({"type": "text", "text": f"\n--- Context ---\n{context_prompt}"})

        # Build tools list
        tools = await self.build_operator_tools()

        # Collect events from agent loop
        text = ""
        tools_called = []
        errors = []
        # A tool that hit a permanent OAuth failure returns the structured
        # auth_required envelope as its LoopToolResult. Capture it so the caller
        # (DagRunner) can defer the run for re-authorization instead of failing.
        auth_required: dict | None = None

        async for event in agent_loop(
            client=self._client,
            agent=operator,
            model=self._settings.resolved_model,
            system_blocks=system_blocks,
            tools=tools,
            message=message,
            user_id=run.user_id,
            workspace_id=run.workspace_id or "",
            db_factory=self._db_factory,
            services=None,
            budget=self._budget,
            trace=self._active_traces.get(run.run_id),
            execute_tool_fn=self._execute_tool_fn,
            max_tool_rounds=10,
            stream=False,
            circuit_breaker=self._circuit_breaker,
            run_id=run.run_id,
            cancel_event=cancel_event,
        ):
            if isinstance(event, LoopDone):
                text = event.text
                tools_called = event.tools_called
            elif isinstance(event, LoopError):
                errors.append(event.message)
            elif isinstance(event, LoopToolResult):
                result = event.result
                if (
                    auth_required is None
                    and isinstance(result, dict)
                    and result.get("error_code") == "auth_required"
                ):
                    auth_required = result
            elif isinstance(event, LoopToolCall):
                pass  # Already tracked in LoopDone.tools_called

        output: dict = {
            "status": "completed",
            "result": text,
            "tools_called": tools_called,
            "errors": errors,
        }
        if auth_required is not None:
            # Surfaced so DagRunner._defer_for_reauth parks the run for re-auth.
            output["status"] = "error"
            output["error_code"] = "auth_required"
            output["provider"] = auth_required.get("provider", "")
            output["server"] = auth_required.get("server", "")
            output["auth_required"] = auth_required
        return output

    async def build_step_context(self, run: TaskRun, step: TaskStep) -> str:
        """Build context prompt for a step using ContextBuilder."""
        if not self._context_builder:
            return ""
        try:
            input_data = step.input_data or {}
            query = input_data.get("goal", input_data.get("context", ""))
            task_type = input_data.get("task_type")
            pack = await self._context_builder.build(
                user_id=run.user_id,
                query=query or "",
                task_type=task_type,
            )
            from src.services.context_builder import ContextBuilder

            return ContextBuilder.to_prompt(pack)
        except Exception:
            logger.debug("ContextBuilder failed for step %s", step.step_id, exc_info=True)
            return ""
