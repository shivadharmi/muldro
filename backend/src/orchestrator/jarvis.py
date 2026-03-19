"""JarvisOrchestrator — the consciousness of Jarvis.

Routes user messages and system events to the right sub-agents,
manages traces, enforces budgets, and coordinates the intelligence loop.
This is the main entry point for all Jarvis interactions.
"""

import json
import logging
import time
from collections.abc import AsyncGenerator
from typing import Any

import anthropic
from ulid import ULID

from src.config.settings import Settings, get_anthropic_client
from src.models.task_graph import TaskRun, TaskStep
from src.orchestrator.agents import AGENTS, SubAgent
from src.orchestrator.budget import BudgetTracker
from src.orchestrator.contracts import AgentEnvelope, AgentResult, SpanToolCall
from src.orchestrator.hooks import (
    audit_post_tool_hook,
    governor_pre_tool_hook,
)
from src.orchestrator.prompts import JARVIS_SOUL
from src.orchestrator.services import ServiceContainer
from src.orchestrator.tracing import TraceManager
from src.services.agent_registry import AgentRegistry
from src.services.context_builder import ContextBuilder, ContextPack
from src.services.route_resolver import RouteResolver
from src.services.trace_store import TraceStore

logger = logging.getLogger(__name__)

# Event types published to the agent events stream
AGENT_EVENT_TYPES = {
    "plan_generated",
    "research_started",
    "research_completed",
    "approval_requested",
    "execution_started",
    "execution_completed",
    "memory_updated",
    "entity_created",
    "briefing_generated",
    "perception_completed",
}

# Model IDs for each tier (direct API)
MODEL_TIERS = {
    "opus": "claude-opus-4-20250514",
    "sonnet": "claude-sonnet-4-20250514",
    "haiku": "claude-haiku-4-20250514",
}

# Bedrock inference profile IDs (cross-region, works in ap-south-1)
BEDROCK_MODEL_TIERS = {
    "opus": "global.anthropic.claude-opus-4-5-20251101-v1:0",
    "sonnet": "apac.anthropic.claude-sonnet-4-20250514-v1:0",
    "haiku": "global.anthropic.claude-haiku-4-5-20251001-v1:0",
}

# Agents that benefit from context enrichment (read-heavy agents)
CONTEXT_ENRICHED_AGENTS = {"planner", "presenter", "researcher", "librarian"}


class JarvisOrchestrator:
    """The Jarvis brain — orchestrates sub-agents via Claude API.

    This is NOT a ClaudeSDKClient wrapper (SDK not yet stable enough).
    Instead, we use the Anthropic API directly with structured prompts
    to simulate sub-agent routing. Each sub-agent call is a separate
    Claude API call with the agent's specific prompt and tool scope.
    """

    def __init__(
        self,
        settings: Settings,
        db_factory,
        services: dict | ServiceContainer,
    ):
        self._settings = settings
        self._db_factory = db_factory
        # Accept both dict (legacy callers / tests) and ServiceContainer
        if isinstance(services, ServiceContainer):
            self._services = services
        else:
            self._services = ServiceContainer.from_dict(services)
        self._client = get_anthropic_client(settings)
        self._trace_store = TraceStore(
            elasticsearch_url=settings.elasticsearch_url,
            db_factory=db_factory,
        )
        self._trace_manager = TraceManager(trace_store=self._trace_store)
        self._budget = BudgetTracker(daily_limit_usd=settings.daily_token_budget_usd)
        self._tools = self._build_tool_definitions()
        self._agents: dict[str, SubAgent] = dict(AGENTS)  # Start with hardcoded defaults
        self._event_bus = None  # Lazy-init when Redis available

    async def load_agents_from_db(self) -> None:
        """Load agent definitions from the database, replacing hardcoded defaults."""
        try:
            async with self._db_factory() as db:
                registry = AgentRegistry(db)
                db_agents = await registry.load_as_sub_agents()
                if db_agents:
                    self._agents = db_agents
                    logger.info(
                        "Loaded %d agents from DB: %s",
                        len(db_agents),
                        sorted(db_agents.keys()),
                    )
        except Exception:
            logger.debug("Agent DB load failed, using hardcoded defaults", exc_info=True)

    async def _create_lightweight_run(
        self,
        user_id: str,
        workspace_id: str,
        decision: dict,
        trace_id: str,
        conversation_id: str | None = None,
    ) -> str | None:
        """Create a lightweight TaskRun for every user interaction.

        Even simple decisions (acknowledge, answer_directly) get a single-step
        run so ALL interactions are tracked in the runs table.
        Returns the run_id on success, None if DB unavailable.
        """
        run_id = f"run_{ULID()}"
        decision_type = decision.get("decision", "acknowledge")

        try:
            async with self._db_factory() as db:
                run = TaskRun(
                    run_id=run_id,
                    user_id=user_id,
                    workspace_id=workspace_id,
                    plan_id=None,
                    status="running",
                    source="user_message",
                    execution_mode="auto_execute",
                    policy_decision={"decision": decision_type},
                    conversation_id=conversation_id,
                    trace_id=trace_id,
                )
                db.add(run)

                step = TaskStep(
                    step_id=f"step_{ULID()}",
                    run_id=run_id,
                    workspace_id=workspace_id,
                    task_id=f"task_{ULID()}",
                    plan_task_id=None,
                    step_type=decision_type,
                    status="running",
                    input_data={"decision": decision},
                )
                db.add(step)
                await db.commit()
        except Exception:
            logger.debug("Failed to create lightweight run", exc_info=True)
            return None

        return run_id

    async def _complete_lightweight_run(
        self,
        run_id: str,
        result: dict,
        success: bool = True,
    ) -> None:
        """Mark a lightweight run and its step as completed or failed."""
        try:
            async with self._db_factory() as db:
                from sqlalchemy import select

                res = await db.execute(select(TaskRun).where(TaskRun.run_id == run_id))
                run = res.scalar_one_or_none()
                if not run:
                    return

                run.status = "completed" if success else "failed"
                if not success:
                    run.error = {"message": result.get("summary", "unknown error")[:500]}

                step_res = await db.execute(select(TaskStep).where(TaskStep.run_id == run_id))
                for step in step_res.scalars().all():
                    step.status = "completed" if success else "failed"
                    if success:
                        step.output_data = {
                            "decision": result.get("decision"),
                            "summary": str(result.get("summary", ""))[:1000],
                        }

                await db.commit()
        except Exception:
            logger.debug("Failed to complete lightweight run %s", run_id, exc_info=True)

    def _build_tool_definitions(self) -> list[dict]:
        """Build Claude tool definitions from intelligence + MCP tools."""
        # Internal tools + any discovered MCP tools from external servers
        tools = self._build_internal_tool_definitions()

        # Append MCP tools discovered from external servers
        from src.connectors.mcp_bridge import list_mcp_tools

        for mcp_tool in list_mcp_tools():
            schema = mcp_tool.get("input_schema", {})
            tools.append(
                {
                    "name": mcp_tool["name"],
                    "description": mcp_tool.get("description", "External MCP tool"),
                    "input_schema": schema if schema else {"type": "object", "properties": {}},
                }
            )

        return tools

    def _build_internal_tool_definitions(self) -> list[dict]:
        """Build Claude tool definitions for internal intelligence tools."""
        return [
            {
                "name": "ingest_event",
                "description": "Ingest an event into the Jarvis intelligence pipeline.",
                "input_schema": {
                    "type": "object",
                    "properties": {
                        "source": {"type": "string"},
                        "event_type": {"type": "string"},
                        "entity_type": {"type": "string"},
                        "entity_id": {"type": "string"},
                        "title": {"type": "string"},
                        "summary": {"type": "string", "default": ""},
                        "actor_email": {"type": "string", "default": ""},
                        "actor_name": {"type": "string", "default": ""},
                        "occurred_at": {"type": "string", "default": ""},
                    },
                    "required": ["source", "event_type", "entity_type", "entity_id", "title"],
                },
            },
            {
                "name": "search_memory",
                "description": "Search Jarvis knowledge: memories, entities, events.",
                "input_schema": {
                    "type": "object",
                    "properties": {
                        "query": {"type": "string"},
                        "scope": {"type": "string", "default": "all"},
                        "limit": {"type": "integer", "default": 10},
                    },
                    "required": ["query"],
                },
            },
            {
                "name": "get_entities",
                "description": "Get entities from the world model.",
                "input_schema": {
                    "type": "object",
                    "properties": {
                        "query": {"type": "string", "default": ""},
                        "entity_type": {"type": "string", "default": ""},
                        "limit": {"type": "integer", "default": 20},
                    },
                },
            },
            {
                "name": "plan_command",
                "description": "Process a command through the Jarvis planner.",
                "input_schema": {
                    "type": "object",
                    "properties": {
                        "command": {"type": "string"},
                        "context": {"type": "string", "default": ""},
                    },
                    "required": ["command"],
                },
            },
            {
                "name": "evaluate_policy",
                "description": "Evaluate governance policy for a plan.",
                "input_schema": {
                    "type": "object",
                    "properties": {"plan_id": {"type": "string"}},
                    "required": ["plan_id"],
                },
            },
            {
                "name": "get_briefing",
                "description": "Generate or fetch the daily briefing.",
                "input_schema": {
                    "type": "object",
                    "properties": {"date": {"type": "string", "default": "today"}},
                },
            },
            {
                "name": "get_observation_cursor",
                "description": "Get the last observation checkpoint for a source.",
                "input_schema": {
                    "type": "object",
                    "properties": {"source": {"type": "string"}},
                    "required": ["source"],
                },
            },
            {
                "name": "update_observation_cursor",
                "description": "Update observation checkpoint after successful observation.",
                "input_schema": {
                    "type": "object",
                    "properties": {
                        "source": {"type": "string"},
                        "cursor_type": {"type": "string"},
                        "cursor_value": {"type": "string"},
                    },
                    "required": ["source", "cursor_type", "cursor_value"],
                },
            },
            {
                "name": "report_observation",
                "description": "Report observation cycle results for health tracking.",
                "input_schema": {
                    "type": "object",
                    "properties": {
                        "source": {"type": "string"},
                        "items_found": {"type": "integer", "default": 0},
                        "items_ingested": {"type": "integer", "default": 0},
                        "status": {"type": "string", "default": "ok"},
                        "error_message": {"type": "string", "default": ""},
                    },
                    "required": ["source"],
                },
            },
            {
                "name": "approve_action",
                "description": "Approve or reject a pending action.",
                "input_schema": {
                    "type": "object",
                    "properties": {
                        "approval_id": {"type": "string"},
                        "decision": {"type": "string", "enum": ["approved", "rejected"]},
                        "reason": {"type": "string", "default": ""},
                    },
                    "required": ["approval_id", "decision"],
                },
            },
            {
                "name": "update_execution",
                "description": "Update the status of an execution.",
                "input_schema": {
                    "type": "object",
                    "properties": {
                        "execution_id": {"type": "string"},
                        "status": {"type": "string"},
                        "result_summary": {"type": "string", "default": ""},
                        "error_message": {"type": "string", "default": ""},
                    },
                    "required": ["execution_id", "status"],
                },
            },
            {
                "name": "update_entity",
                "description": "Update an entity's attributes or add an alias.",
                "input_schema": {
                    "type": "object",
                    "properties": {
                        "entity_id": {"type": "string"},
                        "attributes": {"type": "string", "default": ""},
                        "add_alias": {"type": "string", "default": ""},
                    },
                    "required": ["entity_id"],
                },
            },
            {
                "name": "get_active_plans",
                "description": "Get currently active plans.",
                "input_schema": {
                    "type": "object",
                    "properties": {"limit": {"type": "integer", "default": 10}},
                },
            },
            {
                "name": "extract_preferences",
                "description": "Extract and store user preferences from interaction text.",
                "input_schema": {
                    "type": "object",
                    "properties": {
                        "source_text": {"type": "string"},
                    },
                    "required": ["source_text"],
                },
            },
            {
                "name": "create_task",
                "description": "Create a standalone task in the task system.",
                "input_schema": {
                    "type": "object",
                    "properties": {
                        "title": {"type": "string"},
                        "description": {"type": "string", "default": ""},
                        "task_type": {"type": "string", "default": "general"},
                        "priority": {"type": "string", "default": "medium"},
                        "goal_id": {"type": "string", "default": ""},
                    },
                    "required": ["title"],
                },
            },
            {
                "name": "get_task",
                "description": "Get details of a task by ID.",
                "input_schema": {
                    "type": "object",
                    "properties": {"task_id": {"type": "string"}},
                    "required": ["task_id"],
                },
            },
            {
                "name": "get_goals",
                "description": "Get user goals, optionally filtered by status.",
                "input_schema": {
                    "type": "object",
                    "properties": {
                        "status": {"type": "string", "default": "active"},
                        "limit": {"type": "integer", "default": 10},
                    },
                },
            },
            {
                "name": "build_context",
                "description": "Build a rich context pack for a query/task.",
                "input_schema": {
                    "type": "object",
                    "properties": {
                        "query": {"type": "string"},
                        "task_type": {"type": "string", "default": ""},
                    },
                    "required": ["query"],
                },
            },
            {
                "name": "verify_run",
                "description": "Verify a completed run against success conditions.",
                "input_schema": {
                    "type": "object",
                    "properties": {"run_id": {"type": "string"}},
                    "required": ["run_id"],
                },
            },
        ]

    def _get_tools_for_agent(self, agent: SubAgent) -> list[dict]:
        """Filter tool definitions to only those the agent can use."""
        return [t for t in self._tools if agent.can_use_tool(t["name"])]

    def _get_model_for_agent(self, agent: SubAgent) -> str:
        """Get the Claude model ID for an agent's tier."""
        if self._settings.use_bedrock:
            return BEDROCK_MODEL_TIERS.get(agent.model_tier, BEDROCK_MODEL_TIERS["sonnet"])
        return MODEL_TIERS.get(agent.model_tier, MODEL_TIERS["sonnet"])

    async def process_message(
        self,
        message: str,
        user_id: str,
        workspace_id: str,
        conversation_id: str | None = None,
        surface: str = "api",
        context: dict | None = None,
    ) -> dict:
        """Process a user message through the orchestrator.

        This is the main entry point for user interactions.
        The orchestrator decides which sub-agents to invoke.
        """
        trace = self._trace_manager.start_trace("user_message")
        run_id: str | None = None

        try:
            # Load conversation history for multi-turn context
            history_block = await self._load_conversation_history(conversation_id)

            # Step 1: Route to Planner for intent determination
            planner_message = f"User message: {message}\n\nContext: {json.dumps(context or {})}"
            if history_block:
                planner_message = f"{history_block}\n\n{planner_message}"

            plan_result = await self._call_agent(
                "planner",
                message=planner_message,
                user_id=user_id,
                trace=trace,
                workspace_id=workspace_id,
            )

            decision = self._extract_decision(plan_result)
            decision_json = json.dumps(decision)

            # Create a lightweight TaskRun for tracking
            run_id = await self._create_lightweight_run(
                user_id=user_id,
                workspace_id=workspace_id,
                decision=decision,
                trace_id=trace.trace_id,
                conversation_id=conversation_id,
            )

            # Step 2: Resolve route dynamically from DB
            result = {
                "trace_id": trace.trace_id,
                "run_id": run_id,
                "decision": decision.get("decision", "acknowledge"),
                "summary": decision.get("reasoning", plan_result),
            }

            # Publish plan event
            await self._publish_event(
                "plan_generated",
                user_id,
                {"decision": decision, "trace_id": trace.trace_id},
                trace_id=trace.trace_id,
            )

            # Resolve agent pipeline from routes
            pipeline = await self._resolve_pipeline(decision)

            for step in pipeline:
                agent_name = step.get("agent", "")
                if not agent_name or agent_name not in self._agents:
                    continue

                # Check step-level condition
                step_cond = step.get("condition")
                if step_cond and not self._check_step_condition(step_cond, decision):
                    continue

                # Handle special actions
                action = step.get("action")
                if action == "execute_plan":
                    plan_id = decision.get("plan_id")
                    if plan_id:
                        exec_result = await self._execute_plan_via_graph(plan_id, user_id, trace)
                        result["execution"] = exec_result
                    continue

                # Format message from template
                template = step.get("message_template", "Process this: {decision_json}")
                agent_message = template.format(
                    decision_json=decision_json, surface=surface, message=message
                )

                agent_result = await self._call_agent(
                    agent_name,
                    message=agent_message,
                    user_id=user_id,
                    trace=trace,
                    workspace_id=workspace_id,
                )
                result[agent_name] = agent_result

            # Step 3: Presenter formats the response (if not already in pipeline)
            if not any(s.get("agent") == "presenter" for s in pipeline):
                presenter_msg = f"Format this for the user ({surface}): {decision_json}"
                if history_block:
                    presenter_msg = f"{history_block}\n\n{presenter_msg}"
                present_result = await self._call_agent(
                    "presenter",
                    message=presenter_msg,
                    user_id=user_id,
                    trace=trace,
                    workspace_id=workspace_id,
                )
                result["presentation"] = present_result

            # Step 4: Persona learns from this interaction (fire-and-forget)
            try:
                await self._call_agent(
                    "persona",
                    message=f"Observe this user interaction on {surface}:\n"
                    f"User said: {message}\n"
                    f"Decision: {decision.get('decision', 'unknown')}\n"
                    f"Extract any preference signals.",
                    user_id=user_id,
                    trace=trace,
                    workspace_id=workspace_id,
                )
            except Exception:
                logger.debug("Persona reflection skipped", exc_info=True)

            # Complete the lightweight run
            await self._complete_lightweight_run(run_id, result, success=True)

            return result

        except Exception as e:
            logger.error("process_message failed: %s", e, exc_info=True)
            error_result = {
                "trace_id": trace.trace_id,
                "decision": "error",
                "summary": f"Error processing message: {e}",
            }
            if run_id:
                await self._complete_lightweight_run(run_id, error_result, success=False)
            return error_result
        finally:
            await self._trace_manager.finish_trace(
                trace.trace_id, user_id=user_id, workspace_id=workspace_id
            )

    async def process_message_stream(
        self,
        message: str,
        user_id: str,
        workspace_id: str,
        surface: str = "web",
        context: dict | None = None,
        conversation_id: str | None = None,
    ) -> AsyncGenerator[dict[str, Any], None]:
        """Stream events while processing a user message through the orchestrator.

        Yields SSE-compatible dicts with event types:
          agent_start, thinking, tool_call, tool_result, agent_done,
          response, error, done
        """
        trace = self._trace_manager.start_trace("user_message")
        run_id: str | None = None

        try:
            yield {"event": "trace", "trace_id": trace.trace_id}

            # Load conversation history for multi-turn context
            history_block = await self._load_conversation_history(conversation_id)

            # Step 1: Planner determines intent
            planner_message = f"User message: {message}\n\nContext: {json.dumps(context or {})}"
            if history_block:
                planner_message = f"{history_block}\n\n{planner_message}"

            plan_text = ""
            async for evt in self._call_agent_stream(
                "planner",
                message=planner_message,
                user_id=user_id,
                trace=trace,
                workspace_id=workspace_id,
            ):
                yield evt
                if evt.get("event") == "agent_done":
                    plan_text = evt.get("text", "")

            decision = self._extract_decision(plan_text)
            decision_json = json.dumps(decision)

            # Create a lightweight TaskRun for tracking
            run_id = await self._create_lightweight_run(
                user_id=user_id,
                workspace_id=workspace_id,
                decision=decision,
                trace_id=trace.trace_id,
                conversation_id=conversation_id,
            )

            yield {"event": "decision", "decision": decision, "run_id": run_id}

            # Step 2: Resolve route dynamically from DB
            pipeline = await self._resolve_pipeline(decision)

            for step in pipeline:
                agent_name = step.get("agent", "")
                if not agent_name or agent_name not in self._agents:
                    continue

                # Check step-level condition
                step_cond = step.get("condition")
                if step_cond and not self._check_step_condition(step_cond, decision):
                    continue

                # Handle special actions
                action = step.get("action")
                if action == "execute_plan":
                    plan_id = decision.get("plan_id")
                    if plan_id:
                        yield {"event": "execution_start", "plan_id": plan_id}
                        exec_result = await self._execute_plan_via_graph(plan_id, user_id, trace)
                        yield {
                            "event": "execution_result",
                            "run_id": exec_result.get("run_id"),
                            "status": exec_result.get("status"),
                        }
                    continue

                # Format message from template
                template = step.get("message_template", "Process this: {decision_json}")
                agent_message = template.format(
                    decision_json=decision_json, surface=surface, message=message
                )

                async for evt in self._call_agent_stream(
                    agent_name,
                    message=agent_message,
                    user_id=user_id,
                    trace=trace,
                    workspace_id=workspace_id,
                ):
                    yield evt

            # Always route through presenter for user-facing response
            presenter_msg = (
                f"Format this for the user ({surface}). Be conversational and helpful.\n\n"
                f"Original user message: {message}\n"
                f"Planner decision: {decision_json}\n"
                f"Planner analysis: {plan_text[:2000]}"
            )
            if history_block:
                presenter_msg = f"{history_block}\n\n{presenter_msg}"

            presenter_text = ""
            async for evt in self._call_agent_stream(
                "presenter",
                message=presenter_msg,
                user_id=user_id,
                trace=trace,
                workspace_id=workspace_id,
            ):
                yield evt
                if evt.get("event") == "agent_done":
                    presenter_text = evt.get("text", "")
                    yield {"event": "response", "text": presenter_text}

            # Fire-and-forget persona learning (no streaming needed)
            try:
                await self._call_agent(
                    "persona",
                    message=f"Observe this user interaction on {surface}:\n"
                    f"User said: {message}\n"
                    f"Decision: {decision.get('decision', 'unknown')}\n"
                    f"Extract any preference signals.",
                    user_id=user_id,
                    trace=trace,
                    workspace_id=workspace_id,
                )
            except Exception:
                pass

            # Complete the lightweight run
            if run_id:
                await self._complete_lightweight_run(
                    run_id,
                    {"decision": decision.get("decision"), "summary": presenter_text},
                    success=True,
                )

            yield {"event": "done", "trace_id": trace.trace_id, "run_id": run_id}

        except Exception as e:
            logger.error("process_message_stream failed: %s", e, exc_info=True)
            if run_id:
                await self._complete_lightweight_run(run_id, {"summary": str(e)}, success=False)
            yield {"event": "error", "message": str(e)}
        finally:
            await self._trace_manager.finish_trace(
                trace.trace_id, user_id=user_id, workspace_id=workspace_id
            )

    async def _call_agent_stream(
        self,
        agent_name: str,
        message: str,
        user_id: str,
        trace=None,
        max_tool_rounds: int = 10,
        workspace_id: str = "",
    ) -> AsyncGenerator[dict[str, Any], None]:
        """Call a sub-agent, yielding events as it thinks, calls tools, etc."""
        agent = self._agents.get(agent_name)
        if not agent:
            yield {"event": "error", "message": f"Unknown agent: {agent_name}"}
            return

        model = self._get_model_for_agent(agent)
        tools = self._apply_cache_control_to_tools(self._get_tools_for_agent(agent))
        span = trace.start_span(agent_name) if trace else None

        yield {"event": "agent_start", "agent": agent_name, "model": model}

        # Assemble context (memories + entities) for enriched agents
        context_block = await self._assemble_context(agent_name, message, user_id=user_id)
        system_blocks = self._build_system_prompt(agent, context_block)

        messages = [{"role": "user", "content": message}]

        total_input = 0
        total_output = 0
        total_cache_creation = 0
        total_cache_read = 0
        tools_called: list[str] = []
        tool_call_details: list[SpanToolCall] = []
        thinking_chunks: list[str] = []
        text = ""
        start_time = time.time()

        # Extended thinking — all Claude 4+ models support it
        thinking_budget = min(8192, max(1024, agent.max_tokens // 2))
        if thinking_budget >= agent.max_tokens:
            thinking_budget = agent.max_tokens - 1

        try:
            for _round in range(max_tool_rounds):
                api_kwargs = {
                    "model": model,
                    "max_tokens": agent.max_tokens,
                    "temperature": 1,  # required when thinking is enabled
                    "thinking": {
                        "type": "enabled",
                        "budget_tokens": thinking_budget,
                    },
                    "system": system_blocks,
                    "messages": messages,
                }
                if tools:
                    api_kwargs["tools"] = tools

                # Stream for real token-by-token output
                response = None
                try:
                    async with self._client.messages.stream(**api_kwargs) as stream:
                        async for event in stream:
                            if event.type == "content_block_delta":
                                delta = event.delta
                                if delta.type == "thinking_delta":
                                    thinking_chunks.append(delta.thinking)
                                    yield {
                                        "event": "thinking",
                                        "agent": agent_name,
                                        "text": delta.thinking,
                                        "is_thinking": True,
                                    }
                                elif delta.type == "text_delta":
                                    yield {
                                        "event": "text_delta",
                                        "agent": agent_name,
                                        "text": delta.text,
                                    }
                        response = await stream.get_final_message()
                except Exception as stream_err:
                    if response is None:
                        # Fallback to non-streaming if stream() fails
                        logger.warning(
                            "Streaming failed for %s, falling back: %s",
                            agent_name,
                            stream_err,
                        )
                        api_kwargs["temperature"] = agent.temperature
                        api_kwargs.pop("thinking", None)
                        response = await self._client.messages.create(**api_kwargs)

                total_input += response.usage.input_tokens
                total_output += response.usage.output_tokens
                total_cache_creation += (
                    getattr(response.usage, "cache_creation_input_tokens", 0) or 0
                )
                total_cache_read += getattr(response.usage, "cache_read_input_tokens", 0) or 0

                # Capture thinking text from final message for persistence
                for block in response.content:
                    if block.type == "thinking" and hasattr(block, "thinking"):
                        thinking_chunks.append(block.thinking)

                text_blocks = [b for b in response.content if b.type == "text"]
                tool_use_blocks = [b for b in response.content if b.type == "tool_use"]

                # Emit text blocks as intermediate reasoning (non-streamed fallback)
                for tb in text_blocks:
                    if tb.text.strip():
                        yield {
                            "event": "thinking",
                            "agent": agent_name,
                            "text": tb.text,
                        }

                if not tool_use_blocks:
                    text = "".join(b.text for b in text_blocks)
                    break

                # Process tool calls
                tool_results = []
                for tool_block in tool_use_blocks:
                    tool_name = tool_block.name
                    tool_input = tool_block.input
                    tools_called.append(tool_name)

                    yield {
                        "event": "tool_call",
                        "agent": agent_name,
                        "tool": tool_name,
                        "input": tool_input,
                    }

                    # Governor pre-hook
                    pre_result = await governor_pre_tool_hook(
                        tool_name,
                        tool_input,
                        agent_name,
                        user_id=user_id,
                        db_factory=self._db_factory,
                        services=self._services,
                    )

                    if not pre_result.get("allowed", True):
                        blocked_msg = {
                            "error": pre_result.get("reason", "Blocked by policy"),
                            "approval_required": pre_result.get("approval_required", False),
                            "approval_id": pre_result.get("approval_id"),
                        }
                        tool_results.append(
                            {
                                "type": "tool_result",
                                "tool_use_id": tool_block.id,
                                "content": json.dumps(blocked_msg),
                            }
                        )
                        tool_call_details.append(
                            SpanToolCall(
                                tool_name=tool_name,
                                input_data=tool_input if isinstance(tool_input, dict) else {},
                                output_data=blocked_msg,
                                status="blocked",
                                error=pre_result.get("reason", "Blocked by policy"),
                            )
                        )
                        yield {
                            "event": "tool_result",
                            "agent": agent_name,
                            "tool": tool_name,
                            "result": blocked_msg,
                            "blocked": True,
                        }
                        continue

                    tool_start = time.time()
                    result = await self._execute_tool(tool_name, tool_input, user_id=user_id)
                    tool_latency = int((time.time() - tool_start) * 1000)

                    tool_results.append(
                        {
                            "type": "tool_result",
                            "tool_use_id": tool_block.id,
                            "content": json.dumps(result)
                            if isinstance(result, dict)
                            else str(result),
                        }
                    )

                    # Truncate large results for persistence (keep first 2000 chars)
                    persisted_output = result
                    if isinstance(result, str) and len(result) > 2000:
                        persisted_output = result[:2000] + "...[truncated]"
                    elif isinstance(result, dict):
                        result_str = json.dumps(result, default=str)
                        if len(result_str) > 2000:
                            persisted_output = {"_truncated": result_str[:2000]}

                    tool_call_details.append(
                        SpanToolCall(
                            tool_name=tool_name,
                            input_data=tool_input if isinstance(tool_input, dict) else {},
                            output_data=persisted_output,
                            status="success",
                            duration_ms=tool_latency,
                        )
                    )

                    yield {
                        "event": "tool_result",
                        "agent": agent_name,
                        "tool": tool_name,
                        "result": result,
                        "latency_ms": tool_latency,
                    }

                    await audit_post_tool_hook(
                        tool_name,
                        tool_input,
                        result,
                        agent_name,
                        trace_id=trace.trace_id if trace else None,
                        span_id=span.span_id if span else None,
                        latency_ms=tool_latency,
                        db_factory=self._db_factory,
                    )

                # Preserve thinking blocks for multi-turn tool-use continuity
                messages.append({"role": "assistant", "content": response.content})
                messages.append({"role": "user", "content": tool_results})
            else:
                text = f"[Agent {agent_name} hit max tool rounds ({max_tool_rounds})]"

        except anthropic.APIError as e:
            logger.error("Claude API error in %s: %s", agent_name, e)
            text = f"[Agent {agent_name} API error: {e}]"
            yield {"event": "error", "agent": agent_name, "message": str(e)}
        except Exception as e:
            logger.error("Agent %s failed: %s", agent_name, e, exc_info=True)
            text = f"[Agent {agent_name} error: {e}]"
            yield {"event": "error", "agent": agent_name, "message": str(e)}

        latency_ms = int((time.time() - start_time) * 1000)

        # Assemble thinking summary for persistence (truncate to 5000 chars)
        thinking_summary = "".join(thinking_chunks)
        if len(thinking_summary) > 5000:
            thinking_summary = thinking_summary[:5000] + "...[truncated]"
        thinking_summary = thinking_summary or None

        # Record usage — must commit or data is lost on session close
        cost_usd = 0.0
        try:
            async with self._db_factory() as db:
                usage = await self._budget.record_usage(
                    db,
                    agent_name=agent_name,
                    model=model,
                    input_tokens=total_input,
                    output_tokens=total_output,
                    cache_creation_input_tokens=total_cache_creation,
                    cache_read_input_tokens=total_cache_read,
                    trigger=trace.trigger if trace else "unknown",
                    trace_id=trace.trace_id if trace else None,
                    workspace_id=workspace_id,
                )
                cost_usd = usage.cost_usd
                await db.commit()
        except Exception as e:
            logger.error("Failed to record token usage: %s", e)

        if span and trace:
            trace.end_span(
                span.span_id,
                input_tokens=total_input,
                output_tokens=total_output,
                cache_creation_input_tokens=total_cache_creation,
                cache_read_input_tokens=total_cache_read,
                tools_called=tools_called,
                tool_call_details=tool_call_details,
                thinking_summary=thinking_summary,
                response_text=text,
                model=model,
                cost_usd=cost_usd,
            )

        yield {
            "event": "agent_done",
            "agent": agent_name,
            "text": text,
            "input_tokens": total_input,
            "output_tokens": total_output,
            "cache_creation_tokens": total_cache_creation,
            "cache_read_tokens": total_cache_read,
            "tools_called": tools_called,
            "latency_ms": latency_ms,
            "cost_usd": round(cost_usd, 6),
        }

    async def run_perception_cycle(self, source: str, user_id: str, workspace_id: str = "") -> dict:
        """Run a perception cycle for a specific data source.

        Observer reads new data -> Librarian extracts entities/memories ->
        Planner evaluates importance -> Presenter notifies if needed.
        """
        trace = self._trace_manager.start_trace(f"perception_{source}")

        try:
            # Check budget
            async with self._db_factory() as db:
                budget_status = await self._budget.get_budget_status(db)
            if not self._budget.should_allow_perception(budget_status):
                logger.warning(
                    "perception_skipped_budget",
                    extra={"source": source, "mode": budget_status.budget_mode},
                )
                return {"status": "skipped", "reason": "budget_exhausted"}

            # Step 1: Observer reads new data from source
            observer_result = await self._call_agent(
                "observer",
                message=f"Observe {source} for new activity. Use get_observation_cursor "
                f"to find where we left off, then fetch only new data.",
                user_id=user_id,
                trace=trace,
                workspace_id=workspace_id,
            )

            # Bail early if observer failed (e.g. API error, no credits)
            if isinstance(observer_result, str) and "API error" in observer_result:
                logger.warning(
                    "perception_observer_failed",
                    extra={"source": source, "error": observer_result},
                )
                return {
                    "status": "error",
                    "source": source,
                    "error": observer_result,
                }

            # Step 2: Librarian extracts entities and memories
            librarian_result = await self._call_agent(
                "librarian",
                message=f"Process these observations from {source} and extract "
                f"entities and memories: {observer_result}",
                user_id=user_id,
                trace=trace,
                workspace_id=workspace_id,
            )

            # Step 3: Planner evaluates if any action is needed
            planner_result = await self._call_agent(
                "planner",
                message=f"Evaluate these observations from {source}. "
                f"Create plans for anything important: {observer_result}",
                user_id=user_id,
                trace=trace,
                workspace_id=workspace_id,
            )

            # Publish perception completed event
            await self._publish_event(
                "perception_completed",
                user_id,
                {"source": source, "trace_id": trace.trace_id},
                trace_id=trace.trace_id,
            )

            return {
                "status": "completed",
                "source": source,
                "trace_id": trace.trace_id,
                "observer": observer_result,
                "librarian": librarian_result,
                "planner": planner_result,
            }

        except Exception as e:
            logger.error("perception_cycle failed: %s", e, exc_info=True)
            return {"status": "error", "source": source, "error": str(e)}
        finally:
            await self._trace_manager.finish_trace(
                trace.trace_id, user_id=user_id, workspace_id=workspace_id
            )

    async def generate_briefing(self, user_id: str, workspace_id: str = "") -> dict:
        """Generate the daily briefing through the Presenter agent.

        Uses the get_briefing tool to fetch real data from the intelligence
        backend (events, plans, approvals, goals) and then formats it through
        the Presenter agent for user-facing delivery.
        """
        trace = self._trace_manager.start_trace("scheduled_briefing")
        try:
            # Step 1: Gather raw briefing data from intelligence server
            raw_data = await self._execute_tool("get_briefing", {"date": "today"}, user_id=user_id)

            # Step 2: Let Presenter format it into a user-friendly briefing
            result = await self._call_agent(
                "presenter",
                message=(
                    "Format the following briefing data into a clear, concise daily briefing "
                    "for the user. Include: top priorities, recent changes, pending approvals, "
                    "and recommended next actions.\n\n"
                    f"Raw briefing data:\n{json.dumps(raw_data, indent=2, default=str)}"
                ),
                user_id=user_id,
                trace=trace,
                workspace_id=workspace_id,
            )

            await self._publish_event(
                "briefing_generated",
                user_id,
                {"trace_id": trace.trace_id},
                trace_id=trace.trace_id,
            )

            return {"status": "completed", "trace_id": trace.trace_id, "briefing": result}
        except Exception as e:
            logger.error("generate_briefing failed: %s", e, exc_info=True)
            return {"status": "error", "error": str(e)}
        finally:
            await self._trace_manager.finish_trace(
                trace.trace_id, user_id=user_id, workspace_id=workspace_id
            )

    async def _publish_event(
        self, event_type: str, user_id: str, payload: dict, trace_id: str | None = None
    ) -> None:
        """Publish an agent action event to the event bus (best-effort)."""
        try:
            if self._event_bus is None:
                import redis.asyncio as aioredis

                self._event_bus_redis = aioredis.from_url(
                    self._settings.redis_url, decode_responses=True
                )
                from src.services.event_bus import EventBus

                self._event_bus = EventBus(self._event_bus_redis)

            stream = self._event_bus.agent_stream(user_id)
            metadata = {"trace_id": trace_id} if trace_id else {}
            await self._event_bus.publish(stream, event_type, payload, user_id, metadata)
        except Exception:
            logger.debug("Failed to publish event %s to bus", event_type, exc_info=True)

    async def _load_conversation_history(
        self, conversation_id: str | None, max_messages: int = 20, max_chars: int = 8000
    ) -> str:
        """Load recent conversation history from DB for multi-turn context.

        Returns a formatted block of prior messages or empty string.
        Truncates to stay within token budget.
        """
        if not conversation_id or not self._db_factory:
            return ""

        try:
            from sqlalchemy import select

            from src.models.conversations import Message

            async with self._db_factory() as db:
                result = await db.execute(
                    select(Message.role, Message.content)
                    .where(Message.conversation_id == conversation_id)
                    .order_by(Message.created_at.desc())
                    .limit(max_messages + 1)  # +1 for the just-saved user message
                )
                rows = result.all()

            if len(rows) <= 1:
                # Only the current message — no history
                return ""

            # Reverse to chronological, skip the last (current) user message
            history = list(reversed(rows[1:]))

            lines: list[str] = []
            total = 0
            for role, content in history:
                label = "User" if role == "user" else "Assistant"
                # Truncate individual messages to avoid one huge message dominating
                snippet = content[:1000] if len(content) > 1000 else content
                line = f"{label}: {snippet}"
                if total + len(line) > max_chars:
                    break
                lines.append(line)
                total += len(line)

            if not lines:
                return ""

            return (
                "--- CONVERSATION HISTORY (most recent messages) ---\n"
                + "\n".join(lines)
                + "\n--- END HISTORY ---"
            )
        except Exception:
            logger.debug("Failed to load conversation history", exc_info=True)
            return ""

    async def _assemble_context(self, agent_name: str, message: str, user_id: str) -> str:
        """Pre-load relevant context for context-enriched agents using ContextBuilder.

        Returns a context block to append to the system prompt, giving the
        agent ambient awareness of the user's world without requiring it to
        explicitly call search_memory.
        """
        if agent_name not in CONTEXT_ENRICHED_AGENTS:
            return ""

        try:
            svc = self._services
            builder = ContextBuilder(
                world_model=svc.world_model,
                memory_service=svc.memory_service,
                goal_tracker=svc.goal_tracker,
                procedure_library=svc.procedure_library,
                artifact_store=svc.artifact_store,
            )
            pack: ContextPack = await builder.build(
                user_id=user_id,
                query=message[:500],
            )
            context_text = ContextBuilder.to_prompt(pack)
            if context_text:
                return f"\n\n--- CONTEXT ---\n{context_text}"
        except Exception:
            logger.debug("Context assembly via ContextBuilder failed", exc_info=True)

        return ""

    def _build_system_prompt(self, agent: SubAgent, context: str = "") -> list[dict]:
        """Build system prompt with cache_control for prompt caching.

        Uses structured system blocks so the static soul + role prompt is cached
        across calls (5-min TTL), saving ~90% on re-reads of the system prompt.
        """
        blocks = [
            {
                "type": "text",
                "text": f"{JARVIS_SOUL}\n\n--- YOUR ROLE ---\n{agent.prompt}",
                "cache_control": {"type": "ephemeral"},
            },
        ]
        if context:
            blocks.append({"type": "text", "text": context})
        return blocks

    def _apply_cache_control_to_tools(self, tools: list[dict]) -> list[dict]:
        """Mark the last tool definition with cache_control for tool caching."""
        if not tools:
            return tools
        tools = [dict(t) for t in tools]
        tools[-1] = {**tools[-1], "cache_control": {"type": "ephemeral"}}
        return tools

    async def _call_agent(
        self,
        agent_name: str,
        message: str,
        user_id: str,
        trace=None,
        max_tool_rounds: int = 10,
        workspace_id: str = "",
    ) -> str:
        """Call a sub-agent with the Claude API.

        Handles tool use loops: the agent may call tools, we execute them
        and feed results back until the agent produces a final text response.
        """
        agent = self._agents.get(agent_name)
        if not agent:
            raise ValueError(f"Unknown agent: {agent_name}")

        model = self._get_model_for_agent(agent)
        tools = self._apply_cache_control_to_tools(self._get_tools_for_agent(agent))
        span = trace.start_span(agent_name) if trace else None

        # Build typed envelope for this agent call
        envelope = AgentEnvelope(
            agent_name=agent_name,
            message=message,
            tools_available=[t["name"] for t in tools],
        )
        logger.debug(
            "agent_envelope: %s tools=%d", envelope.agent_name, len(envelope.tools_available)
        )

        # Assemble context (memories + entities) for enriched agents
        context_block = await self._assemble_context(agent_name, message, user_id=user_id)
        system_blocks = self._build_system_prompt(agent, context_block)

        messages = [{"role": "user", "content": message}]

        total_input = 0
        total_output = 0
        total_cache_creation = 0
        total_cache_read = 0
        tools_called: list[str] = []
        tool_call_details: list[SpanToolCall] = []
        thinking_chunks: list[str] = []
        start_time = time.time()

        # Extended thinking — all Claude 4+ models support it
        thinking_budget = min(8192, max(1024, agent.max_tokens // 2))
        if thinking_budget >= agent.max_tokens:
            thinking_budget = agent.max_tokens - 1

        try:
            for _round in range(max_tool_rounds):
                api_kwargs = {
                    "model": model,
                    "max_tokens": agent.max_tokens,
                    "temperature": 1,  # required when thinking is enabled
                    "thinking": {
                        "type": "enabled",
                        "budget_tokens": thinking_budget,
                    },
                    "system": system_blocks,
                    "messages": messages,
                }
                if tools:
                    api_kwargs["tools"] = tools

                try:
                    response = await self._client.messages.create(**api_kwargs)
                except Exception as think_err:
                    # Fallback: disable thinking if the model/provider rejects it
                    logger.warning(
                        "Thinking failed for %s, falling back: %s", agent_name, think_err
                    )
                    api_kwargs["temperature"] = agent.temperature
                    api_kwargs.pop("thinking", None)
                    response = await self._client.messages.create(**api_kwargs)

                total_input += response.usage.input_tokens
                total_output += response.usage.output_tokens
                total_cache_creation += (
                    getattr(response.usage, "cache_creation_input_tokens", 0) or 0
                )
                total_cache_read += getattr(response.usage, "cache_read_input_tokens", 0) or 0

                # Capture thinking content for persistence
                for block in response.content:
                    if block.type == "thinking" and hasattr(block, "thinking"):
                        thinking_chunks.append(block.thinking)

                # Check if the response contains tool use
                tool_use_blocks = [b for b in response.content if b.type == "tool_use"]

                if not tool_use_blocks:
                    # Final text response
                    text = "".join(b.text for b in response.content if b.type == "text")
                    break

                # Process tool calls
                tool_results = []
                for tool_block in tool_use_blocks:
                    tool_name = tool_block.name
                    tool_input = tool_block.input
                    tools_called.append(tool_name)

                    # Governor pre-hook for write tools
                    pre_result = await governor_pre_tool_hook(
                        tool_name,
                        tool_input,
                        agent_name,
                        user_id=user_id,
                        db_factory=self._db_factory,
                        services=self._services,
                    )

                    if not pre_result.get("allowed", True):
                        tool_results.append(
                            {
                                "type": "tool_result",
                                "tool_use_id": tool_block.id,
                                "content": json.dumps(
                                    {
                                        "error": pre_result.get("reason", "Blocked by policy"),
                                        "approval_required": pre_result.get(
                                            "approval_required", False
                                        ),
                                        "approval_id": pre_result.get("approval_id"),
                                    }
                                ),
                            }
                        )
                        tool_call_details.append(
                            SpanToolCall(
                                tool_name=tool_name,
                                input_data=tool_input if isinstance(tool_input, dict) else {},
                                status="blocked",
                                error=pre_result.get("reason", "Blocked by policy"),
                            )
                        )
                        continue

                    # Execute the tool
                    tool_start = time.time()
                    result = await self._execute_tool(tool_name, tool_input, user_id=user_id)
                    tool_latency = int((time.time() - tool_start) * 1000)

                    tool_results.append(
                        {
                            "type": "tool_result",
                            "tool_use_id": tool_block.id,
                            "content": json.dumps(result)
                            if isinstance(result, dict)
                            else str(result),
                        }
                    )

                    # Truncate large results for persistence
                    persisted_output = result
                    if isinstance(result, str) and len(result) > 2000:
                        persisted_output = result[:2000] + "...[truncated]"
                    elif isinstance(result, dict):
                        result_str = json.dumps(result, default=str)
                        if len(result_str) > 2000:
                            persisted_output = {"_truncated": result_str[:2000]}

                    tool_call_details.append(
                        SpanToolCall(
                            tool_name=tool_name,
                            input_data=tool_input if isinstance(tool_input, dict) else {},
                            output_data=persisted_output,
                            status="success",
                            duration_ms=tool_latency,
                        )
                    )

                    # Audit post-hook
                    await audit_post_tool_hook(
                        tool_name,
                        tool_input,
                        result,
                        agent_name,
                        trace_id=trace.trace_id if trace else None,
                        span_id=span.span_id if span else None,
                        latency_ms=tool_latency,
                        db_factory=self._db_factory,
                    )

                # Preserve thinking blocks for multi-turn tool-use continuity
                messages.append({"role": "assistant", "content": response.content})
                messages.append({"role": "user", "content": tool_results})
            else:
                text = f"[Agent {agent_name} hit max tool rounds ({max_tool_rounds})]"

        except anthropic.APIError as e:
            logger.error("Claude API error in %s: %s", agent_name, e)
            text = f"[Agent {agent_name} API error: {e}]"
        except Exception as e:
            logger.error("Agent %s failed: %s", agent_name, e, exc_info=True)
            text = f"[Agent {agent_name} error: {e}]"

        latency_ms = int((time.time() - start_time) * 1000)

        # Assemble thinking summary for persistence (truncate to 5000 chars)
        thinking_summary = "".join(thinking_chunks)
        if len(thinking_summary) > 5000:
            thinking_summary = thinking_summary[:5000] + "...[truncated]"
        thinking_summary = thinking_summary or None

        # Record token usage — must commit or data is lost on session close
        cost_usd = 0.0
        try:
            async with self._db_factory() as db:
                usage = await self._budget.record_usage(
                    db,
                    agent_name=agent_name,
                    model=model,
                    input_tokens=total_input,
                    output_tokens=total_output,
                    cache_creation_input_tokens=total_cache_creation,
                    cache_read_input_tokens=total_cache_read,
                    trigger=trace.trigger if trace else "unknown",
                    trace_id=trace.trace_id if trace else None,
                    workspace_id=workspace_id,
                )
                cost_usd = usage.cost_usd
                await db.commit()
        except Exception as e:
            logger.error("Failed to record token usage: %s", e)

        # End span
        if span and trace:
            trace.end_span(
                span.span_id,
                input_tokens=total_input,
                output_tokens=total_output,
                cache_creation_input_tokens=total_cache_creation,
                cache_read_input_tokens=total_cache_read,
                tools_called=tools_called,
                tool_call_details=tool_call_details,
                thinking_summary=thinking_summary,
                response_text=text,
                model=model,
                cost_usd=cost_usd,
            )

        # Build typed result
        agent_result = AgentResult(
            agent_name=agent_name,
            response_text=text,
            tools_called=tools_called,
            tokens_used=total_input + total_output,
        )

        logger.info(
            "agent_call_complete",
            extra={
                "agent": agent_result.agent_name,
                "model": model,
                "input_tokens": total_input,
                "output_tokens": total_output,
                "tools_called": agent_result.tools_called,
                "latency_ms": latency_ms,
                "trace_id": trace.trace_id if trace else None,
            },
        )

        return agent_result.response_text

    async def _execute_tool(self, tool_name: str, tool_input: dict, user_id: str) -> dict:
        """Execute a tool by name, using ToolRegistry for dispatch.

        Resolution order:
        0. Pre-dispatch: ToolRegistry blocked/risk/write classification
        1. Internal intelligence tools (FastMCP handlers)
        2. MCP bridge (external MCP servers)
        3. Connector-backed tools (dispatched via connector.execute_action)
        """
        # 0. Pre-dispatch: ToolRegistry classification
        try:
            from src.services.tool_registry import ToolRegistry

            async with self._db_factory() as db:
                registry = ToolRegistry(db)
                if await registry.is_blocked_tool(tool_name):
                    return {"error": f"Tool '{tool_name}' is disabled", "blocked": True}
        except Exception:
            pass  # Fallback: skip pre-checks if registry unavailable

        from src.tools import intelligence_server

        # Internal tool handlers — the intelligence server functions
        internal_handlers = {
            "ingest_event": intelligence_server.ingest_event,
            "search_memory": intelligence_server.search_memory,
            "get_entities": intelligence_server.get_entities,
            "update_entity": intelligence_server.update_entity,
            "plan_command": intelligence_server.plan_command,
            "get_active_plans": intelligence_server.get_active_plans,
            "evaluate_policy": intelligence_server.evaluate_policy,
            "approve_action": intelligence_server.approve_action,
            "get_briefing": intelligence_server.get_briefing,
            "get_observation_cursor": intelligence_server.get_observation_cursor,
            "update_observation_cursor": intelligence_server.update_observation_cursor,
            "report_observation": intelligence_server.report_observation,
            "update_execution": intelligence_server.update_execution,
            "extract_preferences": intelligence_server.extract_preferences,
            "create_task": intelligence_server.create_task,
            "get_task": intelligence_server.get_task,
            "get_goals": intelligence_server.get_goals,
            "build_context": intelligence_server.build_context,
            "verify_run": intelligence_server.verify_run,
        }

        # Emit tool.started event
        await self._publish_event("tool.started", user_id, {"tool": tool_name})

        # 1. Try internal handlers first
        handler = internal_handlers.get(tool_name)
        if handler:
            try:
                result = await handler(user_id=user_id, **tool_input)
                await self._publish_event("tool.completed", user_id, {"tool": tool_name})
                return result
            except TypeError as e:
                logger.warning("Tool %s argument error: %s", tool_name, e)
                await self._publish_event(
                    "tool.failed",
                    user_id,
                    {"tool": tool_name, "error": str(e)[:200]},
                )
                return {"error": f"Invalid arguments for {tool_name}: {e}"}

        # 2. Try MCP bridge (external MCP servers: Google Workspace, GitHub, Slack, etc.)
        from src.connectors.mcp_bridge import call_mcp_tool, is_mcp_tool

        if is_mcp_tool(tool_name):
            try:
                result = await call_mcp_tool(tool_name, tool_input)
                await self._publish_event("tool.completed", user_id, {"tool": tool_name})
                return result
            except Exception as e:
                await self._publish_event(
                    "tool.failed",
                    user_id,
                    {"tool": tool_name, "error": str(e)[:200]},
                )
                raise

        # 3. Fall back to ToolRegistry for connector-backed tools
        try:
            result = await self._execute_connector_tool(tool_name, tool_input, user_id=user_id)
            await self._publish_event("tool.completed", user_id, {"tool": tool_name})
            return result
        except Exception as e:
            logger.error("Connector tool %s failed: %s", tool_name, e, exc_info=True)
            await self._publish_event(
                "tool.failed",
                user_id,
                {"tool": tool_name, "error": str(e)[:200]},
            )
            return {"error": f"Tool execution failed for {tool_name}: {e}"}

    async def _execute_connector_tool(self, tool_name: str, tool_input: dict, user_id: str) -> dict:
        """Execute a tool via its connector, resolved from the ToolRegistry."""
        from src.connectors.base import CONNECTOR_REGISTRY
        from src.services.tool_registry import ToolRegistry

        async with self._db_factory() as db:
            registry = ToolRegistry(db)
            tool_def = await registry.get_tool(tool_name)

            if not tool_def:
                return {"error": f"Unknown tool: {tool_name}"}

            if not tool_def.enabled:
                return {"error": f"Tool '{tool_name}' is disabled"}

            connector_type = tool_def.connector_type
            if not connector_type or connector_type == "internal":
                return {"error": f"No connector handler for internal tool: {tool_name}"}

            # Map connector_type to CONNECTOR_REGISTRY key
            connector_cls = CONNECTOR_REGISTRY.get(connector_type)
            if not connector_cls:
                return {
                    "error": f"No connector registered for type: {connector_type}",
                    "available_connectors": list(CONNECTOR_REGISTRY.keys()),
                }

            # Get credentials from OAuth manager
            oauth = self._services.oauth_manager
            credentials = {}
            if oauth:
                try:
                    credentials = await oauth.get_credentials(user_id, connector_type)
                except Exception:
                    logger.warning("No credentials for connector %s", connector_type)

            # Instantiate connector and execute action
            connector = connector_cls(self._settings)
            # Derive the action name from the tool name (e.g. gmail_send → send)
            action = tool_name
            if tool_name.startswith(f"{connector_type}_"):
                action = tool_name[len(connector_type) + 1 :]

            return await connector.execute_action(action, tool_input, credentials)

    async def _execute_plan_via_graph(self, plan_id: str, user_id: str, trace=None) -> dict:
        """Bridge: create a run from a plan and execute it via GraphExecutor.

        This is the critical connection between the orchestrator (agent routing)
        and the GraphExecutor (DAG execution). Without this bridge, plans generated
        by the Planner would never actually execute.
        """
        from src.services.context_builder import ContextBuilder
        from src.services.graph_executor import GraphExecutor
        from src.services.tool_registry import ToolRegistry

        try:
            async with self._db_factory() as db:
                svc = self._services
                tool_registry = ToolRegistry(db)

                context_builder = ContextBuilder(
                    world_model=svc.world_model,
                    memory_service=svc.memory_service,
                    goal_tracker=svc.goal_tracker,
                    procedure_library=svc.procedure_library,
                    artifact_store=svc.artifact_store,
                )

                async def get_credentials(connector_type: str) -> dict:
                    if svc.oauth_manager:
                        return await svc.oauth_manager.get_credentials(user_id, connector_type)
                    return {}

                executor = GraphExecutor(
                    settings=self._settings,
                    db=db,
                    event_bus=self._event_bus,
                    notifier=self._notifier,
                    tool_registry=tool_registry,
                    context_builder=context_builder,
                    connector_credentials_fn=get_credentials,
                    memory_service=svc.memory_service,
                )

                run = await executor.create_run(plan_id, user_id)

                await self._publish_event(
                    "execution_started",
                    user_id,
                    {"plan_id": plan_id, "run_id": run.run_id},
                    trace_id=trace.trace_id if trace else None,
                )

                completed_run = await executor.execute_run(
                    run.run_id,
                    trace_id=trace.trace_id if trace else None,
                )

                await self._publish_event(
                    "execution_completed",
                    user_id,
                    {
                        "plan_id": plan_id,
                        "run_id": run.run_id,
                        "status": completed_run.status,
                    },
                    trace_id=trace.trace_id if trace else None,
                )

                return {
                    "run_id": run.run_id,
                    "status": completed_run.status,
                    "error": completed_run.error,
                }
        except Exception as e:
            logger.error("Plan execution via graph failed: %s", e, exc_info=True)
            return {"status": "error", "error": str(e)}

    def _extract_decision(self, response_text: str) -> dict:
        """Extract structured decision from planner response."""
        # Try to parse JSON from the response
        try:
            # Look for JSON block in the response
            if "{" in response_text:
                start = response_text.index("{")
                # Find matching closing brace
                depth = 0
                for i, ch in enumerate(response_text[start:], start):
                    if ch == "{":
                        depth += 1
                    elif ch == "}":
                        depth -= 1
                        if depth == 0:
                            json_str = response_text[start : i + 1]
                            return json.loads(json_str)
        except (json.JSONDecodeError, ValueError):
            pass

        # Fallback: extract what we can
        return {
            "decision": "acknowledge",
            "reasoning": response_text[:500],
        }

    async def _resolve_pipeline(self, decision: dict) -> list[dict]:
        """Resolve a planner decision to an agent pipeline via RouteResolver."""
        try:
            async with self._db_factory() as db:
                resolver = RouteResolver(db)
                return await resolver.resolve(decision)
        except Exception:
            logger.debug("Route resolution failed, using empty pipeline", exc_info=True)
            return []

    @staticmethod
    def _check_step_condition(condition: dict, decision: dict) -> bool:
        """Check if a pipeline step's condition is satisfied."""
        for key, value in condition.items():
            if key == "has_key":
                if value not in decision:
                    return False
            elif key == "not_has_key":
                if value in decision:
                    return False
            else:
                if decision.get(key) != value:
                    return False
        return True
