"""JarvisOrchestrator — the consciousness of Jarvis.

Routes user messages and system events to the right sub-agents,
manages traces, enforces budgets, and coordinates the intelligence loop.
This is the main entry point for all Jarvis interactions.
"""

import json
import logging
from collections.abc import AsyncGenerator
from typing import Any

from ulid import ULID

from src.config.settings import Settings, get_anthropic_client
from src.models.task_graph import TaskRun, TaskStep
from src.orchestrator.agent_loop import (
    LoopAgentStart,
    LoopDone,
    LoopError,
    LoopTextDelta,
    LoopThinking,
    LoopToolCall,
    LoopToolResult,
    agent_loop,
)
from src.orchestrator.agents import AGENTS, SubAgent
from src.orchestrator.budget import BudgetTracker
from src.orchestrator.contracts import PlannerOutput
from src.orchestrator.prompts import JARVIS_SOUL
from src.orchestrator.services import ServiceContainer
from src.orchestrator.tool_schemas import build_tool_definitions
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

# Intent classifier prompt — used with Haiku for fast, cheap classification
INTENT_CLASSIFIER_PROMPT = """\
<role>
You classify user messages for a personal AI assistant called Jarvis.
Output ONLY a JSON object, nothing else.
</role>

<intents>
- greeting: Greetings, pleasantries, "hey", "hi", "good morning", "thanks"
- chitchat: Casual conversation, "how are you", jokes, small talk
- simple_question: Direct factual question answerable from context/memory
- data_fetch: Read from external source (check email, show calendar, read slack)
- status_query: Asking about goals, plans, briefing, pending items, tasks
- approval_response: Approving/rejecting a pending action
- command: Actionable WRITE request needing planning (send email, schedule, create)
- complex: Multi-step, ambiguous, or high-stakes requests needing deep planning
</intents>

<output_format>
{"intent": "<one of above>", "confidence": 0.0-1.0}
</output_format>

<examples>
"Hey Jarvis" -> {"intent": "greeting", "confidence": 0.99}
"What's John's email?" -> {"intent": "simple_question", "confidence": 0.9}
"Check my gmail" -> {"intent": "data_fetch", "confidence": 0.95}
"Show my latest emails" -> {"intent": "data_fetch", "confidence": 0.95}
"What's on my calendar today" -> {"intent": "data_fetch", "confidence": 0.95}
"Any new Slack messages?" -> {"intent": "data_fetch", "confidence": 0.9}
"Show my goals" -> {"intent": "status_query", "confidence": 0.95}
"Approve that email" -> {"intent": "approval_response", "confidence": 0.9}
"Send a follow-up to the investor" -> {"intent": "command", "confidence": 0.95}
"Analyze our Q3 pipeline and create action items" -> {"intent": "complex", "confidence": 0.9}
</examples>
"""

# Intents that skip the Planner entirely
FAST_INTENTS = {
    "greeting",
    "chitchat",
    "simple_question",
    "data_fetch",
    "status_query",
    "approval_response",
}

# Confidence threshold — below this, fall back to Planner
INTENT_CONFIDENCE_THRESHOLD = 0.7


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
        services: ServiceContainer,
    ):
        self._settings = settings
        self._db_factory = db_factory
        self._services = services
        self._client = get_anthropic_client(settings)
        self._trace_store = TraceStore(
            elasticsearch_url=settings.elasticsearch_url,
            db_factory=db_factory,
        )
        self._trace_manager = TraceManager(trace_store=self._trace_store)
        self._budget = BudgetTracker(daily_limit_usd=settings.daily_token_budget_usd)
        self._agents: dict[str, SubAgent] = dict(AGENTS)  # Start with hardcoded defaults
        self._tools = self._build_tool_definitions()
        self._event_bus = None  # Lazy-init when Redis available

    async def load_agents_from_db(self) -> None:
        """Load agent definitions from the database, replacing hardcoded defaults."""
        try:
            async with self._db_factory() as db:
                registry = AgentRegistry(db)
                await registry.seed_defaults()
                await db.commit()
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
        decision: PlannerOutput,
        trace_id: str,
        conversation_id: str | None = None,
    ) -> str | None:
        """Create a lightweight TaskRun for every user interaction.

        Even simple decisions (acknowledge, answer_directly) get a single-step
        run so ALL interactions are tracked in the runs table.
        Returns the run_id on success, None if DB unavailable.
        """
        run_id = f"run_{ULID()}"

        try:
            async with self._db_factory() as db:
                run = TaskRun(
                    run_id=run_id,
                    user_id=user_id,
                    workspace_id=workspace_id,
                    plan_id=decision.plan_id,
                    status="running",
                    source="user_message",
                    execution_mode=decision.execution_mode,
                    policy_decision={"decision": decision.decision},
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
                    step_type=decision.decision,
                    status="running",
                    input_data=decision.model_dump(mode="json"),
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
        """Build Claude tool definitions from internal tools + MCP + native connectors.

        Sources (in order):
        1. Internal Pydantic-defined tools (intelligence layer)
        2. MCP tools from session pool (external MCP servers)
        3. Native connector actions (Gmail, Calendar, GitHub, Slack, etc.)
        """
        tools = self._build_internal_tool_definitions()

        # Append MCP tools from the bridge session pool
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

        # Append native connector actions as tools (Gmail, Calendar, etc.)
        tools.extend(self._build_native_connector_tools())

        return tools

    @staticmethod
    def _build_native_connector_tools() -> list[dict]:
        """Build Claude tool definitions for native connector actions.

        These tools use Jarvis's built-in connectors (with OAuth tokens from the DB)
        instead of MCP servers that need separate credential files.
        """
        return [
            {
                "name": "gmail_list_unread",
                "description": "List unread emails from Gmail inbox.",
                "input_schema": {
                    "type": "object",
                    "properties": {
                        "max_results": {
                            "type": "integer",
                            "description": "Max emails to return (default 20)",
                            "default": 20,
                        },
                        "query": {
                            "type": "string",
                            "description": "Gmail search query (default: is:inbox is:unread)",
                        },
                    },
                },
            },
            {
                "name": "gmail_get_message",
                "description": "Get full details of a specific Gmail message by ID.",
                "input_schema": {
                    "type": "object",
                    "properties": {
                        "message_id": {"type": "string", "description": "Gmail message ID"},
                    },
                    "required": ["message_id"],
                },
            },
            {
                "name": "gmail_send_email",
                "description": "Send an email via Gmail.",
                "input_schema": {
                    "type": "object",
                    "properties": {
                        "to": {"type": "string", "description": "Recipient email address"},
                        "subject": {"type": "string", "description": "Email subject"},
                        "body": {"type": "string", "description": "Email body text"},
                        "thread_id": {
                            "type": "string",
                            "description": "Thread ID for replies (optional)",
                        },
                    },
                    "required": ["to", "subject", "body"],
                },
            },
            {
                "name": "gmail_create_draft",
                "description": "Create an email draft in Gmail.",
                "input_schema": {
                    "type": "object",
                    "properties": {
                        "to": {"type": "string", "description": "Recipient email"},
                        "subject": {"type": "string", "description": "Email subject"},
                        "body": {"type": "string", "description": "Email body"},
                    },
                    "required": ["to", "subject", "body"],
                },
            },
            {
                "name": "gmail_archive",
                "description": "Archive a Gmail message (remove from inbox).",
                "input_schema": {
                    "type": "object",
                    "properties": {
                        "message_id": {"type": "string", "description": "Gmail message ID"},
                    },
                    "required": ["message_id"],
                },
            },
            {
                "name": "gmail_mark_read",
                "description": "Mark a Gmail message as read.",
                "input_schema": {
                    "type": "object",
                    "properties": {
                        "message_id": {"type": "string", "description": "Gmail message ID"},
                    },
                    "required": ["message_id"],
                },
            },
        ]

    def _build_internal_tool_definitions(self) -> list[dict]:
        """Build Claude tool definitions from Pydantic models in tool_schemas."""
        return build_tool_definitions()

    @staticmethod
    def _internal_tool_names() -> set[str]:
        """Return the set of internal (non-MCP) tool names."""
        from src.orchestrator.tool_schemas import TOOL_INPUT_MODELS

        return set(TOOL_INPUT_MODELS.keys())

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
            # Emit command_received event
            await self._emit_runtime_event(
                "command_received",
                workspace_id=workspace_id,
                user_id=user_id,
                payload={"surface": surface, "message_preview": message[:100]},
            )

            # Load conversation history for multi-turn context
            history_block = await self._load_conversation_history(conversation_id)

            # Step 0: Fast intent classification
            intent, confidence = await self._classify_intent(message, history_block)
            use_planner = intent not in FAST_INTENTS or confidence < INTENT_CONFIDENCE_THRESHOLD

            # Emit route_selected event
            await self._emit_runtime_event(
                "route_selected",
                workspace_id=workspace_id,
                user_id=user_id,
                payload={"intent": intent, "confidence": confidence, "use_planner": use_planner},
            )

            if use_planner:
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
            else:
                decision = self._intent_to_decision(intent, message)

            decision_dict = decision.model_dump(mode="json")
            decision_json = json.dumps(decision_dict)

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
                "decision": decision.decision,
                "summary": decision.reasoning or plan_result,
            }

            # Publish plan event
            await self._publish_event(
                "plan_generated",
                user_id,
                {"decision": decision_dict, "trace_id": trace.trace_id},
                trace_id=trace.trace_id,
            )

            # Resolve agent pipeline from routes
            pipeline = await self._resolve_pipeline(decision_dict)

            for step in pipeline:
                agent_name = step.get("agent", "")
                if not agent_name or agent_name not in self._agents:
                    continue

                # Check step-level condition
                step_cond = step.get("condition")
                if step_cond and not self._check_step_condition(step_cond, decision_dict):
                    continue

                # Handle special actions
                action = step.get("action")
                if action == "execute_plan":
                    if decision.plan_id:
                        exec_result = await self._execute_plan_via_graph(
                            decision.plan_id, user_id, trace
                        )
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
                    f"Decision: {decision.decision}\n"
                    f"Extract any preference signals.",
                    user_id=user_id,
                    trace=trace,
                    workspace_id=workspace_id,
                )
            except Exception:
                logger.debug("Persona reflection skipped", exc_info=True)

            # Complete the lightweight run
            await self._complete_lightweight_run(run_id, result, success=True)
            await self._emit_runtime_event(
                "run_completed",
                workspace_id=workspace_id,
                user_id=user_id,
                run_id=run_id,
                payload={"trace_id": trace.trace_id},
            )

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
                await self._emit_runtime_event(
                    "run_failed",
                    workspace_id=workspace_id,
                    user_id=user_id,
                    run_id=run_id,
                    payload={"error": str(e)[:200]},
                )
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
        mode: str = "ask",
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

            # Step 0: Fast intent classification (Haiku — <200ms)
            intent, confidence = await self._classify_intent(message, history_block)
            yield {"event": "intent", "intent": intent, "confidence": confidence}

            # Decide routing based on intent AND mode
            # execute mode: always plan, then auto-execute
            # plan mode: always plan, but stop before execution
            # ask mode: use intent classification (current default)
            if mode == "execute":
                use_planner = True
            elif mode == "plan":
                use_planner = True
            else:
                use_planner = intent not in FAST_INTENTS or confidence < INTENT_CONFIDENCE_THRESHOLD

            decision: PlannerOutput
            plan_text = ""

            if use_planner:
                # Full Planner path for commands/complex intents
                planner_message = f"User message: {message}\n\nContext: {json.dumps(context or {})}"
                if history_block:
                    planner_message = f"{history_block}\n\n{planner_message}"

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
            else:
                # Fast path — synthesize a lightweight decision from intent
                decision = self._intent_to_decision(intent, message)

            # Apply mode overrides
            if mode == "execute" and decision.execution_mode != "auto_execute":
                decision = decision.model_copy(update={"execution_mode": "auto_execute"})
            elif mode == "plan" and decision.execution_mode != "draft_only":
                decision = decision.model_copy(update={"execution_mode": "draft_only"})

            decision_dict = decision.model_dump(mode="json")
            decision_json = json.dumps(decision_dict)

            # Create a lightweight TaskRun for tracking
            run_id = await self._create_lightweight_run(
                user_id=user_id,
                workspace_id=workspace_id,
                decision=decision,
                trace_id=trace.trace_id,
                conversation_id=conversation_id,
            )

            yield {
                "event": "decision",
                "decision": decision_dict,
                "run_id": run_id,
            }

            # Step 2: Route based on intent
            if use_planner:
                # Planner path: resolve pipeline from DB routes
                pipeline = await self._resolve_pipeline(decision_dict)

                for step in pipeline:
                    agent_name = step.get("agent", "")
                    if not agent_name or agent_name not in self._agents:
                        continue

                    step_cond = step.get("condition")
                    if step_cond and not self._check_step_condition(step_cond, decision_dict):
                        continue

                    action = step.get("action")
                    if action == "execute_plan":
                        # Plan mode (draft_only): skip execution, just present the plan
                        if decision.execution_mode == "draft_only":
                            yield {
                                "event": "plan_ready",
                                "plan_id": decision.plan_id,
                                "message": "Plan created. Review and approve to execute.",
                            }
                            continue
                        if decision.plan_id:
                            yield {
                                "event": "execution_start",
                                "plan_id": decision.plan_id,
                            }
                            exec_result = await self._execute_plan_via_graph(
                                decision.plan_id, user_id, trace
                            )
                            yield {
                                "event": "execution_result",
                                "run_id": exec_result.get("run_id"),
                                "status": exec_result.get("status"),
                            }
                        continue

                    template = step.get("message_template", "Process this: {decision_json}")
                    agent_message = template.format(
                        decision_json=decision_json,
                        surface=surface,
                        message=message,
                    )

                    async for evt in self._call_agent_stream(
                        agent_name,
                        message=agent_message,
                        user_id=user_id,
                        trace=trace,
                        workspace_id=workspace_id,
                    ):
                        yield evt

            elif intent == "simple_question":
                # Researcher gathers context, then Presenter responds
                async for evt in self._call_agent_stream(
                    "researcher",
                    message=f"Research this question for the user: {message}",
                    user_id=user_id,
                    trace=trace,
                    workspace_id=workspace_id,
                ):
                    yield evt

            elif intent == "data_fetch":
                # Observer reads from external sources (Gmail, Calendar, Slack)
                observer_text = ""
                async for evt in self._call_agent_stream(
                    "observer",
                    message=(
                        f"The user wants to check an external source. "
                        f"Read the relevant data and report what you find.\n\n"
                        f"User request: {message}"
                    ),
                    user_id=user_id,
                    trace=trace,
                    workspace_id=workspace_id,
                ):
                    yield evt
                    if evt.get("event") == "agent_done":
                        observer_text = evt.get("text", "")

                # Feed observer results to presenter context
                if observer_text:
                    plan_text = f"Observer findings:\n{observer_text}"

            elif intent == "status_query":
                # Fetch status data via tools, then let Presenter format
                pass  # Presenter will handle with context enrichment below

            elif intent == "approval_response":
                # Governor handles approval directly
                async for evt in self._call_agent_stream(
                    "governor",
                    message=f"The user wants to approve/reject an action: {message}",
                    user_id=user_id,
                    trace=trace,
                    workspace_id=workspace_id,
                ):
                    yield evt

            # Step 3: Presenter formats the response (always)
            presenter_msg = (
                f"Respond to the user ({surface}). Be conversational and helpful.\n\n"
                f"User message: {message}\n"
                f"Intent: {intent}\n"
            )
            if plan_text:
                presenter_msg += (
                    f"Planner decision: {decision_json}\nPlanner analysis: {plan_text[:2000]}\n"
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

            # Persona learning — only for meaningful interactions
            if intent in ("command", "complex"):
                try:
                    await self._call_agent(
                        "persona",
                        message=f"Observe this user interaction on {surface}:\n"
                        f"User said: {message}\n"
                        f"Decision: {decision.decision}\n"
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
                    {"decision": decision.decision, "summary": presenter_text},
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
        """Call a sub-agent with streaming, yielding SSE-compatible dicts."""
        agent = self._agents.get(agent_name)
        if not agent:
            yield {"event": "error", "message": f"Unknown agent: {agent_name}"}
            return

        model = self._get_model_for_agent(agent)
        tools = self._apply_cache_control_to_tools(self._get_tools_for_agent(agent))
        context_block = await self._assemble_context(agent_name, message, user_id=user_id)
        system_blocks = self._build_system_prompt(agent, context_block)

        async for evt in agent_loop(
            client=self._client,
            agent=agent,
            model=model,
            system_blocks=system_blocks,
            tools=tools,
            message=message,
            user_id=user_id,
            workspace_id=workspace_id,
            db_factory=self._db_factory,
            services=self._services,
            budget=self._budget,
            trace=trace,
            execute_tool_fn=self._execute_tool,
            max_tool_rounds=max_tool_rounds,
            stream=True,
        ):
            if isinstance(evt, LoopAgentStart):
                yield {"event": "agent_start", "agent": evt.agent, "model": evt.model}
            elif isinstance(evt, LoopThinking):
                yield {
                    "event": "thinking",
                    "agent": evt.agent,
                    "text": evt.text,
                    "is_thinking": evt.is_thinking,
                }
            elif isinstance(evt, LoopTextDelta):
                yield {"event": "text_delta", "agent": evt.agent, "text": evt.text}
            elif isinstance(evt, LoopToolCall):
                yield {
                    "event": "tool_call",
                    "agent": evt.agent,
                    "tool": evt.tool_name,
                    "input": evt.tool_input,
                }
            elif isinstance(evt, LoopToolResult):
                yield {
                    "event": "tool_result",
                    "agent": evt.agent,
                    "tool": evt.tool_name,
                    "result": evt.result,
                    "blocked": evt.blocked,
                    "latency_ms": evt.latency_ms,
                }
            elif isinstance(evt, LoopError):
                yield {"event": "error", "agent": evt.agent, "message": evt.message}
            elif isinstance(evt, LoopDone):
                yield {
                    "event": "agent_done",
                    "agent": evt.agent,
                    "text": evt.text,
                    "input_tokens": evt.input_tokens,
                    "output_tokens": evt.output_tokens,
                    "cache_creation_tokens": evt.cache_creation_tokens,
                    "cache_read_tokens": evt.cache_read_tokens,
                    "tools_called": evt.tools_called,
                    "latency_ms": evt.latency_ms,
                    "cost_usd": round(evt.cost_usd, 6),
                }

    async def run_perception_cycle(self, source: str, user_id: str, workspace_id: str = "") -> dict:
        """Run a perception cycle for a specific data source.

        Step 1: Poll the connector directly (no Claude call — just API fetch).
        Step 2: If new events found, Librarian extracts entities/memories.
        Step 3: Planner evaluates importance and creates plans if needed.
        """
        trace = self._trace_manager.start_trace(f"perception_{source}")

        try:
            # Check budget (only for Librarian + Planner calls, polling is cheap)
            async with self._db_factory() as db:
                budget_status = await self._budget.get_budget_status(db)
            if not self._budget.should_allow_perception(budget_status):
                logger.warning(
                    "perception_skipped_budget",
                    extra={"source": source, "mode": budget_status.budget_mode},
                )
                return {"status": "skipped", "reason": "budget_exhausted"}

            # Step 1: Poll the connector directly for new events
            raw_events, new_cursor, poll_error, cursor_type = await self._poll_connector(
                source, user_id, workspace_id
            )

            if poll_error:
                logger.warning(
                    "perception_poll_failed",
                    extra={"source": source, "error": poll_error},
                )
                return {"status": "error", "source": source, "error": poll_error}

            if not raw_events:
                logger.info(
                    "perception_no_new_events",
                    extra={"source": source},
                )
                return {"status": "completed", "source": source, "events": 0}

            # Ingest raw events into normalized_events table
            event_summaries = await self._ingest_raw_events(raw_events, user_id, workspace_id)

            # Update the observation cursor
            await self._update_cursor(source, user_id, workspace_id, new_cursor, cursor_type)

            observer_summary = f"Polled {source}: {len(raw_events)} new event(s).\n" + "\n".join(
                f"- {s}" for s in event_summaries[:20]
            )

            # Step 2: Librarian extracts entities and memories
            librarian_result = await self._call_agent(
                "librarian",
                message=f"Process these observations from {source} and extract "
                f"entities and memories:\n{observer_summary}",
                user_id=user_id,
                trace=trace,
                workspace_id=workspace_id,
            )

            # Step 3: Planner evaluates if any action is needed
            planner_result = await self._call_agent(
                "planner",
                message=f"Evaluate these observations from {source}. "
                f"Create plans for anything important:\n{observer_summary}",
                user_id=user_id,
                trace=trace,
                workspace_id=workspace_id,
            )

            # Publish perception completed event
            await self._publish_event(
                "perception_completed",
                user_id,
                {
                    "source": source,
                    "trace_id": trace.trace_id,
                    "event_count": len(raw_events),
                },
                trace_id=trace.trace_id,
            )

            return {
                "status": "completed",
                "source": source,
                "trace_id": trace.trace_id,
                "events": len(raw_events),
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

    async def _poll_connector(
        self, source: str, user_id: str, workspace_id: str
    ) -> tuple[list, str | None, str | None, str]:
        """Poll a connector for new events. Returns (events, new_cursor, error, cursor_type)."""
        from src.connectors.base import CONNECTOR_REGISTRY
        from src.services.oauth_manager import OAuthManager

        connector_cls = CONNECTOR_REGISTRY.get(source)
        if not connector_cls:
            return [], None, f"No connector registered for source: {source}", "opaque"

        connector = connector_cls(settings=self._settings)
        cursor_type = connector.cursor_type

        # Get OAuth credentials
        oauth_mgr = OAuthManager(
            self._db_factory,
            encryption_key=self._settings.oauth_encryption_key,
        )
        # Map source to OAuth provider (gmail/calendar share "google" provider)
        oauth_provider = "google" if source in ("gmail", "calendar") else source
        access_token = await oauth_mgr.get_valid_token(user_id, oauth_provider)
        if not access_token:
            return (
                [], None,
                f"No valid credentials for {source} — user may need to re-authorize",
                cursor_type,
            )

        # Get current cursor
        cursor = None
        async with self._db_factory() as db:
            from sqlalchemy import select

            from src.models.observation_cursor import ObservationCursor

            result = await db.execute(
                select(ObservationCursor.cursor_value).where(
                    ObservationCursor.user_id == user_id,
                    ObservationCursor.source == source,
                )
            )
            row = result.first()
            if row:
                cursor = row[0]

        try:
            events, new_cursor = await connector.poll(
                user_id, cursor, {"access_token": access_token}
            )
            return events, new_cursor, None, cursor_type
        except Exception as e:
            from src.integrations.mcp_errors import classify_error

            error_code = classify_error(e)
            logger.warning(
                "connector_poll_error",
                extra={"source": source, "error_code": error_code, "error": str(e)[:500]},
            )
            return [], None, f"Poll failed for {source} ({error_code}): {e}", cursor_type

    async def _ingest_raw_events(
        self, raw_events: list, user_id: str, workspace_id: str
    ) -> list[str]:
        """Ingest raw events into the event processor. Returns summary strings."""
        summaries = []
        async with self._db_factory() as db:
            from src.services.dead_letter import DeadLetterService
            from src.services.event_processor import EventProcessor

            event_bus = await self._ensure_event_bus()
            dead_letter = DeadLetterService(db)

            processor = EventProcessor(
                self._settings,
                db,
                world_model=self._services.world_model,
                memory_service=self._services.memory_service,
                dead_letter=dead_letter,
                event_bus=event_bus,
                notifier=self._services.notifier,
                planner=self._services.planner,
            )
            for raw in raw_events:
                try:
                    event_id = await processor.process(
                        raw, user_id=user_id, workspace_id=workspace_id,
                    )
                    title = raw.title or getattr(raw, "raw_data", {}).get("subject", "")
                    summary = f"[{raw.source}] {raw.event_type}: {title}"
                    if event_id:
                        summary += f" (event_id={event_id})"
                    summaries.append(summary)
                except Exception as e:
                    logger.warning(
                        "event_ingest_failed",
                        extra={
                            "source": raw.source,
                            "event_type": raw.event_type,
                            "error": str(e)[:500],
                        },
                    )
                    summaries.append(f"[{raw.source}] {raw.event_type} (ingest error)")
                    try:
                        await dead_letter.enqueue(
                            user_id=user_id,
                            operation_type="event_ingest",
                            error_type=type(e).__name__,
                            error_message=str(e),
                            source_id=raw.entity_id,
                            payload={
                                "source": raw.source,
                                "event_type": raw.event_type,
                                "entity_id": raw.entity_id,
                            },
                            workspace_id=workspace_id,
                        )
                    except Exception:
                        logger.debug("DLQ enqueue failed", exc_info=True)
            await db.commit()
        return summaries

    async def _update_cursor(
        self,
        source: str,
        user_id: str,
        workspace_id: str,
        new_cursor: str | None,
        cursor_type: str = "opaque",
    ) -> None:
        """Update the observation cursor after a successful poll."""
        if not new_cursor:
            return
        async with self._db_factory() as db:
            from datetime import datetime, timezone

            from sqlalchemy import select

            from src.models.observation_cursor import ObservationCursor

            result = await db.execute(
                select(ObservationCursor).where(
                    ObservationCursor.user_id == user_id,
                    ObservationCursor.source == source,
                )
            )
            existing = result.scalar_one_or_none()
            if existing:
                existing.cursor_value = new_cursor
                existing.last_observation_at = datetime.now(timezone.utc)
            else:
                from ulid import ULID

                db.add(
                    ObservationCursor(
                        cursor_id=f"cur_{ULID()}",
                        user_id=user_id,
                        workspace_id=workspace_id,
                        source=source,
                        cursor_type=cursor_type,
                        cursor_value=new_cursor,
                        last_observation_at=datetime.now(timezone.utc),
                    )
                )
            await db.commit()

    async def generate_briefing(self, user_id: str, workspace_id: str = "") -> dict:
        """Generate the daily briefing through the Presenter agent.

        Uses the get_briefing tool to fetch real data from the intelligence
        backend (events, plans, approvals, goals) and then formats it through
        the Presenter agent for user-facing delivery.
        """
        trace = self._trace_manager.start_trace("scheduled_briefing")
        try:
            # Step 1: Gather raw briefing data from intelligence server
            raw_data = await self._execute_tool(
                "get_briefing", {"date": "today"}, user_id=user_id, workspace_id=workspace_id
            )

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

    async def _ensure_event_bus(self):
        """Lazily initialize the event bus. Returns the bus or None on failure."""
        if self._event_bus is not None:
            return self._event_bus
        try:
            import redis.asyncio as aioredis

            from src.services.event_bus import EventBus

            self._event_bus_redis = aioredis.from_url(
                self._settings.redis_url, decode_responses=True
            )
            self._event_bus = EventBus(self._event_bus_redis)
        except Exception:
            logger.debug("Failed to init event_bus", exc_info=True)
        return self._event_bus

    async def _publish_event(
        self, event_type: str, user_id: str, payload: dict, trace_id: str | None = None
    ) -> None:
        """Publish an agent action event to the event bus (best-effort)."""
        try:
            event_bus = await self._ensure_event_bus()
            if event_bus is None:
                return

            stream = event_bus.agent_stream(user_id)
            metadata = {"trace_id": trace_id} if trace_id else {}
            await event_bus.publish(stream, event_type, payload, user_id, metadata)
        except Exception:
            logger.debug("Failed to publish event %s to bus", event_type, exc_info=True)

    async def _emit_runtime_event(
        self,
        event_type: str,
        *,
        workspace_id: str,
        user_id: str,
        run_id: str | None = None,
        step_id: str | None = None,
        payload: dict | None = None,
    ) -> None:
        """Emit a durable runtime event to DB + Redis (best-effort)."""
        try:
            async with self._db_factory() as db:
                from src.services.runtime_events import RuntimeEventEmitter

                emitter = RuntimeEventEmitter(db, workspace_id, self._event_bus)
                await emitter.emit(
                    event_type,
                    run_id=run_id,
                    step_id=step_id,
                    user_id=user_id,
                    payload=payload,
                )
                await db.commit()
        except Exception:
            logger.debug("Failed to emit runtime event %s", event_type, exc_info=True)

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

    async def _classify_intent(
        self,
        message: str,
        history_block: str = "",
    ) -> tuple[str, float]:
        """Classify user message intent using Haiku — fast and cheap.

        Returns (intent, confidence). Falls back to "command" on error.
        """
        classifier_input = message
        if history_block:
            classifier_input = f"{history_block}\n\nUser: {message}"

        if self._settings.use_bedrock:
            model = BEDROCK_MODEL_TIERS["haiku"]
        else:
            model = MODEL_TIERS["haiku"]

        try:
            response = await self._client.messages.create(
                model=model,
                max_tokens=100,
                temperature=0,
                system=[{"type": "text", "text": INTENT_CLASSIFIER_PROMPT}],
                messages=[{"role": "user", "content": classifier_input}],
            )

            text = "".join(b.text for b in response.content if b.type == "text")

            # Parse JSON from response
            if "{" in text:
                start = text.index("{")
                end = text.rindex("}") + 1
                parsed = json.loads(text[start:end])
                intent = parsed.get("intent", "command")
                confidence = float(parsed.get("confidence", 0.5))

                valid_intents = {
                    "greeting",
                    "chitchat",
                    "simple_question",
                    "data_fetch",
                    "status_query",
                    "approval_response",
                    "command",
                    "complex",
                }
                if intent not in valid_intents:
                    intent = "command"

                logger.info(
                    "intent_classified",
                    extra={
                        "intent": intent,
                        "confidence": confidence,
                        "message_preview": message[:80],
                    },
                )
                return intent, confidence

        except Exception as e:
            logger.warning("Intent classification failed, defaulting to command: %s", e)

        return "command", 0.5

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
        """Call a sub-agent (non-streaming). Returns final text response."""
        agent = self._agents.get(agent_name)
        if not agent:
            raise ValueError(f"Unknown agent: {agent_name}")

        model = self._get_model_for_agent(agent)
        tools = self._apply_cache_control_to_tools(self._get_tools_for_agent(agent))
        context_block = await self._assemble_context(agent_name, message, user_id=user_id)
        system_blocks = self._build_system_prompt(agent, context_block)

        text = ""
        async for evt in agent_loop(
            client=self._client,
            agent=agent,
            model=model,
            system_blocks=system_blocks,
            tools=tools,
            message=message,
            user_id=user_id,
            workspace_id=workspace_id,
            db_factory=self._db_factory,
            services=self._services,
            budget=self._budget,
            trace=trace,
            execute_tool_fn=self._execute_tool,
            max_tool_rounds=max_tool_rounds,
            stream=False,
        ):
            if isinstance(evt, LoopDone):
                text = evt.text
                logger.info(
                    "agent_call_complete",
                    extra={
                        "agent": agent_name,
                        "model": model,
                        "input_tokens": evt.input_tokens,
                        "output_tokens": evt.output_tokens,
                        "tools_called": evt.tools_called,
                        "latency_ms": evt.latency_ms,
                        "trace_id": trace.trace_id if trace else None,
                    },
                )

        return text

    async def _execute_tool(
        self, tool_name: str, tool_input: dict, user_id: str, workspace_id: str = ""
    ) -> dict:
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

        # Governor structured output: reporting tool returns input as-is
        if tool_name == "report_governor_verdict":
            return tool_input

        # Internal tools served via MCP protocol (in-process Client)
        internal_tools = {
            "ingest_event", "search_memory", "get_entities", "update_entity",
            "plan_command", "get_active_plans", "evaluate_policy", "approve_action",
            "get_briefing", "get_observation_cursor", "update_observation_cursor",
            "report_observation", "update_execution", "extract_preferences",
            "create_task", "get_task", "get_goals", "build_context", "verify_run",
        }

        # Inject workspace_id so tools always have it, even if the model omitted it
        if workspace_id and "workspace_id" not in tool_input:
            tool_input = {**tool_input, "workspace_id": workspace_id}

        # Emit tool.started event
        await self._publish_event("tool.started", user_id, {"tool": tool_name})

        # 1. Try internal tools via in-process MCP Client
        if tool_name in internal_tools:
            try:
                result = await self._call_internal_tool(
                    tool_name, {**tool_input, "user_id": user_id}
                )
                await self._publish_event("tool.completed", user_id, {"tool": tool_name})
                return result
            except Exception as e:
                logger.warning("Internal tool %s failed: %s", tool_name, e)
                await self._publish_event(
                    "tool.failed",
                    user_id,
                    {"tool": tool_name, "error": str(e)[:200]},
                )
                return {"error": f"Tool execution failed for {tool_name}: {e}"}

        # 2. Try native connector dispatch (gmail_*, calendar_*, etc.)
        native_result = await self._try_native_connector(tool_name, tool_input, user_id)
        if native_result is not None:
            await self._publish_event("tool.completed", user_id, {"tool": tool_name})
            return native_result

        # 3. Try capability resolver (routes to best backend: native, MCP official, user MCP)
        try:
            from src.connectors.mcp_bridge import get_session_pool
            from src.integrations.capability_resolver import CapabilityResolver

            async with self._db_factory() as db:
                session_pool = get_session_pool()
                resolver = CapabilityResolver(db, session_pool, workspace_id)
                capability = resolver.resolve_tool_to_capability(tool_name)
                if capability:
                    result = await resolver.execute(tool_name, tool_input, user_id=user_id)
                    await self._publish_event("tool.completed", user_id, {"tool": tool_name})
                    return result
        except Exception as e:
            logger.debug("Capability resolver failed for %s: %s", tool_name, e)

        # 3. Try MCP bridge directly (session pool, circuit-breaker protected)
        from src.connectors.mcp_bridge import call_mcp_tool, is_mcp_tool

        if is_mcp_tool(tool_name):
            try:
                result = await call_mcp_tool(
                    tool_name, tool_input, user_id=user_id, workspace_id=workspace_id,
                )
                await self._publish_event("tool.completed", user_id, {"tool": tool_name})
                return result
            except Exception as e:
                await self._publish_event(
                    "tool.failed",
                    user_id,
                    {"tool": tool_name, "error": str(e)[:200]},
                )
                raise

        # 4. Fall back to ToolRegistry for connector-backed tools
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

    # Native connector tool name → (connector_type, action) mapping
    _NATIVE_TOOL_MAP: dict[str, tuple[str, str]] = {
        "gmail_list_unread": ("gmail", "list_unread"),
        "gmail_get_message": ("gmail", "get_message"),
        "gmail_send_email": ("gmail", "send_email"),
        "gmail_create_draft": ("gmail", "create_draft"),
        "gmail_archive": ("gmail", "archive"),
        "gmail_mark_read": ("gmail", "mark_read"),
    }

    # Cached in-process MCP client for internal tools
    _internal_client = None
    _internal_client_ctx = None

    async def _call_internal_tool(self, tool_name: str, tool_input: dict) -> dict:
        """Call an internal tool via in-process FastMCP Client (MCP protocol).

        The composed server mounts intelligence tools under "intelligence_" namespace.
        We map flat tool names (e.g. "search_memory") to namespaced names
        (e.g. "intelligence_search_memory").
        """
        import json

        from fastmcp import Client

        from src.tools.server import jarvis_tools

        # Lazy-init: create and cache the in-process client
        if self._internal_client is None:
            self._internal_client_ctx = Client(jarvis_tools)
            self._internal_client = await self._internal_client_ctx.__aenter__()

        # Map flat name to namespaced name (intelligence_ prefix)
        namespaced = f"intelligence_{tool_name}"
        result = await self._internal_client.call_tool(namespaced, tool_input)

        # Extract result from CallToolResult
        if result.is_error:
            error_text = result.data if hasattr(result, "data") else str(result)
            return {"status": "error", "error": error_text}

        # Parse structured content if available
        if hasattr(result, "structured_content") and result.structured_content:
            return result.structured_content.get("result", result.structured_content)

        # Fallback: parse text content as JSON
        text = result.data if hasattr(result, "data") else str(result)
        if isinstance(text, str):
            try:
                return json.loads(text)
            except (json.JSONDecodeError, TypeError):
                return {"status": "ok", "result": text}
        return {"status": "ok", "result": text}

    async def _try_native_connector(
        self, tool_name: str, tool_input: dict, user_id: str
    ) -> dict | None:
        """Try to execute a tool via native connector. Returns None if not a native tool."""
        mapping = self._NATIVE_TOOL_MAP.get(tool_name)
        if not mapping:
            return None

        connector_type, action = mapping

        from src.connectors.base import CONNECTOR_REGISTRY

        connector_cls = CONNECTOR_REGISTRY.get(connector_type)
        if not connector_cls:
            return {"error": f"No native connector for: {connector_type}"}

        # Get OAuth credentials
        credentials = {}
        oauth = self._services.oauth_manager
        if oauth:
            try:
                provider_map = {
                    "gmail": "google",
                    "calendar": "google",
                    "drive": "google",
                    "github": "github",
                    "slack": "slack",
                }
                oauth_provider = provider_map.get(connector_type, connector_type)
                token = await oauth.get_valid_token(user_id, oauth_provider)
                if token:
                    credentials = {"access_token": token}
            except Exception:
                logger.warning("No credentials for native connector %s", connector_type)

        if not credentials:
            return {
                "error": f"No OAuth credentials for {connector_type}. "
                f"Please connect {connector_type} first."
            }

        connector = connector_cls(self._settings)
        try:
            return await connector.execute_action(action, tool_input, credentials)
        except Exception as e:
            logger.error("Native connector %s.%s failed: %s", connector_type, action, e)
            return {"error": f"{connector_type}.{action} failed: {e}"}

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
                    # Map connector type to OAuth provider (e.g. gmail→google)
                    provider_map = {
                        "gmail": "google",
                        "calendar": "google",
                        "drive": "google",
                        "github": "github",
                        "slack": "slack",
                        "linear": "linear",
                        "notion": "notion",
                        "jira": "jira",
                    }
                    oauth_provider = provider_map.get(connector_type, connector_type)
                    access_token = await oauth.get_valid_token(user_id, oauth_provider)
                    if access_token:
                        credentials = {"access_token": access_token}
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
                        procedure_library=svc.procedure_library,
                    artifact_store=svc.artifact_store,
                )

                async def get_credentials(connector_type: str) -> dict:
                    if svc.oauth_manager:
                        provider_map = {
                            "gmail": "google",
                            "calendar": "google",
                            "drive": "google",
                            "github": "github",
                            "slack": "slack",
                            "linear": "linear",
                            "notion": "notion",
                            "jira": "jira",
                        }
                        oauth_provider = provider_map.get(connector_type, connector_type)
                        token = await svc.oauth_manager.get_valid_token(user_id, oauth_provider)
                        if token:
                            return {"access_token": token}
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

    @staticmethod
    def _intent_to_decision(intent: str, message: str) -> PlannerOutput:
        """Synthesize a lightweight PlannerOutput from a fast intent classification."""
        intent_map = {
            "greeting": "acknowledge",
            "chitchat": "acknowledge",
            "simple_question": "answer_directly",
            "data_fetch": "read_source",
            "status_query": "answer_directly",
            "approval_response": "acknowledge",
        }
        return PlannerOutput(
            decision=intent_map.get(intent, "acknowledge"),
            reasoning=f"Fast-classified as {intent}",
            priority="low" if intent in ("greeting", "chitchat") else "medium",
            risk_level="none" if intent in ("greeting", "chitchat") else "low",
            execution_mode="auto_execute",
            goal=message[:200],
        )

    def _extract_decision(self, response_text: str) -> PlannerOutput:
        """Extract and validate structured decision from planner response."""
        raw: dict[str, Any] = {}
        try:
            if "{" in response_text:
                start = response_text.index("{")
                depth = 0
                for i, ch in enumerate(response_text[start:], start):
                    if ch == "{":
                        depth += 1
                    elif ch == "}":
                        depth -= 1
                        if depth == 0:
                            json_str = response_text[start : i + 1]
                            raw = json.loads(json_str)
                            break
        except (json.JSONDecodeError, ValueError):
            pass

        if not raw:
            raw = {"decision": "acknowledge", "reasoning": response_text[:500]}

        return PlannerOutput.model_validate(raw)

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
